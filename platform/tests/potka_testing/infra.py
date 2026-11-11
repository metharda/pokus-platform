"""The pokus-infrastructure checkout at INFRA_PIN, for tests that compare with its files.

``POTKA_INFRA`` names the checkout (CI sets it, and ``make test`` and the
gate set it from ``INFRA``); else it is the directory
``pokus-infrastructure`` next to this repository. A checkout at another
commit than ``INFRA_PIN``, or with local changes, fails the test; a missing
one skips it (and so fails CI).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
GOLDEN = Path("tools") / "platform" / "tests" / "golden"


def at_pin(path: Path) -> Path:
    """PATH, a checkout of pokus-infrastructure at INFRA_PIN without local changes.

    Args:
        path: The checkout.

    Returns:
        PATH.
    """
    pin = (REPOSITORY / "INFRA_PIN").read_text(encoding="utf-8").strip()
    head = subprocess.run(  # noqa: S603 (git, fixed arguments)
        ["git", "-C", str(path), "rev-parse", "HEAD"],  # noqa: S607 (git from PATH)
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != pin:
        pytest.fail(f"the pokus-infrastructure checkout is at {head}, not at INFRA_PIN {pin}")
    changes = subprocess.run(  # noqa: S603 (git, fixed arguments)
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if changes:
        pytest.fail("the pokus-infrastructure checkout has local changes")
    return path


@pytest.fixture(scope="session")
def infra_checkout() -> Path:
    """The checkout of pokus-infrastructure at INFRA_PIN (the module's docstring)."""
    path = Path(os.environ.get("POTKA_INFRA") or REPOSITORY.parent / "pokus-infrastructure")
    path = path.resolve()
    if not (path / ".git").exists():
        pytest.skip(f"no pokus-infrastructure checkout at {path} (POTKA_INFRA)")
    return at_pin(path)
