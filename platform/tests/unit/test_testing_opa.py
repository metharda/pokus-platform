"""The tests' own policy engine: which binary it runs, and when a test skips or fails."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from potka_ci import policy
from potka_ci.contract import OPA_LOCK
from potka_testing import opa

ROOT = Path(__file__).resolve().parents[3]


def tree(root: Path) -> Path:
    """A working tree with the lock of the real one and no binary."""
    (root / OPA_LOCK).parent.mkdir(parents=True)
    shutil.copy(ROOT / OPA_LOCK, root / OPA_LOCK)
    return root


def ended(action: Callable[[], object]) -> BaseException:
    """How ACTION ended the test that called it: with a skip or with a failure.

    Caught here by its kind: a skip that leaves a test which expects a
    failure would only skip that test, and say nothing.
    """
    try:
        action()
    except (pytest.skip.Exception, pytest.fail.Exception) as outcome:
        return outcome
    raise AssertionError("the test was neither skipped nor failed")


def served() -> None:
    with opa.server():
        pass


def test_without_the_binary_a_test_that_needs_it_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(opa, "ROOT", tree(tmp_path))
    monkeypatch.setattr(opa, "USED", [])
    for action in (opa.binary, served):
        outcome = ended(action)
        assert isinstance(outcome, pytest.skip.Exception)
        assert "make opa" in str(outcome)
    assert opa.USED == []


@pytest.mark.parametrize("content", [b"", b"#!/bin/sh\necho opa\n"])
def test_a_binary_that_is_not_the_locked_one_fails_and_is_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    root = tree(tmp_path)
    other = root / policy.BINARY
    other.parent.mkdir(parents=True)
    other.write_bytes(content)
    other.chmod(0o755)
    monkeypatch.setattr(opa, "ROOT", root)
    monkeypatch.setattr(opa, "USED", [])
    # The server's start asks for the binary first, so it ends the same way, with nothing run.
    for action in (opa.binary, served):
        outcome = ended(action)
        assert isinstance(outcome, pytest.fail.Exception), "it was skipped"
        assert "is not the locked binary" in str(outcome)
    assert opa.USED == []


def test_a_directory_in_the_binarys_place_fails_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tree(tmp_path)
    (root / policy.BINARY).mkdir(parents=True)
    monkeypatch.setattr(opa, "ROOT", root)
    assert isinstance(ended(opa.binary), pytest.fail.Exception), "it was skipped"
