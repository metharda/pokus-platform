"""Commands that need root: run directly as root, else in CI through passwordless sudo.

The local gate may run as root; CI's runner is not root but may use
``sudo -n``. A test that needs root for part of its work builds that
command with the ``as_root`` fixture, so it runs in both places. Outside CI
sudo is never used: a test that is not root there skips, and the skip is
named (in CI an unlisted skip fails).
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence

import pytest

SKIP_REASON = "needs root, or in CI passwordless sudo (sudo -n)"


@functools.cache
def sudo_path() -> str | None:
    """The absolute path of sudo when ``sudo -n true`` succeeds (as on CI's runner).

    Returns:
        The path, or None when there is no sudo or it asks for a password.
    """
    sudo = shutil.which("sudo")
    if sudo is None:
        return None
    done = subprocess.run(  # noqa: S603 (a fixed command)
        [sudo, "-n", "true"], capture_output=True, check=False, timeout=60
    )
    return sudo if done.returncode == 0 else None


def root_argv(
    argv: Sequence[str],
    euid: int | None = None,
    sudo: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """ARGV as a command that runs as root.

    Args:
        argv: The command; its program an absolute path (sudo resets PATH).
        euid: The effective UID (default: this process's).
        sudo: sudo's path when passwordless sudo works (default: :func:`sudo_path`,
            asked only in CI).
        environ: The environment, for ``CI`` (default: this process's).

    Returns:
        ARGV itself when running as root, else ``<sudo> -n -- ARGV`` in CI.

    Raises:
        ValueError: The program is not an absolute path.
        pytest.skip.Exception: Neither root nor, in CI, passwordless sudo.
    """
    if not os.path.isabs(argv[0]):
        raise ValueError(f"not an absolute program path: {argv[0]}")
    if (os.geteuid() if euid is None else euid) == 0:
        return list(argv)
    if (os.environ if environ is None else environ).get("CI") == "true":
        sudo = sudo or sudo_path()
        if sudo is not None:
            return [sudo, "-n", "--", *argv]
    pytest.skip(SKIP_REASON)


@pytest.fixture
def as_root() -> Callable[[Sequence[str]], list[str]]:
    """A function that turns a command into one that runs as root (:func:`root_argv`)."""
    return root_argv
