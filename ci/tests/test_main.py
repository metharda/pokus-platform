from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import Repo

from potka_ci import __main__ as cli
from potka_ci import commits, gate, privacy
from potka_ci.outcome import Outcome
from potka_ci.push import InputError, Push, push_inputs

PLATFORM = Path(__file__).resolve().parents[2] / "platform"
BEFORE = "1" * 40
AFTER = "2" * 40


def recorder(calls: list[tuple[object, ...]]) -> object:
    def run(*args: object, **kwargs: object) -> Outcome:
        calls.append(args)
        return Outcome("passed", ["ok"])

    return run


def actions(
    tmp_path: Path, branch: str, event: object, name: str = "push", sha: str = AFTER
) -> dict[str, str]:
    """The variables GitHub Actions sets for a run, with its event file."""
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REF_NAME": branch,
        "GITHUB_EVENT_NAME": name,
        "GITHUB_EVENT_PATH": str(path),
        "GITHUB_SHA": sha,
    }


def hostile_branch(tmp_path: Path) -> tuple[str, list[Path]]:
    """A valid branch name holding $(...), backticks and ';', and the files they would create."""
    markers = [tmp_path / f"ran-{n}" for n in (1, 2, 3)]
    name = f"x/$(touch${{IFS}}{markers[0]})`touch${{IFS}}{markers[1]}`;touch${{IFS}}{markers[2]}"
    done = subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"], check=False)
    assert done.returncode == 0, name
    return name, markers


def test_outside_actions_the_checked_out_branch_and_head(repo: Repo) -> None:
    repo.git("checkout", "-q", "-b", "moving/on")
    assert push_inputs(repo.path, {}) == Push("moving/on", None, "HEAD")
    repo.git("checkout", "-q", "--detach")
    assert push_inputs(repo.path, {}) == Push("HEAD", None, "HEAD")


def test_a_push_event_gives_its_before_and_after(repo: Repo, tmp_path: Path) -> None:
    environ = actions(tmp_path, "main", {"before": BEFORE, "after": AFTER}, sha="3" * 40)
    assert push_inputs(repo.path, environ) == Push("main", BEFORE, AFTER)


def test_a_manual_run_has_no_before(repo: Repo, tmp_path: Path) -> None:
    environ = actions(tmp_path, "moving/on", {"inputs": {}}, name="workflow_dispatch")
    assert push_inputs(repo.path, environ) == Push("moving/on", None, AFTER)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"before": "abc"}, "the push event's before is not a commit hash (40 hex digits)"),
        ({"before": "HEAD"}, "the push event's before is not a commit hash (40 hex digits)"),
        ({"before": None}, "the push event's before is not a commit hash (40 hex digits)"),
        (
            {"after": "--all" + "0" * 35},
            "the push event's after is not a commit hash (40 hex digits)",
        ),
        ({"after": "A" * 40}, "the push event's after is not a commit hash (40 hex digits)"),
        ({"after": AFTER + "\n"}, "the push event's after is not a commit hash (40 hex digits)"),
        ({"GITHUB_REF_NAME": "a..b"}, "GITHUB_REF_NAME is not a valid branch name"),
        ({"GITHUB_REF_NAME": "x y"}, "GITHUB_REF_NAME is not a valid branch name"),
        ({"GITHUB_REF_NAME": ""}, "GITHUB_REF_NAME is not a valid branch name"),
        ({"GITHUB_EVENT_PATH": ""}, "GITHUB_EVENT_PATH is not set: the push event cannot be read"),
    ],
)
def test_a_malformed_input_is_refused(
    repo: Repo, tmp_path: Path, change: dict[str, object], message: str
) -> None:
    event: dict[str, object] = {"before": BEFORE, "after": AFTER}
    event.update({k: v for k, v in change.items() if not k.startswith("GITHUB_")})
    environ = actions(tmp_path, "moving/on", event)
    environ.update({k: str(v) for k, v in change.items() if k.startswith("GITHUB_")})
    with pytest.raises(InputError) as raised:
        push_inputs(repo.path, environ)
    assert str(raised.value) == message


def test_a_manual_run_needs_a_commit_hash(repo: Repo, tmp_path: Path) -> None:
    environ = actions(tmp_path, "main", {}, name="workflow_dispatch", sha="HEAD")
    with pytest.raises(InputError, match=r"^GITHUB_SHA is not a commit hash"):
        push_inputs(repo.path, environ)


@pytest.mark.parametrize("check", ["privacy", "commits"])
def test_the_command_line_refuses_a_malformed_input(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    check: str,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(privacy if check == "privacy" else commits, "run", recorder(calls))
    for key, value in actions(tmp_path, "main", {"before": "abc", "after": AFTER}).items():
        monkeypatch.setenv(key, value)
    assert cli.main([check]) == 1
    assert calls == []
    assert capsys.readouterr().out.splitlines()[0] == (
        "the push event's before is not a commit hash (40 hex digits)"
    )


@pytest.mark.parametrize("check", ["privacy", "commits"])
def test_the_command_line_passes_the_push_as_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, check: str
) -> None:
    name, markers = hostile_branch(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(privacy if check == "privacy" else commits, "run", recorder(calls))
    for key, value in actions(tmp_path, name, {"before": BEFORE, "after": AFTER}).items():
        monkeypatch.setenv(key, value)
    assert cli.main([check]) == 0
    assert calls == [(cli.ROOT, name, BEFORE, AFTER)]
    assert not [m for m in markers if m.exists()]


def test_a_hostile_branch_name_reaches_the_privacy_scan_as_plain_data(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    name, markers = hostile_branch(tmp_path)
    repo.set_origin_main(repo.root)
    repo.git("checkout", "-q", "-b", "work")
    head = repo.commit("A change")
    monkeypatch.setattr(cli, "ROOT", repo.path)
    environ = actions(tmp_path, name, {"before": "0" * 40, "after": head})
    for key, value in {**environ, privacy.DENYLIST_ENV: "x-y"}.items():
        monkeypatch.setenv(key, value)
    assert cli.main(["privacy"]) == 0
    assert capsys.readouterr().out == (
        f"privacy: 0 file(s) and 1 commit(s) of origin/main..{head} (branch {name}) scanned\n"
    )
    assert not [m for m in markers if m.exists()]


def test_the_command_line_takes_no_range_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["privacy", "--branch", "main"])
    assert "unrecognized arguments: --branch main" in capsys.readouterr().err


def test_the_gate_reads_the_push_from_the_environment(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    name, markers = hostile_branch(tmp_path)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(gate, "tool", lambda *args: Outcome("passed", ["ok"]))
    monkeypatch.setattr(privacy, "run", recorder(calls))
    monkeypatch.setattr(commits, "run", recorder(calls))
    environ = actions(tmp_path, name, {"before": BEFORE, "after": AFTER})
    gate.run(repo.path, None, printer=lambda line: None, environ=environ)
    assert calls == [(repo.path, name, BEFORE, AFTER), (repo.path, name, BEFORE, AFTER)]
    repo.git("checkout", "-q", "-b", "moving/on")
    calls.clear()
    gate.run(repo.path, None, printer=lambda line: None, environ={})
    assert calls == [(repo.path, "moving/on", None, "HEAD"), (repo.path, "moving/on", None, "HEAD")]
    assert not [m for m in markers if m.exists()]


RECORDER = """\
import json, os, sys
with open(sys.argv[1], "w") as out:
    json.dump({"argv": sys.argv[2:], "ref": os.environ.get("GITHUB_REF_NAME")}, out)
"""


@pytest.mark.parametrize("target", ["privacy", "commits"])
def test_the_make_targets_pass_no_push_value_through_make_or_a_shell(
    tmp_path: Path, target: str
) -> None:
    # Even the variables the targets once read (BRANCH, BEFORE, AFTER) are
    # set to the hostile name: make must not expand them into a recipe.
    name, markers = hostile_branch(tmp_path)
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    environ = actions(tmp_path, name, {"before": BEFORE, "after": AFTER})
    environ.update(BRANCH=name, BEFORE=name, AFTER=name)
    done = subprocess.run(
        ["make", "-s", "-C", str(PLATFORM), target, f"CHECK={sys.executable} {script} {record}"],
        env={**environ, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == {"argv": [target], "ref": name}
    assert not [m for m in markers if m.exists()]
