"""A derived image's overlay: files/ and install.sh, and nothing else."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

import pytest
from potka_core.catalog.overlay import Overlay, OverlayError, read_overlay


def overlay(root: Path) -> Path:
    (root / "files" / "etc" / "fixture").mkdir(parents=True)
    (root / "files" / "etc" / "fixture" / "job.conf").write_text("workers=1\n")
    (root / "files" / "usr" / "local" / "bin").mkdir(parents=True)
    (root / "files" / "usr" / "local" / "bin" / "job").write_text("#!/bin/sh\n")
    (root / "files" / "b.txt").write_text("b")
    (root / "files" / "a").mkdir()
    (root / "files" / "a" / "z.txt").write_text("z")
    (root / "files" / "empty").mkdir()
    (root / "install.sh").write_text("systemctl enable job.timer\n")
    return root


def problems(root: Path) -> list[tuple[str, str]]:
    with pytest.raises(OverlayError) as caught:
        read_overlay(root)
    assert str(caught.value) == "; ".join(
        f"{path}: {reason}" for path, reason in caught.value.problems
    )
    return list(caught.value.problems)


def test_an_overlay_lists_its_files(tmp_path: Path) -> None:
    root = overlay(tmp_path / "overlay")
    found = read_overlay(root)
    assert found == Overlay(
        root=root,
        files=("a/z.txt", "b.txt", "etc/fixture/job.conf", "usr/local/bin/job"),
        install=True,
    )
    assert found.paths() == (
        root / "files" / "a" / "z.txt",
        root / "files" / "b.txt",
        root / "files" / "etc" / "fixture" / "job.conf",
        root / "files" / "usr" / "local" / "bin" / "job",
        root / "install.sh",
    )
    assert all(path.is_file() for path in found.paths())


def test_an_overlay_of_files_only_or_of_an_install_step_only(tmp_path: Path) -> None:
    only_files = overlay(tmp_path / "one")
    (only_files / "install.sh").unlink()
    found = read_overlay(only_files)
    assert found.install is False
    assert found.paths() == tuple(only_files / "files" / name for name in found.files)
    only_install = tmp_path / "two"
    only_install.mkdir()
    (only_install / "install.sh").write_text("true\n")
    assert read_overlay(only_install) == Overlay(root=only_install, files=(), install=True)
    empty_files = tmp_path / "three"
    (empty_files / "files" / "etc").mkdir(parents=True)
    assert problems(empty_files) == [
        (".", "an overlay holds a file below files/, install.sh or both")
    ]


def test_what_is_not_an_overlay_directory_is_refused(tmp_path: Path) -> None:
    assert problems(tmp_path / "missing") == [(".", "cannot be read (FileNotFoundError)")]
    (tmp_path / "file").write_text("x")
    assert problems(tmp_path / "file") == [(".", "a regular file, not a directory")]
    real = overlay(tmp_path / "real")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    assert problems(tmp_path / "link") == [(".", "a symbolic link, not a directory")]
    (tmp_path / "empty").mkdir()
    assert problems(tmp_path / "empty") == [
        (".", "an overlay holds a file below files/, install.sh or both")
    ]


def test_every_other_entry_and_every_link_is_named(tmp_path: Path) -> None:
    root = overlay(tmp_path / "overlay")
    (root / "README.md").write_text("x")
    (root / ".git").mkdir()
    (root / "Files").mkdir()
    (root / "files" / "etc" / "link").symlink_to("/etc/passwd")
    (root / "files" / "dirlink").symlink_to(root / "files" / "a", target_is_directory=True)
    (root / "files" / "dangling").symlink_to("nowhere")
    os.mkfifo(root / "files" / "a" / "pipe")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(root / "files" / "sock"))
        assert problems(root) == [
            (".git", "an overlay holds only files/ and install.sh"),
            ("Files", "an overlay holds only files/ and install.sh"),
            ("README.md", "an overlay holds only files/ and install.sh"),
            ("files/a/pipe", "a special file, not a directory or a regular file"),
            ("files/dangling", "a symbolic link, not a directory or a regular file"),
            ("files/dirlink", "a symbolic link, not a directory or a regular file"),
            ("files/etc/link", "a symbolic link, not a directory or a regular file"),
            ("files/sock", "a special file, not a directory or a regular file"),
        ]


def test_files_and_install_sh_of_another_kind_are_refused(tmp_path: Path) -> None:
    one = tmp_path / "one"
    one.mkdir()
    (one / "files").write_text("x")
    (one / "install.sh").mkdir()
    assert problems(one) == [
        ("files", "a regular file, not a directory"),
        ("install.sh", "a directory, not a regular file"),
    ]
    real = overlay(tmp_path / "real")
    two = tmp_path / "two"
    two.mkdir()
    (two / "files").symlink_to(real / "files", target_is_directory=True)
    (two / "install.sh").symlink_to(real / "install.sh")
    assert problems(two) == [
        ("files", "a symbolic link, not a directory"),
        ("install.sh", "a symbolic link, not a regular file"),
    ]


def test_a_name_that_is_not_printable_utf8_is_refused(tmp_path: Path) -> None:
    root = overlay(tmp_path / "overlay")
    (root / "files" / "new\nline").write_text("x")
    (root / "files" / "tab\there").mkdir()
    with open(os.fsencode(root / "files") + b"/latin-\xe9", "w") as handle:
        handle.write("x")
    (root / "files" / "ünïcode ok.txt").write_text("x")
    assert problems(root) == [
        ("files/latin-?", "a name that is not printable UTF-8"),
        ("files/new?line", "a name that is not printable UTF-8"),
        ("files/tab?here", "a name that is not printable UTF-8"),
    ]
    for name in ("new\nline", "tab\there"):
        path = root / "files" / name
        path.rmdir() if path.is_dir() else path.unlink()
    os.unlink(os.fsencode(root / "files") + b"/latin-\xe9")
    assert "ünïcode ok.txt" in read_overlay(root).files


def test_a_directory_that_cannot_be_read_is_named_and_the_rest_is_still_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = overlay(tmp_path / "overlay")
    (root / "files" / "usr" / "link").symlink_to("/etc")
    scandir = os.scandir

    def refuse(path: Any) -> Any:
        if Path(path) == root / "files" / "etc":
            raise PermissionError(13, "Permission denied", str(path))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", refuse)
    assert problems(root) == [
        ("files/etc", "cannot be read (PermissionError)"),
        ("files/usr/link", "a symbolic link, not a directory or a regular file"),
    ]


def test_directories_nested_too_deeply_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = overlay(tmp_path / "overlay")
    monkeypatch.chdir(root / "files")
    for _ in range(1200):
        os.mkdir("d")
        os.chdir("d")
    monkeypatch.chdir(tmp_path)
    assert problems(root) == [("files", "directories nested too deeply")]


def test_an_entry_of_the_overlay_whose_name_cannot_be_printed(tmp_path: Path) -> None:
    root = overlay(tmp_path / "overlay")
    (root / "two\nlines").write_text("x")
    with open(os.fsencode(root) + b"/latin-\xe9", "w") as handle:
        handle.write("x")
    assert problems(root) == [
        ("latin-?", "an overlay holds only files/ and install.sh"),
        ("two?lines", "an overlay holds only files/ and install.sh"),
    ]
