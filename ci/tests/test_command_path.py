from __future__ import annotations

from pathlib import Path

import pytest
from potka_core import shells

from potka_ci import command_path

ROOT = Path(__file__).resolve().parents[2]
MAIN = "platform/packages/potka-executor/src/potka_executor/main.py"
CLEAN = '''"""A module that makes commands as lists and reads nothing as a shell would."""
import os
import os.path
from os import environ, path

SHELLS = {"sh", "bash"}
WORDS = "sh -c is what this never runs; shell=True neither"
COMMAND = ["systemctl", "restart", "nginx.service"]
OTHER = ("runuser", "--user=postgres", "--", "psql", "-c", "select 1")
NAMES = ["web", "shell", "/etc/bash.bashrc"]


def run(client, name, line):
    shell = os.environ.get("SHELL")  # read, never run
    words = line.split() + line.split("/") + WORDS.rsplit(";", 1)
    return client.exec(name, [*COMMAND, path.basename(shell or ""), *words])
'''


def tree(root: Path, source: str, package: str = "potka-executor") -> Path:
    module = root / "platform" / "packages" / package / "src" / package.replace("-", "_")
    module.mkdir(parents=True)
    (module / "main.py").write_text(source, encoding="utf-8")
    return root


def test_the_repository_s_command_path_holds_no_shell() -> None:
    outcome = command_path.run(ROOT)
    assert outcome.status == "passed"
    # Every .py file of the six packages, counted here on its own.
    packages = ROOT / "platform" / "packages"
    there = [name for name in command_path.PACKAGES if (packages / name).is_dir()]
    files = sum(1 for name in there for _ in (packages / name / "src").rglob("*.py"))
    assert files >= 25
    assert outcome.lines == [f"{files} file(s) of 6 package(s) on the command path: no shell"]
    # The check reads the packages that make, check and carry a command, and every
    # package of the release is on one of its two lists.
    assert command_path.PACKAGES == (
        "potka-executor",
        "potka-incus",
        "potka-injector",
        "potka-primitives",
        "potka-tools",
        "potka-verifier",
    )
    known = {*command_path.PACKAGES, *command_path.OTHERS}
    assert {entry.name for entry in packages.iterdir() if entry.is_dir()} == known
    assert not set(command_path.PACKAGES) & set(command_path.OTHERS)
    # The check imports no package of the platform: its list of shells is a copy of theirs.
    assert list(command_path.SHELLS) == sorted(shells.SHELLS)


def test_commands_as_lists_and_words_about_shells_are_no_findings(tmp_path: Path) -> None:
    outcome = command_path.run(tree(tmp_path, CLEAN))
    assert (outcome.status, outcome.lines) == (
        "passed",
        [
            "1 file(s) of 1 package(s) on the command path: no shell (not there: potka-incus,"
            " potka-injector, potka-primitives, potka-tools, potka-verifier)"
        ],
    )


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import subprocess\n", ["1: imports subprocess"]),
        ("import os, subprocess as sp\n", ["1: imports subprocess"]),
        ("from subprocess import run\n", ["1: imports subprocess"]),
        ("import subprocess.run\n", ["1: imports subprocess"]),
        ("def late():\n    import shlex\n", ["2: imports shlex"]),
        (
            "import pty\nimport pexpect\nimport commands\n",
            ["1: imports pty", "2: imports pexpect", "3: imports commands"],
        ),
        ("from os import system\n", ["1: imports os.system"]),
        ("from os import path, popen\n", ["1: imports os.popen"]),
        ("from os import execv\n", ["1: imports os.execv"]),
        (
            "from os import spawnlp, posix_spawnp\n",
            ["1: imports os.posix_spawnp", "1: imports os.spawnlp"],
        ),
        ("import asyncio.subprocess\n", ["1: imports asyncio.subprocess"]),
        ("from asyncio import subprocess\n", ["1: imports asyncio.subprocess"]),
        ("from asyncio.subprocess import PIPE\n", ["1: imports asyncio.subprocess"]),
        (
            "from asyncio import create_subprocess_shell as start\n",
            ["1: imports asyncio.create_subprocess_shell"],
        ),
        (
            "import asyncio\n\n\nasync def run(line):\n"
            "    return await asyncio.create_subprocess_shell(line)\n",
            ["5: uses create_subprocess_shell"],
        ),
        (
            "import asyncio\n\n\nasync def run(*argv):\n"
            "    return await asyncio.create_subprocess_exec(*argv)\n",
            ["5: uses create_subprocess_exec"],
        ),
        ("import asyncio\nPIPE = asyncio.subprocess.PIPE\n", ["2: uses asyncio.subprocess"]),
        ("loop.subprocess_shell(factory, line)\n", ["1: uses subprocess_shell"]),
        ("loop.subprocess_exec(factory, 'ls')\n", ["1: uses subprocess_exec"]),
        ("import os\nos.system('ls')\n", ["2: uses os.system"]),
        ("import os\nrun = os.popen\n", ["2: uses os.popen"]),
        ("import os\nos.execvp('ls', ['ls'])\n", ["2: uses os.execvp"]),
        ("import os\nos.spawnlp(0, 'ls')\n", ["2: uses os.spawnlp"]),
        ("import os\nos.posix_spawn('/bin/ls', [], {})\n", ["2: uses os.posix_spawn"]),
        ("import os\nos.startfile('a')\n", ["2: uses os.startfile"]),
        ("run(['ls'], shell=True)\n", ["1: a call with shell="]),
        ("run('ls', shell=False)\n", ["1: a call with shell="]),
        ("client.exec(name, ['sh', '-c', line])\n", ["1: a list that names a shell (sh)"]),
        ("ARGV = ('/bin/bash', script)\n", ["1: a list that names a shell (bash)"]),
        (
            "argv = ['busybox', 'sh']\n",
            ["1: a list that names a shell (busybox)", "1: a list that names a shell (sh)"],
        ),
        # A shell behind another program is a shell.
        (
            "client.exec(name, ['timeout', '5s', 'sh', '-c', line])\n",
            ["1: a list that names a shell (sh)"],
        ),
        ("argv = ['/usr/bin/env', 'bash', '-c', line]\n", ["1: a list that names a shell (bash)"]),
        (
            "argv = ('runuser', '--user=postgres', '--', 'rbash', '-c', q)\n",
            ["1: a list that names a shell (rbash)"],
        ),
        ("NAMES = ['web', 'sh']\n", ["1: a list that names a shell (sh)"]),
        # A command written as a line and split.
        ("client.exec(name, \"sh -c 'nginx -t'\".split())\n", ["1: a string split into a command"]),
        (
            "client.exec(name, f'systemctl restart {unit}'.split())\n",
            ["1: a string split into a command"],
        ),
        (
            "argv = 'systemctl restart {}'.format(unit).split(' ')\n",
            ["1: a string split into a command"],
        ),
        ("argv = ('systemctl restart %s' % unit).split()\n", ["1: a string split into a command"]),
        (
            "import subprocess\n\nsubprocess.run(['dash', '-c', x], shell=True)\n",
            [
                "1: imports subprocess",
                "3: a call with shell=",
                "3: a list that names a shell (dash)",
            ],
        ),
    ],
)
def test_what_could_run_a_shell_is_found_with_its_line(
    tmp_path: Path, source: str, found: list[str]
) -> None:
    assert [f"{line}: {what}" for line, what in command_path.source_findings(source)] == found
    outcome = command_path.run(tree(tmp_path, source, "potka-tools"))
    assert outcome.status == "failed"
    shown = "platform/packages/potka-tools/src/potka_tools/main.py"
    assert outcome.lines == [f"{shown}:{line}" for line in found]


def test_a_package_on_neither_list_fails_until_it_is_put_on_one(tmp_path: Path) -> None:
    root = tree(tmp_path, CLEAN)
    packages = root / "platform" / "packages"
    (packages / "potka-cli").mkdir()
    (packages / "README.md").write_text("no package\n", encoding="utf-8")
    assert command_path.run(root).status == "passed"
    (packages / "potka-runner" / "src").mkdir(parents=True)
    outcome = command_path.run(root)
    assert (outcome.status, outcome.lines) == (
        "failed",
        [
            "platform/packages/potka-runner: in neither list of the command path check"
            " (PACKAGES: it makes, checks or carries a command; OTHERS: it does not)"
        ],
    )


def test_what_is_no_command_is_no_finding() -> None:
    for source in (
        "from . import subprocess\n",
        "from .shlex import words\n",
        "import asyncio\nfrom asyncio import gather, run\n",
        "parts = line.split()\nwords = text.split('/')\n",
        "words = WORDS.split()\n",
        "SHELLS = {'sh', 'bash'}\nFROZEN = frozenset({'dash'})\n",
        "text = 'sh -c is prose here'\n",
        "names = ['web', 'shell', '/etc/bash.bashrc', 'bin/shx']\n",
    ):
        assert command_path.source_findings(source) == [], source


def test_a_file_that_is_no_python_fails_and_so_does_a_tree_without_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = command_path.run(tree(tmp_path / "broken", "def (:\n"))
    assert (outcome.status, outcome.lines) == (
        "failed",
        [f"{MAIN}: not Python the check can read"],
    )
    binary = tree(tmp_path / "binary", "")
    (binary / "platform/packages/potka-executor/src/potka_executor/main.py").write_bytes(
        b"\xff\xfe"
    )
    assert command_path.run(binary).status == "failed"
    # A file that cannot be read fails with its name, and the others are still read.
    locked = tree(tmp_path / "locked", "import subprocess\n")
    main = locked / "platform/packages/potka-executor/src/potka_executor/main.py"
    (main.parent / "other.py").write_text("import shlex\n", encoding="utf-8")
    read = Path.read_text

    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        if self == main:
            raise PermissionError(13, "Permission denied")
        return read(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", refuse)
    assert command_path.run(locked).lines == [
        f"{MAIN}: cannot be read (PermissionError)",
        f"{MAIN.replace('main.py', 'other.py')}:1: imports shlex",
    ]
    monkeypatch.undo()
    # Sources of another package are not the command path's; without any, nothing was checked.
    outcome = command_path.run(tree(tmp_path / "other", "import subprocess\n", "potka-cli"))
    assert (outcome.status, outcome.lines[0]) == (
        "failed",
        "no source of the command path's packages was found",
    )
