"""Every skipped test is named; in CI only the listed ones may skip."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from potka_ci import skip_policy

CI_DIR = Path(__file__).resolve().parents[1]
TESTS = """
import pytest

def test_runs():
    pass

def test_listed():
    pytest.skip("listed reason")

def test_unlisted():
    pytest.skip("needs a GPU")

@pytest.fixture
def needs_a_server():
    pytest.skip("no server")

def test_skipped_in_setup(needs_a_server):
    pass

@pytest.mark.xfail(reason="known bug")
def test_expected_to_fail():
    assert False
"""
MODULE_SKIP = "import pytest\npytest.skip('module gone', allow_module_level=True)\n"
TEARDOWN_SKIP = """
import pytest

@pytest.fixture
def ends_with_a_skip():
    yield
    pytest.skip("skipped at the end")

def test_ends_with_a_skip(ends_with_a_skip):
    pass
"""


def run_pytest(
    tmp_path: Path, ci: bool, listed: tuple[str, ...] = (), module_skip: bool = True
) -> str:
    """Run pytest on the sample tests with the plugin and a list naming LISTED."""
    (tmp_path / "test_a.py").write_text(TESTS, encoding="utf-8")
    if module_skip:
        (tmp_path / "test_gone.py").write_text(MODULE_SKIP, encoding="utf-8")
    skips = [{"test": t, "run": "pytest", "reason": "a reason"} for t in listed]
    skips.append({"test": "test_a.py::test_unlisted", "run": "py39", "reason": "another run"})
    path = tmp_path / "skips.json"
    path.write_text(json.dumps({"skips": skips}), encoding="utf-8")
    return run_plain(tmp_path, path, ci)


def run_plain(tmp_path: Path, path: Path, ci: bool) -> str:
    """Run pytest on TMP_PATH's tests with the plugin and the list at PATH."""
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env["PYTHONPATH"] = str(CI_DIR)
    if ci:
        env["CI"] = "true"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "potka_ci.skip_policy",
            "-c",
            os.devnull,
            "--rootdir",
            str(tmp_path),
            "-o",
            f"ci_skip_list={path}",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return f"exit {done.returncode}\n{done.stdout}{done.stderr}"


def test_outside_ci_every_skip_passes_and_is_named(tmp_path: Path) -> None:
    out = run_pytest(tmp_path, ci=False)
    assert out.startswith("exit 0\n"), out
    for line in [
        "skipped test: test_a.py::test_listed (reason: listed reason)",
        "skipped test: test_a.py::test_unlisted (reason: needs a GPU)",
        "skipped test: test_a.py::test_skipped_in_setup (reason: no server)",
        "skipped test: test_a.py::test_expected_to_fail (reason: expected to fail: known bug)",
        "skipped test: test_gone.py (reason: module gone)",
    ]:
        assert line in out.splitlines(), out


def test_in_ci_a_skip_the_list_does_not_name_fails(tmp_path: Path) -> None:
    out = run_pytest(tmp_path, ci=True, listed=("test_a.py::test_listed",), module_skip=False)
    assert out.startswith("exit 1\n"), out
    for test, reason in [
        ("test_a.py::test_unlisted", "needs a GPU"),
        ("test_a.py::test_skipped_in_setup", "no server"),
        ("test_a.py::test_expected_to_fail", "expected to fail: known bug"),
    ]:
        assert skip_policy.refusal(test, reason) in out, out
    assert "skipped test: test_a.py::test_listed (reason: listed reason)" in out.splitlines()
    assert "test_a.py::test_listed skipped in CI" not in out


def test_in_ci_an_unlisted_xfail_alone_fails_the_run(tmp_path: Path) -> None:
    listed = (
        "test_a.py::test_listed",
        "test_a.py::test_unlisted",
        "test_a.py::test_skipped_in_setup",
    )
    out = run_pytest(tmp_path, ci=True, listed=listed, module_skip=False)
    assert out.startswith("exit 1\n"), out
    assert (
        skip_policy.refusal("test_a.py::test_expected_to_fail", "expected to fail: known bug")
        in out
    )


def test_in_ci_a_skip_in_teardown_fails(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text(TEARDOWN_SKIP, encoding="utf-8")
    path = tmp_path / "skips.json"
    path.write_text('{"skips": []}', encoding="utf-8")
    out = run_plain(tmp_path, path, ci=True)
    assert out.startswith("exit 1\n"), out
    assert skip_policy.refusal("test_a.py::test_ends_with_a_skip", "skipped at the end") in out


@pytest.mark.parametrize("content", [None, "not json"], ids=["missing", "not-json"])
def test_a_missing_or_unreadable_list_stops_the_run(tmp_path: Path, content: str | None) -> None:
    (tmp_path / "test_a.py").write_text("def test_runs():\n    pass\n", encoding="utf-8")
    path = tmp_path / "skips.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    out = run_plain(tmp_path, path, ci=False)
    assert not out.startswith("exit 0\n"), out
    assert "1 passed" not in out


def test_in_ci_an_unlisted_module_skip_fails_the_collection(tmp_path: Path) -> None:
    out = run_pytest(tmp_path, ci=True, listed=("test_a.py::test_listed",))
    assert out.startswith("exit 2\n"), out
    assert skip_policy.refusal("test_gone.py", "module gone") in out, out


def test_in_ci_the_listed_skips_pass(tmp_path: Path) -> None:
    listed = (
        "test_a.py::test_listed",
        "test_a.py::test_unlisted",
        "test_a.py::test_skipped_in_setup",
        "test_a.py::test_expected_to_fail",
        "test_gone.py",
    )
    out = run_pytest(tmp_path, ci=True, listed=listed)
    assert out.startswith("exit 0\n"), out
    assert "1 passed, 4 skipped, 1 xfailed" in out


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"skips": {}},
        {"skips": [{"test": "t", "run": "pytest"}]},
        {"skips": [{"test": "t", "run": "pytest", "reason": "  "}]},
        {"skips": ["t"]},
    ],
    ids=["not-an-object", "not-an-array", "no-reason", "blank-reason", "not-an-entry"],
)
def test_a_malformed_list_is_refused(tmp_path: Path, data: object) -> None:
    path = tmp_path / "skips.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=r"skips\.json: "):
        skip_policy.allowed("pytest", path)


def test_the_repository_s_list_is_empty_and_valid() -> None:
    assert skip_policy.allowed("pytest") == {}
    assert json.loads(skip_policy.LIST.read_text(encoding="utf-8"))["skips"] == []


def test_ci_is_read_from_the_ci_variable_and_reasons_stay_on_one_line() -> None:
    assert skip_policy.in_ci({"CI": "true"})
    assert not skip_policy.in_ci({"CI": "1"})
    assert not skip_policy.in_ci({})
    assert skip_policy.line("t", "a\n  b") == "skipped test: t (reason: a b)"
