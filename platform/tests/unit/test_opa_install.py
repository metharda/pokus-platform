"""The installer of the policy engine: only the locked file, and only after its sha256 was compared.

The script (``tests/opa/install.sh``, ``make opa``) is run here in a tree of
its own, with a lock whose URLs are files of the test: nothing is fetched
from the network, and the real ``.opa/`` is not touched. The files it is
given to install are scripts that leave a mark when they are run; after
every test there is none.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parents[2]
SCRIPT = PLATFORM / "tests" / "opa" / "install.sh"
MACHINE = platform.machine()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Tree:
    """A platform directory with the installer, and a place its lock's URLs point to."""

    def __init__(self, root: Path) -> None:
        self.mark = root / "ran"
        # What is installed, and another file: each would leave the mark if it were run.
        self.binary = f"#!/bin/sh\ntouch {self.mark}\necho the locked binary\n".encode()
        self.other = f"#!/bin/sh\ntouch {self.mark}\necho another file\n".encode()
        self.script = root / "platform" / "tests" / "opa" / "install.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copy(SCRIPT, self.script)
        self.lock = self.script.with_name("opa.lock")
        self.served = root / "served"
        self.served.mkdir()
        self.installed = root / "platform" / ".opa" / "opa"

    def serve(self, name: str, content: bytes) -> str:
        (self.served / name).write_bytes(content)
        return (self.served / name).as_uri()

    def locked(self, *lines: str) -> None:
        self.lock.write_text("# A lock of the test.\n" + "".join(f"{line}\n" for line in lines))

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["/bin/bash", str(self.script)],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.served)},
        )

    def left(self) -> list[str]:
        """What the installer's directory holds."""
        directory = self.installed.parent
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


@pytest.fixture
def tree(tmp_path: Path) -> Iterator[Tree]:
    made = Tree(tmp_path)
    yield made
    # Whatever the installer did with a file, it never ran it.
    assert not made.mark.exists()


def test_the_files_of_these_tests_leave_a_mark_when_they_are_run(tmp_path: Path) -> None:
    made = Tree(tmp_path)
    for content in (made.binary, made.other):
        program = tmp_path / "program"
        program.write_bytes(content)
        program.chmod(0o755)
        subprocess.run([str(program)], check=True, capture_output=True)  # noqa: S603
        assert made.mark.exists()
        made.mark.unlink()


def test_the_locked_file_is_installed_and_made_executable(tree: Tree) -> None:
    url = tree.serve("opa", tree.binary)
    tree.locked(
        f"9.9.9 other {url}.other {'0' * 64}", f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}"
    )
    done = tree.run()
    assert (done.returncode, done.stderr) == (0, "")
    assert (
        done.stdout == f"opa 9.9.9 ({MACHINE}), sha256 {sha256(tree.binary)}, in {tree.installed}\n"
    )
    assert tree.installed.read_bytes() == tree.binary
    assert tree.installed.stat().st_mode & stat.S_IXUSR
    assert tree.left() == ["opa"]


def test_a_file_with_another_sha256_is_removed_and_nothing_is_installed(tree: Tree) -> None:
    url = tree.serve("opa", tree.other)
    tree.locked(f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}")
    done = tree.run()
    assert done.returncode == 1
    assert done.stderr == f"opa: {url} does not have the sha256 {sha256(tree.binary)}\n"
    # Neither the file nor what was downloaded is left, executable or not.
    assert tree.left() == []


def test_a_file_that_is_there_and_is_not_the_locked_one_is_replaced(tree: Tree) -> None:
    url = tree.serve("opa", tree.binary)
    tree.locked(f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}")
    tree.installed.parent.mkdir(parents=True)
    tree.installed.write_bytes(tree.other)
    tree.installed.chmod(0o755)
    assert tree.run().returncode == 0
    assert tree.installed.read_bytes() == tree.binary
    assert tree.left() == ["opa"]


def test_a_wrong_file_that_cannot_be_replaced_does_not_stay(tree: Tree) -> None:
    url = tree.serve("opa", tree.other)
    tree.locked(f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}")
    tree.installed.parent.mkdir(parents=True)
    tree.installed.write_bytes(tree.other)
    tree.installed.chmod(0o755)
    assert tree.run().returncode == 1
    assert tree.left() == []


def test_the_locked_file_that_is_there_is_kept_and_nothing_is_fetched(tree: Tree) -> None:
    url = tree.serve("opa", tree.binary)
    tree.locked(f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}")
    assert tree.run().returncode == 0
    (tree.served / "opa").unlink()
    done = tree.run()
    assert (done.returncode, done.stderr) == (0, "")
    assert tree.installed.read_bytes() == tree.binary


def test_a_download_that_fails_installs_nothing(tree: Tree) -> None:
    url = (tree.served / "absent").as_uri()
    tree.locked(f"9.9.9 {MACHINE} {url} {sha256(tree.binary)}")
    done = tree.run()
    assert done.returncode != 0
    assert "opa" not in tree.left()
    assert not [
        name for name in tree.left() if (tree.installed.parent / name).stat().st_mode & 0o111
    ]


@pytest.mark.parametrize(
    ("lines", "count"),
    [
        (["9.9.9 other {url} {right}"], 0),
        (["9.9.9 {machine} {url} {right}", "9.9.9 {machine} {url} {wrong}"], 2),
        (["9.9.9 {machine} {url} {wrong}", "9.9.9 {machine} {url} {right}"], 2),
        (["9.9.9 {machine}"], 1),
        ([], 0),
    ],
    ids=["another-machine", "this-machine-twice", "twice-the-right-one-last", "no-url", "empty"],
)
def test_a_lock_without_exactly_one_line_for_this_machine_installs_nothing(
    tree: Tree, lines: list[str], count: int
) -> None:
    said = {
        "url": tree.serve("opa", tree.binary),
        "machine": MACHINE,
        "right": sha256(tree.binary),
        "wrong": sha256(tree.other),
    }
    tree.locked(*[line.format(**said) for line in lines])
    done = tree.run()
    assert done.returncode == 2
    assert done.stderr == f"opa: opa.lock has {count} lines for {MACHINE}, not one with a URL\n"
    assert tree.left() == []


def test_a_line_without_its_sha256_installs_nothing(tree: Tree) -> None:
    url = tree.serve("opa", tree.binary)
    tree.locked(f"9.9.9 {MACHINE} {url}")
    done = tree.run()
    assert done.returncode == 1
    assert tree.left() == []
