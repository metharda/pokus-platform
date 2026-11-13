from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Repo

from potka_ci import build, commits, contract, gate, generated, privacy, references, todos
from potka_ci.outcome import Outcome


def fixed(outcome: Outcome) -> object:
    return lambda *args, **kwargs: outcome


TOOL_ENVS: list[dict[str, str] | None] = []
GENERATED_INFRA: list[Path | None] = []


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    def tool(name: str, argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> Outcome:
        if name == "pytest":
            TOOL_ENVS.append(env)
            return Outcome("passed", ["...", "12 passed in 0.10s"])
        return Outcome("passed", [f"{name} ok"])

    monkeypatch.setattr(gate, "tool", tool)

    def generated_run(root: Path, infra: Path | None = None) -> Outcome:
        GENERATED_INFRA.append(infra)
        return Outcome("passed", ["4 generated file(s)"])

    monkeypatch.setattr(generated, "run", generated_run)
    monkeypatch.setattr(contract, "run", fixed(Outcome("skipped", ["no checkout"])))
    monkeypatch.setattr(build, "run", fixed(Outcome("skipped", ["no checkout"])))
    monkeypatch.setattr(privacy, "run", fixed(Outcome("warning", ["no denylist", "privacy: 3"])))
    monkeypatch.setattr(references, "run", fixed(Outcome("failed", ["a.md:1:1: plan-step"])))
    monkeypatch.setattr(commits, "run", fixed(Outcome("passed", ["commits: 1"])))
    monkeypatch.setattr(todos, "run", fixed(Outcome("passed", ["none"])))
    return monkeypatch


def test_the_summary_line_and_what_follows_it(repo: Repo, steps: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    ok = gate.run(repo.path, None, printer=lines.append)
    assert ok is False
    assert lines == [
        "gate: 7 passed, 1 failed, 4 skipped (actionlint, shellcheck, contract, build),"
        " 1 warnings (13 steps; pytest: 12 passed)",
        "skipped: actionlint: no .github/workflows/ci.yml",
        "skipped: shellcheck: no shell script (tests/*/*.sh)",
        "skipped: contract: no checkout",
        "skipped: build: no checkout",
        "warning: privacy: no denylist",
        "warning: privacy: privacy: 3",
        "--- references: failed",
        "a.md:1:1: plan-step",
    ]


def test_the_tests_get_the_infrastructure_checkout(
    repo: Repo, steps: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    TOOL_ENVS.clear()
    GENERATED_INFRA.clear()
    gate.run(repo.path, None, printer=lambda line: None)
    gate.run(repo.path, tmp_path / "infra", printer=lambda line: None)
    assert TOOL_ENVS == [None, {"POTKA_INFRA": str((tmp_path / "infra").resolve())}]
    assert GENERATED_INFRA == [None, tmp_path / "infra"]


def test_the_generated_step_is_skipped_or_fails_as_its_check_says(
    repo: Repo, steps: pytest.MonkeyPatch
) -> None:
    not_compared = "4 generated file(s) match; the table was not compared: no checkout"
    steps.setattr(generated, "run", fixed(Outcome("skipped", [not_compared])))
    lines: list[str] = []
    assert gate.run(repo.path, None, printer=lines.append) is False
    assert lines[0] == (
        "gate: 6 passed, 1 failed, 5 skipped (actionlint, shellcheck, generated, contract, build),"
        " 1 warnings (13 steps; pytest: 12 passed)"
    )
    assert f"skipped: generated: {not_compared}" in lines
    steps.setattr(generated, "run", fixed(Outcome("failed", ["a.schema.json: differs"])))
    lines.clear()
    assert gate.run(repo.path, None, printer=lines.append) is False
    assert lines[0].startswith("gate: 6 passed, 2 failed, 4 skipped (actionlint, shellcheck,")
    assert lines[lines.index("--- generated: failed") + 1] == "a.schema.json: differs"


def test_verbose_prints_every_step(repo: Repo, steps: pytest.MonkeyPatch) -> None:
    lines: list[str] = []
    gate.run(repo.path, None, verbose=True, printer=lines.append)
    assert "--- ruff: passed" in lines
    assert "ruff ok" in lines
    assert "--- commits: passed" in lines
    assert lines[lines.index("--- generated: passed") + 1] == "4 generated file(s)"


def test_a_missing_tool_fails(tmp_path: Path) -> None:
    outcome = gate.tool("no-such-tool-here", [], tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == ["no-such-tool-here is not installed: run make setup"]


def test_a_step_that_raises_fails_and_the_gate_goes_on(
    repo: Repo, steps: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kwargs: object) -> Outcome:
        raise ModuleNotFoundError("No module named 'pinka_platform'")

    steps.setattr(build, "run", broken)
    lines: list[str] = []
    assert gate.run(repo.path, None, printer=lines.append) is False
    assert lines[0].startswith(
        "gate: 7 passed, 2 failed, 3 skipped (actionlint, shellcheck, contract)"
    )
    assert "--- build: failed" in lines
    assert lines[-1] == "a.md:1:1: plan-step"
    assert any("ModuleNotFoundError: No module named 'pinka_platform'" in line for line in lines)


@pytest.mark.parametrize(
    ("summary", "status"),
    [
        ("12 passed in 0.10s", "passed"),
        ("11 passed, 1 skipped in 0.10s", "warning"),
        ("12 passed, 2 warnings in 0.10s", "warning"),
        ("====== 11 passed, 1 xfailed in 0.10s ======", "warning"),
    ],
)
def test_pytest_skips_and_warnings_are_warnings(summary: str, status: str) -> None:
    outcome = gate.pytest_outcome(Outcome("passed", ["SKIPPED [1] a reason", summary]))
    assert outcome.status == status
    assert gate.pytest_counts(outcome) == summary.strip("= ").rsplit(" in ", 1)[0]


def test_a_failed_pytest_run_stays_failed() -> None:
    outcome = gate.pytest_outcome(Outcome("failed", ["2 failed, 10 passed in 0.10s"]))
    assert outcome.status == "failed"
    assert gate.pytest_counts(outcome) == "2 failed, 10 passed"
