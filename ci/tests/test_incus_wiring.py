from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from golden import infra, platform
from test_incus_api import COMMIT, tree

from potka_ci import __main__ as cli
from potka_ci import generated, incus_api
from potka_ci.outcome import Outcome

ROOT = Path(__file__).resolve().parents[2]
EXTRACT = "platform/tests/fixtures/incus-api/api.json"
TABLE = "platform/infra-data/golden-images.json"
SCHEMA = "platform/schemas/catalog/scenario.schema.json"
NO_INCUS = "no directory with Incus's two files (INCUS=<path>)"
NOT_COMPARED = f"{EXTRACT} was not compared with a fresh extract: {NO_INCUS}"
MATCHES = f"{EXTRACT} matches a fresh extract"
DIFFERS = f"{EXTRACT}: differs from a fresh generation (make incus-api)"
FETCH = (
    "when one of Incus's two files is missing or is not the recorded one: make incus-sources"
    " INCUS=<path> fetches both at the commit that"
    " platform/tests/fixtures/incus-api/source.json names"
)
FILES = "12 generated file(s) match a fresh generation"


def with_tools(root: Path) -> None:
    """Give the tree ROOT this repository's tool registry and the files written from code.

    They are what the registry is compiled to, and the page of the primitives.
    """
    shutil.copytree(ROOT / generated.REGISTRY, root / generated.REGISTRY)
    assert generated.write(root, "tools").status == "passed"
    assert generated.write(root, "primitives").status == "passed"


def test_the_extract_is_compared_with_a_fresh_one_made_from_incus_s_files(tmp_path: Path) -> None:
    root, incus, _ = tree(tmp_path)
    # A missing extract is found with Incus's files and without them.
    missing = Outcome("failed", [f"{EXTRACT}: missing (make incus-api writes it)"])
    assert generated.extract(root, incus) == missing
    assert generated.extract(root) == missing
    assert generated.write(root, "incus-api") == Outcome("skipped", [NO_INCUS])
    assert generated.write(root, "incus-api", incus=incus).lines == [f"wrote {EXTRACT}"]
    assert generated.extract(root, incus) == Outcome("passed", [MATCHES])
    (root / incus_api.EXTRACT).write_text("{}\n", encoding="utf-8")
    assert generated.extract(root, incus) == Outcome("failed", [DIFFERS])
    # A file of Incus with another sha256, or none: failed, with how to fetch them.
    (incus / incus_api.API).write_bytes(b"paths: {}\n")
    outcome = generated.extract(root, incus)
    assert outcome.status == "failed"
    assert outcome.lines[0].startswith(f"{EXTRACT}: {incus_api.API}: sha256 ")
    assert outcome.lines[1:] == [FETCH]
    assert generated.write(root, "incus-api", incus=incus).status == "failed"
    (incus / incus_api.API).unlink()
    assert generated.extract(root, incus) == Outcome(
        "failed", [f"{EXTRACT}: {incus_api.API}: FileNotFoundError in {incus}", FETCH]
    )
    nowhere = tmp_path / "no such directory"
    assert generated.extract(root, nowhere) == Outcome(
        "failed", [f"{EXTRACT}: {incus_api.API}: FileNotFoundError in {nowhere}", FETCH]
    )


def test_an_extract_that_cannot_be_read_is_failed_with_incus_s_files_and_without(
    tmp_path: Path,
) -> None:
    root, incus, _ = tree(tmp_path)
    (root / incus_api.EXTRACT).mkdir()
    unread = Outcome("failed", [f"{EXTRACT}: cannot be read (IsADirectoryError)"])
    assert generated.extract(root, incus) == unread
    assert generated.extract(root) == unread


def test_without_incus_s_files_only_the_extract_s_comparison_is_skipped(tmp_path: Path) -> None:
    root, incus, _ = tree(tmp_path)
    assert generated.write(root, "schemas").status == "passed"
    with_tools(root)
    generated.write(root, "incus-api", incus=incus)
    (root / incus_api.README).write_text("where it comes from\n", encoding="utf-8")
    # The skipped comparison names the extract.
    assert generated.extract(root) == Outcome("skipped", [NOT_COMPARED])
    # Every other generated file is compared all the same; Incus's files are no reason of its.
    outcome = generated.run(root)
    assert (outcome.status, outcome.lines) == (
        "skipped",
        [
            f"11 generated file(s) match a fresh generation; {TABLE} was not compared:"
            " no pokus-infrastructure checkout (INFRA=<path>)"
        ],
    )
    schema = root / generated.SCHEMAS / "scenario.schema.json"
    kept = schema.read_text(encoding="utf-8")
    schema.write_text("{}\n", encoding="utf-8")
    assert generated.run(root) == Outcome(
        "failed",
        [f"{SCHEMA}: differs from a fresh generation (make schemas)"],
    )
    schema.write_text(kept, encoding="utf-8")
    # The extract's directory holds the extract, its source and what says so, and nothing else.
    (root / incus_api.DIRECTORY / "notes.txt").write_text("x\n", encoding="utf-8")
    assert generated.run(root) == Outcome(
        "failed", ["platform/tests/fixtures/incus-api/notes.txt: no generator writes it: remove it"]
    )


def test_the_command_line_fetches_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, contents = tree(tmp_path)
    monkeypatch.setattr(cli, "ROOT", root)
    monkeypatch.setattr(incus_api, "_download", lambda url: contents[url.split(COMMIT + "/")[1]])
    assert cli.main(["incus-sources", str(tmp_path / "fetched")]) == 0
    assert capsys.readouterr().out.startswith("fetched ")
    assert (
        cli.main(["generated", "--write", "incus-api", "--incus", str(tmp_path / "fetched")]) == 0
    )
    assert capsys.readouterr().out == f"wrote {EXTRACT}\n"
    assert cli.main(["generated", "--write", "incus-api"]) == 2
    assert capsys.readouterr().out == f"{NO_INCUS}\n"


def test_a_skipped_extract_is_named_and_the_status_is_the_other_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, incus, _ = tree(tmp_path)
    generated.write(root, "incus-api", incus=incus)
    monkeypatch.setattr(cli, "ROOT", root)
    files = {"outcome": Outcome("passed", [FILES])}
    monkeypatch.setattr(generated, "run", lambda root, infra=None: files["outcome"])

    def said(*argv: str) -> tuple[int, str, str]:
        status = cli.main(["generated", *argv])
        out, err = capsys.readouterr()
        return status, out, err

    # Without Incus's files: the other files' result and status, and the extract by its name.
    assert said() == (0, f"{FILES}\n{NOT_COMPARED}\n", "incus-extract: skipped\n")
    assert said("--incus", str(incus)) == (0, f"{FILES}\n{MATCHES}\n", "")
    # The other files skipped or failed: that is the status, with the extract or without.
    files["outcome"] = Outcome("skipped", ["4 generated file(s); the table was not compared"])
    assert said("--incus", str(incus))[::2] == (2, "generated: skipped\n")
    assert said()[::2] == (2, "generated: skipped\nincus-extract: skipped\n")
    files["outcome"] = Outcome("failed", ["a.schema.json: differs"])
    assert said() == (1, f"a.schema.json: differs\n{NOT_COMPARED}\n", "incus-extract: skipped\n")
    # A changed extract fails the command where Incus's files are, whatever the others say.
    (root / incus_api.EXTRACT).write_text("{}\n", encoding="utf-8")
    for outcome in (Outcome("passed", [FILES]), Outcome("skipped", ["no table"])):
        files["outcome"] = outcome
        assert said("--incus", str(incus))[:2] == (1, f"{outcome.lines[0]}\n{DIFFERS}\n")
    # Without them it cannot be seen, and the skipped comparison says so.
    files["outcome"] = Outcome("passed", [FILES])
    assert said() == (0, f"{FILES}\n{NOT_COMPARED}\n", "incus-extract: skipped\n")


def test_the_command_runs_both_checks_as_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing stands in for either check here: the schemas and the table of a synthetic
    # tree with its checkout, and the extract of the same tree with Incus's stand-in files.
    repo = infra(tmp_path / "infra")
    root = platform(tmp_path / "platform", repo.git("rev-parse", "HEAD"))
    _, incus, _ = tree(tmp_path / "incus-tree")
    source = tmp_path / "incus-tree" / "root" / incus_api.SOURCE
    (root / incus_api.DIRECTORY).mkdir(parents=True)
    (root / incus_api.SOURCE).write_bytes(source.read_bytes())
    with_tools(root)
    for kind in ("schemas", "golden-images", "incus-api"):
        assert generated.write(root, kind, repo.path, incus).status == "passed"
    monkeypatch.setattr(cli, "ROOT", root)
    assert cli.main(["generated", "--infra", str(repo.path), "--incus", str(incus)]) == 0
    assert capsys.readouterr().out == f"{FILES}\n{MATCHES}\n"
    assert cli.main(["generated", "--infra", str(repo.path)]) == 0
    assert capsys.readouterr() == (f"{FILES}\n{NOT_COMPARED}\n", "incus-extract: skipped\n")
    # A changed schema fails the command without Incus's files, as it did before them.
    (root / generated.SCHEMAS / "scenario.schema.json").write_text("{}\n", encoding="utf-8")
    assert cli.main(["generated", "--infra", str(repo.path)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        f"{SCHEMA}: differs from a fresh generation (make schemas)",
        NOT_COMPARED,
    ]


RECORDER = "import json, sys\njson.dump(sys.argv[2:], open(sys.argv[1], 'w'))\n"


def make(tmp_path: Path, *arguments: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run a make target of platform/ with a recorder as the check; no variable is inherited."""
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    record.unlink(missing_ok=True)
    command = ["make", "-s", "-C", str(ROOT / "platform"), *arguments]
    check = f"CHECK={sys.executable} {script} {record}"
    environ = {"PATH": os.environ["PATH"]}
    done = subprocess.run(
        [*command, check], env=environ, capture_output=True, text=True, check=False
    )
    return done, record


@pytest.mark.parametrize(
    ("target", "argv"),
    [
        ("incus-sources", ["incus-sources", "/incus"]),
        ("incus-api", ["generated", "--write", "incus-api", "--incus", "/incus"]),
    ],
)
def test_the_targets_that_fetch_and_write_need_the_directory_of_incus_s_files(
    tmp_path: Path, target: str, argv: list[str]
) -> None:
    done, record = make(tmp_path, target, "INFRA=/infra")
    assert done.returncode == 2
    assert done.stdout == (
        f"{target}: INCUS=<path of the directory of the two files of Incus's repository>\n"
    )
    assert not record.exists()
    done, record = make(tmp_path, target, "INFRA=/infra", "INCUS=/incus")
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == argv


def test_make_generated_runs_without_incus_s_files_and_passes_them_on_when_given(
    tmp_path: Path,
) -> None:
    done, record = make(tmp_path, "generated", "INFRA=/infra")
    assert (done.returncode, done.stdout) == (0, ""), done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == ["generated", "--infra", "/infra"]
    done, record = make(tmp_path, "generated", "INFRA=/infra", "INCUS=/incus sources")
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == [
        "generated",
        "--infra",
        "/infra",
        "--incus",
        "/incus sources",
    ]
    # In one make with the target that fetches them, a missing INCUS stops before anything runs.
    done, record = make(tmp_path, "incus-sources", "generated", "INFRA=/infra")
    assert done.returncode == 2
    assert not record.exists()


def test_make_gate_passes_the_directory_of_incus_s_files_on_when_given(tmp_path: Path) -> None:
    done, record = make(tmp_path, "gate", "INFRA=/infra", "INCUS=/incus", "UV=uv")
    assert done.returncode == 0, done.stderr
    argv = json.loads(record.read_text(encoding="utf-8"))
    assert argv[:5] == ["gate", "--infra", "/infra", "--incus", "/incus"]
    done, record = make(tmp_path, "gate", "INFRA=/infra", "UV=uv")
    assert done.returncode == 0, done.stderr
    assert "--incus" not in json.loads(record.read_text(encoding="utf-8"))
