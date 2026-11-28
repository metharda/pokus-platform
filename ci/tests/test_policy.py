from __future__ import annotations

import hashlib
import json
import platform
import shutil
import stat
from pathlib import Path

import pytest

from potka_ci import contract, policy

ROOT = Path(__file__).resolve().parents[2]
CASES = [{"package": "data.potka.action_test", "name": f"test_{n}"} for n in range(3)]
# What OPA 1.21.1 printed for `opa test --coverage --format json policy`, with the exit
# status 0 both times: for a bundle of one rule with its default, each tried by a case,
# and for the same bundle with a second rule, of two lines, that no case tries. Below a
# `--threshold` it prints no report at all, only that the threshold is not met.
TRIED = (
    '{"files":{"policy/p/a.rego":{"covered":[{"start":{"row":3,"col":1},"end":{"row":3,"c'
    'ol":8}},{"start":{"row":3,"col":9},"end":{"row":3,"col":23}},{"start":{"row":5,"col"'
    ':1},"end":{"row":5,"col":6}},{"start":{"row":5,"col":10},"end":{"row":5,"col":22}}],'
    '"covered_lines":2,"coverage":100},"policy/p/a_test.rego":{"covered":[{"start":{"row"'
    ':5,"col":1},"end":{"row":5,"col":11}},{"start":{"row":5,"col":15},"end":{"row":5,"co'
    'l":45}},{"start":{"row":7,"col":1},"end":{"row":7,"col":10}},{"start":{"row":7,"col"'
    ':14},"end":{"row":7,"col":42}}],"covered_lines":2,"coverage":100}},"covered_lines":4'
    ',"not_covered_lines":0,"coverage":100}'
)
UNTRIED = (
    '{"files":{"policy/p/a.rego":{"covered":[{"start":{"row":3,"col":1},"end":{"row":3,"c'
    'ol":8}},{"start":{"row":3,"col":9},"end":{"row":3,"col":23}},{"start":{"row":5,"col"'
    ':1},"end":{"row":5,"col":6}},{"start":{"row":5,"col":10},"end":{"row":5,"col":22}}],'
    '"not_covered":[{"start":{"row":7,"col":1},"end":{"row":7,"col":6}},{"start":{"row":8'
    ',"col":2},"end":{"row":8,"col":14}},{"start":{"row":9,"col":2},"end":{"row":9,"col":'
    '14}}],"covered_lines":2,"not_covered_lines":3,"coverage":40},"policy/p/a_test.rego":'
    '{"covered":[{"start":{"row":5,"col":1},"end":{"row":5,"col":11}},{"start":{"row":5,"'
    'col":15},"end":{"row":5,"col":45}},{"start":{"row":7,"col":1},"end":{"row":7,"col":1'
    '0}},{"start":{"row":7,"col":14},"end":{"row":7,"col":42}}],"covered_lines":2,"covera'
    'ge":100}},"covered_lines":4,"not_covered_lines":3,"coverage":57.142857142857146}'
)


def stand_in(root: Path, answers: dict[str, tuple[int, str]] | None = None) -> Path:
    """A working tree whose OPA is a script: it records how it was run and answers as told.

    ANSWERS gives, for the first two words of a run (``check --strict``,
    ``fmt --list``, ``test --format``, ``test --coverage``, ``test policy``),
    an exit status and what is printed; a run without an answer prints
    nothing and ends 0, the cases' run prints three passed cases, and the
    coverage's run the report of a bundle whose every line is tried.
    """
    said = {
        "test --format": (0, json.dumps(CASES)),
        "test --coverage": (0, TRIED),
        **(answers or {}),
    }
    binary = root / policy.BINARY
    binary.parent.mkdir(parents=True)
    lines = ["#!/bin/sh", f'echo "$*" >> "{binary.parent}/runs"', 'case "$1 $2" in']
    for words, (status, text) in said.items():
        answer = binary.parent / f"answer-{words.replace(' ', '-')}"
        answer.write_text(text, encoding="utf-8")
        lines.append(f'  "{words}") cat "{answer}"; exit {status} ;;')
    binary.write_text("\n".join([*lines, "esac", ""]), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    lock = root / contract.OPA_LOCK
    lock.parent.mkdir(parents=True)
    lock.write_text(
        "# The stand-in's lock.\n"
        f"9.9.9 {platform.machine()} https://example.org/opa/v9.9.9/opa {digest}\n"
        f"9.9.9 other https://example.org/opa/v9.9.9/opa_other {'0' * 64}\n",
        encoding="utf-8",
    )
    (root / policy.BUNDLE).mkdir(parents=True)
    return root


def runs(root: Path) -> list[str]:
    record = root / policy.BINARY.parent / "runs"
    return record.read_text(encoding="utf-8").splitlines() if record.exists() else []


def test_a_bundle_that_passes_every_check(tmp_path: Path) -> None:
    outcome = policy.run(stand_in(tmp_path), {})
    assert (outcome.status, outcome.lines) == (
        "passed",
        ["policy: 3 case(s) passed, every line tried, in OPA's format, strict"],
    )
    assert runs(tmp_path) == [
        "check --strict policy",
        "fmt --list policy",
        "test --format json policy",
        "test --coverage --format json policy",
    ]


def test_the_lock_names_this_machines_binary(tmp_path: Path) -> None:
    stand_in(tmp_path)
    version, sha256 = policy.locked(tmp_path)
    assert version == "9.9.9"
    assert sha256 == hashlib.sha256((tmp_path / policy.BINARY).read_bytes()).hexdigest()
    assert policy.locked(tmp_path, "other") == ("9.9.9", "0" * 64)
    with pytest.raises(policy.NoOpa, match="has 0 lines for s390x, not one"):
        policy.locked(tmp_path, "s390x")
    (tmp_path / contract.OPA_LOCK).unlink()
    with pytest.raises(policy.NoOpa, match="is missing or is not UTF-8"):
        policy.locked(tmp_path)


def test_a_machine_named_twice_in_the_lock_has_no_binary(tmp_path: Path) -> None:
    stand_in(tmp_path)
    lock = tmp_path / contract.OPA_LOCK
    line = f"9.9.8 {platform.machine()} https://example.org/opa/v9.9.8/opa {'1' * 64}\n"
    lock.write_text(lock.read_text(encoding="utf-8") + line, encoding="utf-8")
    with pytest.raises(policy.NoOpa, match="has 2 lines for"):
        policy.verified_binary(tmp_path)


@pytest.mark.parametrize(("environ", "status"), [({}, "skipped"), ({"CI": "true"}, "failed")])
def test_without_the_binary_the_check_is_skipped_and_in_ci_it_fails(
    tmp_path: Path, environ: dict[str, str], status: str
) -> None:
    stand_in(tmp_path)
    (tmp_path / policy.BINARY).unlink()
    outcome = policy.run(tmp_path, environ)
    assert (outcome.status, outcome.lines) == (status, ["no OPA in platform/.opa: make opa"])


@pytest.mark.parametrize(("environ", "status"), [({}, "skipped"), ({"CI": "true"}, "failed")])
def test_a_binary_that_is_not_the_locked_one_is_never_run(
    tmp_path: Path, environ: dict[str, str], status: str
) -> None:
    stand_in(tmp_path)
    binary = tmp_path / policy.BINARY
    binary.write_text(binary.read_text(encoding="utf-8") + "# another byte\n", encoding="utf-8")
    outcome = policy.run(tmp_path, environ)
    assert (outcome.status, outcome.lines) == (
        status,
        ["platform/.opa/opa is not the locked binary (its sha256 differs): make opa"],
    )
    assert runs(tmp_path) == []


def test_a_bundle_that_does_not_compile_fails_with_opas_words(tmp_path: Path) -> None:
    said = "1 error occurred: policy/potka/action.rego:3: rego_parse_error: no match found\n"
    outcome = policy.run(stand_in(tmp_path, {"check --strict": (1, said)}), {})
    assert (outcome.status, outcome.lines) == (
        "failed",
        ["opa check --strict policy: exit status 1", f"  {said.rstrip()}"],
    )
    # Its cases are not run: nothing can be said of them.
    assert runs(tmp_path) == ["check --strict policy", "fmt --list policy"]


@pytest.mark.parametrize("status", [0, 1])
def test_a_file_that_is_not_in_opas_format_fails(tmp_path: Path, status: int) -> None:
    stand_in(tmp_path, {"fmt --list": (status, "policy/potka/action_test.rego\n")})
    outcome = policy.run(tmp_path, {})
    assert (outcome.status, outcome.lines) == (
        "failed",
        [
            "opa fmt --list policy: these files are not in OPA's format",
            "  policy/potka/action_test.rego",
        ],
    )


@pytest.mark.parametrize(
    ("cases", "status", "said"),
    [
        ([{**CASES[0], "fail": True}, CASES[1]], 2, ["exit status 2, 2 case(s)", "test_0"]),
        ([{**CASES[0], "error": {"code": "x"}}], 2, ["exit status 2, 1 case(s)", "test_0"]),
        ([{**CASES[0], "skip": True}], 0, ["exit status 0, 1 case(s)", "test_0"]),
        (CASES, 2, ["exit status 2, 3 case(s)"]),
        ([], 0, ["exit status 0, 0 case(s)"]),
    ],
    ids=["a-failed-case", "a-case-in-error", "a-skipped-case", "a-status-alone", "no-case"],
)
def test_cases_that_do_not_all_pass_fail(
    tmp_path: Path, cases: list[dict[str, object]], status: int, said: list[str]
) -> None:
    stand_in(tmp_path, {"test --format": (status, json.dumps(cases))})
    outcome = policy.run(tmp_path, {})
    assert outcome.status == "failed"
    assert outcome.lines[0] == f"opa test policy: {said[0]}"
    assert [line.strip() for line in outcome.lines[1:]] == [
        f"data.potka.action_test.{name}: not passed" for name in said[1:]
    ]
    assert not [run for run in runs(tmp_path) if "--coverage" in run]


def test_cases_that_fail_are_shown_as_opa_itself_reports_them(tmp_path: Path) -> None:
    # What OPA 1.21.1 printed for a bundle with one failing case of two (exit status 2).
    reported = (
        "policy/p/a_test.rego:5:\n"
        "data.p_test.test_allow: FAIL (439.349µs)\n" + "-" * 80 + "\nPASS: 1/2\nFAIL: 1/2\n"
    )
    listed = [
        {"package": "data.p_test", "name": "test_deny"},
        {"package": "data.p_test", "name": "test_allow", "fail": True},
    ]
    stand_in(
        tmp_path,
        {"test --format": (2, json.dumps(listed)), "test policy": (2, reported)},
    )
    outcome = policy.run(tmp_path, {})
    assert outcome.status == "failed"
    assert outcome.lines == [
        "opa test policy: exit status 2, 2 case(s)",
        "  data.p_test.test_allow: not passed",
        *[f"  {line}" for line in reported.splitlines()],
    ]
    assert runs(tmp_path)[-1] == "test policy"


@pytest.mark.parametrize("printed", ["", "not json", '{"cases": []}', '["a case"]'])
def test_a_run_that_prints_no_list_of_cases_fails(tmp_path: Path, printed: str) -> None:
    outcome = policy.run(stand_in(tmp_path, {"test --format": (0, printed)}), {})
    assert outcome.status == "failed"
    first = "opa test policy: no list of cases" if printed else "opa test policy: exit status 0"
    assert outcome.lines[0].startswith(first)


def test_a_line_that_no_case_tries_fails_with_its_place(tmp_path: Path) -> None:
    stand_in(tmp_path, {"test --coverage": (0, UNTRIED)})
    outcome = policy.run(tmp_path, {})
    assert (outcome.status, outcome.lines) == (
        "failed",
        [
            "opa test --coverage policy: not every line is tried (exit status 0, coverage 57.1429)",
            "  policy/p/a.rego: line 7 to 9",
        ],
    )
    assert runs(tmp_path)[-1] == "test --coverage --format json policy"


def test_untried_lines_are_named_once_each_in_runs_of_their_own() -> None:
    def span(first: int, last: int) -> dict[str, dict[str, int]]:
        return {"start": {"row": first, "col": 1}, "end": {"row": last, "col": 9}}

    report = {
        "files": {
            "policy/b.rego": {"not_covered": [span(12, 12)], "coverage": 90},
            "policy/a.rego": {
                "not_covered": [span(9, 11), span(3, 3), span(3, 3), span(4, 4), span(7, 7)],
                "coverage": 50,
            },
            "policy/c.rego": {"coverage": 100},
        },
        "coverage": 80,
    }
    assert policy._coverage(json.dumps(report)) == (
        80.0,
        [
            "  policy/a.rego: line 3 to 4",
            "  policy/a.rego: line 7",
            "  policy/a.rego: line 9 to 11",
            "  policy/b.rego: line 12",
        ],
    )
    assert policy._coverage(TRIED) == (100.0, [])


@pytest.mark.parametrize(
    ("status", "printed", "said"),
    [
        (0, json.dumps({**json.loads(TRIED), "coverage": 99.5}), "exit status 0, coverage 99.5"),
        (2, TRIED, "exit status 2, coverage 100"),
    ],
    ids=["a-share-below-the-whole", "a-status-alone"],
)
def test_a_report_that_is_not_of_every_line_fails_though_it_names_none(
    tmp_path: Path, status: int, printed: str, said: str
) -> None:
    outcome = policy.run(stand_in(tmp_path, {"test --coverage": (status, printed)}), {})
    assert (outcome.status, outcome.lines) == (
        "failed",
        [f"opa test --coverage policy: not every line is tried ({said})"],
    )


@pytest.mark.parametrize(
    "printed",
    [
        "",
        "Code coverage threshold not met: got 57.14 instead of 100.00\n",
        "[]",
        '{"files": {}}',
        '{"coverage": "100", "files": {}}',
        '{"coverage": true, "files": {}}',
        '{"coverage": 100}',
        '{"coverage": 100, "files": []}',
        '{"coverage": 50, "files": {"policy/a.rego": {"not_covered": [{"start": {}}]}}}',
    ],
)
def test_what_is_no_coverage_report_fails_and_is_shown_as_it_is(
    tmp_path: Path, printed: str
) -> None:
    outcome = policy.run(stand_in(tmp_path, {"test --coverage": (0, printed)}), {})
    assert outcome.status == "failed"
    assert outcome.lines == [
        "opa test --coverage policy: no coverage report (exit status 0)",
        *[f"  {line}" for line in printed.splitlines()],
    ]


def test_opa_is_run_with_no_variable_of_the_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand_in(tmp_path)
    binary = tmp_path / policy.BINARY
    text = binary.read_text(encoding="utf-8").replace(
        "#!/bin/sh\n", f'#!/bin/sh\nenv > "{binary.parent}/environment"\n'
    )
    binary.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    lock = tmp_path / contract.OPA_LOCK
    lock.write_text(f"9.9.9 {platform.machine()} https://example.org/opa {digest}\n")
    monkeypatch.setenv("POTKA_SECRET_OF_THE_CALLER", "not for a fetched binary")
    assert policy.run(tmp_path, {}).status == "passed"
    seen = (binary.parent / "environment").read_text(encoding="utf-8")
    assert "POTKA_SECRET_OF_THE_CALLER" not in seen


def labs_opa() -> Path:
    """The installed OPA; the test skips without it, and fails on a binary that is not it."""
    there = (ROOT / policy.BINARY).exists()
    try:
        return policy.verified_binary(ROOT)
    except policy.NoOpa as exc:
        if there:
            pytest.fail(f"{exc}")
        pytest.skip(f"{exc}")


def with_the_labs_opa(root: Path) -> Path:
    """A working tree in ROOT with the real lock and the installed OPA, and no bundle yet."""
    binary = labs_opa()
    (root / contract.OPA_LOCK).parent.mkdir(parents=True)
    shutil.copy(ROOT / contract.OPA_LOCK, root / contract.OPA_LOCK)
    (root / policy.BINARY).parent.mkdir(parents=True)
    (root / policy.BINARY).symlink_to(binary)
    (root / policy.BUNDLE / "p").mkdir(parents=True)
    (root / policy.BUNDLE / ".manifest").write_text('{"roots": ["p"]}\n', encoding="utf-8")
    return root / policy.BUNDLE / "p"


RULE = "package p\n\ndefault allow := false\n\nallow if input.x == 1\n"
NEVER = "\nnever if {\n\tinput.y == 2\n\tinput.z == 3\n}\n"
TESTS = (
    "package p_test\n\nimport data.p\n\n"
    'test_allow if p.allow with input as {"x": 1}\n\n'
    "test_deny if not p.allow with input as {}\n"
)


def test_the_labs_opa_names_a_line_that_no_case_tries(tmp_path: Path) -> None:
    package = with_the_labs_opa(tmp_path)
    (package / "a.rego").write_text(RULE, encoding="utf-8")
    (package / "a_test.rego").write_text(TESTS, encoding="utf-8")
    assert policy.run(tmp_path, {}).lines == [
        "policy: 2 case(s) passed, every line tried, in OPA's format, strict"
    ]
    (package / "a.rego").write_text(RULE + NEVER, encoding="utf-8")
    outcome = policy.run(tmp_path, {})
    assert (outcome.status, outcome.lines) == (
        "failed",
        [
            "opa test --coverage policy: not every line is tried (exit status 0, coverage 57.1429)",
            "  policy/p/a.rego: line 7 to 9",
        ],
    )


def test_the_labs_opa_shows_where_a_case_failed(tmp_path: Path) -> None:
    package = with_the_labs_opa(tmp_path)
    (package / "a.rego").write_text(RULE.replace("== 1", "== 2"), encoding="utf-8")
    (package / "a_test.rego").write_text(TESTS, encoding="utf-8")
    outcome = policy.run(tmp_path, {})
    assert outcome.status == "failed"
    assert outcome.lines[:2] == [
        "opa test policy: exit status 2, 2 case(s)",
        "  data.p_test.test_allow: not passed",
    ]
    assert "  policy/p/a_test.rego:5:" in outcome.lines
    assert outcome.lines[-2:] == ["  PASS: 1/2", "  FAIL: 1/2"]


def test_the_shipped_bundle_passes_with_the_labs_opa() -> None:
    labs_opa()
    outcome = policy.run(ROOT, {})
    assert outcome.status == "passed", outcome.lines
    assert outcome.lines[0].startswith("policy: ")
