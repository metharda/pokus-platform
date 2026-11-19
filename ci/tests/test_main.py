from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import OTHER, Repo

from potka_ci import __main__ as cli
from potka_ci import commits, gate, privacy, references
from potka_ci.outcome import Outcome
from potka_ci.push import InputError, Push, push_inputs

PLATFORM = Path(__file__).resolve().parents[2] / "platform"
BEFORE = "1" * 40
AFTER = "2" * 40


def recorder(calls: list[tuple[object, ...]]) -> object:
    def run(*args: object, **kwargs: object) -> Outcome:
        calls.append((*args, kwargs))
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
    assert push_inputs(repo.path, {}) == Push("moving/on", None, "HEAD", pushed=False)
    repo.git("checkout", "-q", "--detach")
    assert push_inputs(repo.path, {}) == Push("HEAD", None, "HEAD", pushed=False)


def test_a_push_event_gives_its_before_and_after(repo: Repo, tmp_path: Path) -> None:
    environ = actions(tmp_path, "main", {"before": BEFORE, "after": AFTER}, sha="3" * 40)
    assert push_inputs(repo.path, environ) == Push("main", BEFORE, AFTER, pushed=True)


def test_a_manual_run_has_no_before(repo: Repo, tmp_path: Path) -> None:
    environ = actions(tmp_path, "moving/on", {"inputs": {}}, name="workflow_dispatch")
    assert push_inputs(repo.path, environ) == Push("moving/on", None, AFTER, pushed=False)


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
    assert calls == [(cli.ROOT, name, BEFORE, AFTER, {"pushed": True})]
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
    assert calls == [(repo.path, name, BEFORE, AFTER, {"pushed": True})] * 2
    repo.git("checkout", "-q", "-b", "moving/on")
    calls.clear()
    gate.run(repo.path, None, printer=lambda line: None, environ={})
    assert calls == [(repo.path, "moving/on", None, "HEAD", {"pushed": False})] * 2
    assert not [m for m in markers if m.exists()]


# Another checkout


CHECKS = {"privacy": privacy, "references": references, "commits": commits}
PUBLIC_IPV4 = "8.8" + ".8.8"
SECTION = "see " + chr(0xA7) + "3.2"


@pytest.fixture
def elsewhere(repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Repo:
    """Another repository, with one commit on a branch; this repository cannot be read."""
    monkeypatch.setattr(cli, "ROOT", tmp_path / "this-repository-is-not-read")
    monkeypatch.setenv(privacy.DENYLIST_ENV, "x-y")
    repo.set_origin_main(repo.root)
    repo.git("checkout", "-q", "-b", "moving/on")
    repo.write("taxonomy.yaml", "version: 1\n")
    repo.git("add", "taxonomy.yaml")
    repo.commit("Add the taxonomy")
    return repo


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_a_checkout_and_a_root_commit_given_reach_the_check(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, check: str
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(CHECKS[check], "run", recorder(calls))
    monkeypatch.setattr(cli, "ROOT", tmp_path / "this-repository-is-not-read")
    for key, value in actions(tmp_path, "moving/on", {"before": BEFORE, "after": AFTER}).items():
        monkeypatch.setenv(key, value)
    assert cli.main([check, "--root", str(repo.path)]) == 0
    push: tuple[object, ...] = (repo.path, "moving/on", BEFORE, AFTER)
    assert calls == [(repo.path, {}) if check == "references" else (*push, {"pushed": True})]
    if check == "commits":
        assert cli.main([check, "--root", str(repo.path), "--root-commit", "3" * 40]) == 0
        assert calls[1:] == [(*push, {"root_commit": "3" * 40, "pushed": True})]


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_without_a_checkout_given_a_check_reads_this_repository(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, check: str
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(CHECKS[check], "run", recorder(calls))
    monkeypatch.setattr(cli, "ROOT", repo.path)
    for key, value in actions(tmp_path, "moving/on", {"before": BEFORE, "after": AFTER}).items():
        monkeypatch.setenv(key, value)
    assert cli.main([check]) == 0
    push: tuple[object, ...] = (repo.path, "moving/on", BEFORE, AFTER)
    assert calls == [(repo.path, {}) if check == "references" else (*push, {"pushed": True})]
    if check == "commits":
        assert cli.main([check, "--root-commit", "3" * 40]) == 0
        assert calls[1:] == [(*push, {"root_commit": "3" * 40, "pushed": True})]


def test_another_checkout_passes_the_three_checks(
    elsewhere: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(elsewhere.path)
    scope = "origin/main..HEAD (branch moving/on, no push event)"
    assert cli.main(["references", "--root", root]) == 0
    assert cli.main(["privacy", "--root", root]) == 0
    assert cli.main(["commits", "--root", root, "--root-commit", elsewhere.root]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "references: 1 file(s) and 2 commit message(s) checked",
        f"privacy: 1 file(s) and 1 commit(s) of {scope} scanned",
        f"commits: 1 commit(s) checked ({scope})",
    ]


def test_another_checkout_s_findings_are_its_own(
    elsewhere: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(elsewhere.path)
    elsewhere.write("blueprints/web.yaml", f"# {SECTION}\nresolver: {PUBLIC_IPV4}\n")
    sha = elsewhere.commit("Another change", author=OTHER)
    assert cli.main(["references", "--root", root]) == 1
    assert cli.main(["privacy", "--root", root]) == 1
    assert cli.main(["commits", "--root", root, "--root-commit", elsewhere.root]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "blueprints/web.yaml:1:7: section-sign",
        "blueprints/web.yaml:2:11: public IPv4 address (use 192.0.2.0/24 and the like)",
        f"commit {sha[:12]}: the author is not the expected identity",
        f"commit {sha[:12]}: the committer is not the expected identity",
    ]


def test_another_repository_s_commits_need_its_root_commit(
    elsewhere: Repo, capsys: pytest.CaptureFixture[str]
) -> None:
    root = str(elsewhere.path)
    assert cli.main(["commits", "--root", root]) == 1
    assert cli.main(["commits", "--root", root, "--root-commit", elsewhere.head()]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"the history has the root commit(s) {elsewhere.root[:12]}, not the repository's root"
        f" commit {other[:12]}"
        for other in (commits.ROOT_COMMIT, elsewhere.head())
    ]


def test_a_relative_checkout_is_read_from_the_working_directory(
    elsewhere: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(elsewhere.path.parent)
    link = elsewhere.path.parent / "link"
    link.symlink_to(elsewhere.path, target_is_directory=True)
    scope = "origin/main..HEAD (branch moving/on, no push event)"
    for given in (elsewhere.path.name, "link", f"link/../{elsewhere.path.name}"):
        assert cli.main(["references", "--root", given]) == 0
        assert cli.main(["privacy", "--root", given]) == 0
        assert cli.main(["commits", "--root", given, "--root-commit", elsewhere.root]) == 0
    assert (
        capsys.readouterr().out.splitlines()
        == [
            "references: 1 file(s) and 2 commit message(s) checked",
            f"privacy: 1 file(s) and 1 commit(s) of {scope} scanned",
            f"commits: 1 commit(s) checked ({scope})",
        ]
        * 3
    )


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_a_checkout_given_is_the_top_of_a_working_tree(
    elsewhere: Repo,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    check: str,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(CHECKS[check], "run", recorder(calls))
    below = elsewhere.path / "blueprints"
    below.mkdir()
    bare = tmp_path / "bare.git"
    elsewhere.git("clone", "-q", "--bare", str(elsewhere.path), str(bare))
    plain = tmp_path / "plain"
    plain.mkdir()
    file = elsewhere.path / "taxonomy.yaml"
    assert cli.main([check, "--root", str(below)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"--root: {below} is not the top of a git working tree"
    ]
    # Where git itself refuses the path, its own words follow.
    said = {
        bare: "this operation must be run in a work tree",
        plain: "not a git repository",
        file: "Not a directory",
        tmp_path / "missing": "No such file or directory",
    }
    for given, words in said.items():
        assert cli.main([check, "--root", str(given)]) == 1
        (line,) = capsys.readouterr().out.splitlines()
        refusal = f"--root: {given} is not the top of a git working tree"
        assert line.startswith(f"{refusal} (git rev-parse --show-toplevel: fatal: ")
        assert words in line
    assert calls == []


@pytest.mark.parametrize("check", sorted(CHECKS))
def test_a_checkout_given_is_not_this_repository(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], check: str
) -> None:
    # make runs the checks in a directory of this repository: a relative path that is one
    # level short names this repository, which would pass in the other's place.
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(CHECKS[check], "run", recorder(calls))
    monkeypatch.setattr(cli, "ROOT", repo.path)
    below = repo.path / "platform"
    below.mkdir()
    monkeypatch.chdir(below)
    for given in ("..", str(repo.path), f"../../{repo.path.name}"):
        assert cli.main([check, "--root", given]) == 1
        assert capsys.readouterr().out.splitlines() == [
            f"--root: {given} is this repository, which is checked without --root"
        ]
    assert calls == []


@pytest.mark.parametrize("given", ["abc", "HEAD", "A" * 40, "0" * 39, "0" * 41, "0" * 40 + "\n"])
def test_a_root_commit_given_is_a_commit_hash(
    capsys: pytest.CaptureFixture[str], given: str
) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["commits", "--root-commit", given])
    assert raised.value.code == 2
    assert "argument --root-commit: not a commit hash (40 hex digits)" in capsys.readouterr().err


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


CATALOG_TARGETS = [
    ("catalog-lint", ["potka", "catalog", "lint", "{catalog}"]),
    ("catalog-privacy", ["privacy", "--root", "{catalog}"]),
    ("catalog-references", ["references", "--root", "{catalog}"]),
    ("catalog-commits", ["commits", "--root", "{catalog}", "--root-commit", "{root_commit}"]),
]


def make(
    tmp_path: Path, target: str, *variables: str, environ: dict[str, str] | None = None
) -> tuple[int, str, object]:
    """Run a make target with the tools replaced by a recorder: status, output, the record."""
    tmp_path.mkdir(exist_ok=True)
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    tool = f"{sys.executable} {script} {record}"
    done = subprocess.run(
        ["make", "-s", "-C", str(PLATFORM), target, f"CHECK={tool}", f"RUN={tool}", *variables],
        env={"PATH": "/usr/bin:/bin", **(environ or {})},
        capture_output=True,
        text=True,
        check=False,
    )
    recorded = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
    return done.returncode, done.stdout + done.stderr, recorded


@pytest.mark.parametrize(("target", "arguments"), CATALOG_TARGETS)
def test_the_catalog_targets_name_the_catalog_s_checkout(
    tmp_path: Path, target: str, arguments: list[str]
) -> None:
    catalog = str(tmp_path / "a catalog")
    values = {"catalog": catalog, "root_commit": "3" * 40}
    status, output, recorded = make(
        tmp_path, target, f"CATALOG={catalog}", f"ROOT_COMMIT={values['root_commit']}"
    )
    assert (status, output) == (0, "")
    assert recorded == {"argv": [a.format(**values) for a in arguments], "ref": None}


@pytest.mark.parametrize(("target", "arguments"), CATALOG_TARGETS)
def test_a_catalog_target_without_its_checkout_runs_nothing(
    tmp_path: Path, target: str, arguments: list[str]
) -> None:
    status, output, recorded = make(tmp_path, target, f"ROOT_COMMIT={'3' * 40}")
    assert status == 2
    assert output.splitlines()[0] == f"{target}: CATALOG=<path of the catalog checkout>"
    assert recorded is None


def test_the_catalog_s_commit_check_needs_its_root_commit(tmp_path: Path) -> None:
    status, output, recorded = make(tmp_path, "catalog-commits", f"CATALOG={tmp_path}")
    assert status == 2
    assert output.splitlines()[0] == (
        "catalog-commits: ROOT_COMMIT=<the root commit of the catalog's repository>"
    )
    assert recorded is None


OWN_TARGETS = ["privacy", "references", "commits"]


@pytest.mark.parametrize("target", OWN_TARGETS)
def test_a_target_of_this_repository_runs_its_check_without_a_catalog(
    tmp_path: Path, target: str
) -> None:
    ran = (0, "", {"argv": [target], "ref": None})
    assert make(tmp_path / "plain", target) == ran
    # A variable given as empty is not given, on the command line or in the environment.
    assert make(tmp_path / "empty", target, "CATALOG=", "ROOT_COMMIT=") == ran
    assert make(tmp_path / "env", target, environ={"CATALOG": "", "ROOT_COMMIT": ""}) == ran


@pytest.mark.parametrize("target", OWN_TARGETS)
@pytest.mark.parametrize(
    "variables",
    [
        ["CATALOG=../../a-catalog"],
        [f"ROOT_COMMIT={'3' * 40}"],
        ["CATALOG=../../a-catalog", f"ROOT_COMMIT={'3' * 40}"],
    ],
)
def test_a_target_of_this_repository_takes_no_catalog(
    tmp_path: Path, target: str, variables: list[str]
) -> None:
    status, output, recorded = make(tmp_path, target, *variables)
    assert status == 2
    assert output.splitlines()[0] == (
        f"{target}: checks this repository and takes no CATALOG or ROOT_COMMIT;"
        f" catalog-{target} checks a catalog"
    )
    assert recorded is None


@pytest.mark.parametrize("target", OWN_TARGETS)
@pytest.mark.parametrize("variable", ["CATALOG", "ROOT_COMMIT"])
def test_a_catalog_in_the_environment_is_refused_too(
    tmp_path: Path, target: str, variable: str
) -> None:
    status, output, recorded = make(tmp_path, target, environ={variable: "../../a-catalog"})
    assert status == 2
    assert output.splitlines()[0].startswith(f"{target}: checks this repository and takes no ")
    assert recorded is None


@pytest.mark.parametrize("target", OWN_TARGETS)
def test_nothing_runs_after_the_refusal_when_make_ignores_errors(
    tmp_path: Path, target: str
) -> None:
    status, output, recorded = make(tmp_path, target, "CATALOG=../../a-catalog", "-i")
    assert status == 0
    assert output.splitlines()[0].startswith(f"{target}: checks this repository and takes no ")
    assert recorded is None


@pytest.mark.parametrize("target", OWN_TARGETS)
def test_a_catalog_given_to_a_target_of_this_repository_is_never_run(
    tmp_path: Path, target: str
) -> None:
    # make tests the variable itself: its value is not written into the recipe.
    marker = tmp_path / "ran"
    status, _, recorded = make(tmp_path, target, f'CATALOG="; touch {marker}; "')
    assert status == 2
    assert recorded is None
    assert not marker.exists()
