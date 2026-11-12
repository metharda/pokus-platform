"""Commands that need root run as root, in CI through sudo -n, or skip with their reason."""

from __future__ import annotations

from pathlib import Path

import pytest
from potka_testing import root
from potka_testing.root import root_argv, sudo_path

CI = {"CI": "true"}


def test_as_root_the_command_is_unchanged() -> None:
    assert root_argv(["/usr/bin/id", "-u"], euid=0, environ={}) == ["/usr/bin/id", "-u"]


def test_in_ci_it_runs_through_passwordless_sudo_by_its_path() -> None:
    argv = root_argv(["/usr/bin/id"], euid=1000, sudo="/usr/bin/sudo", environ=CI)
    assert argv == ["/usr/bin/sudo", "-n", "--", "/usr/bin/id"]


def test_outside_ci_sudo_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root, "sudo_path", lambda: pytest.fail("sudo asked outside CI"))
    with pytest.raises(pytest.skip.Exception, match=r"^needs root, or in CI passwordless sudo"):
        root_argv(["/usr/bin/id"], euid=1000, environ={})


def test_without_root_or_sudo_the_test_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root, "sudo_path", lambda: None)
    with pytest.raises(pytest.skip.Exception):
        root_argv(["/usr/bin/id"], euid=1000, environ=CI)


def test_the_program_must_be_an_absolute_path() -> None:
    with pytest.raises(ValueError, match=r"^not an absolute program path: id$"):
        root_argv(["id"], euid=0, environ={})


@pytest.mark.parametrize(("status", "found"), [(0, True), (1, False)])
def test_sudo_counts_only_when_it_asks_for_no_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, found: bool
) -> None:
    sudo = tmp_path / "sudo"
    sudo.write_text(f'#!/bin/sh\n[ "$1 $2" = "-n true" ] && exit {status}\nexit 9\n')
    sudo.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    sudo_path.cache_clear()
    try:
        assert sudo_path() == (str(sudo) if found else None)
    finally:
        sudo_path.cache_clear()


def test_without_sudo_there_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    sudo_path.cache_clear()
    try:
        assert sudo_path() is None
    finally:
        sudo_path.cache_clear()
