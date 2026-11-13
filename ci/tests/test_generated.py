from __future__ import annotations

import json
from pathlib import Path

import pytest

from potka_ci import __main__ as cli
from potka_ci import generated

ROOT = Path(__file__).resolve().parents[2]
NAMES = [
    "blueprint.schema.json",
    "ground_truth.schema.json",
    "scenario.schema.json",
    "taxonomy.schema.json",
]


def written(root: Path) -> Path:
    outcome = generated.run(root, write=True)
    assert outcome.status == "passed"
    assert outcome.lines == [f"wrote platform/schemas/catalog/{name}" for name in NAMES]
    return root / generated.SCHEMAS


def test_the_committed_files_match_a_fresh_generation() -> None:
    outcome = generated.run(ROOT)
    assert outcome.lines == ["4 generated file(s) match a fresh generation"]
    assert outcome.status == "passed"
    assert sorted(p.name for p in (ROOT / generated.SCHEMAS).iterdir()) == NAMES


def test_a_schema_file_is_sorted_indented_json_with_a_final_newline(tmp_path: Path) -> None:
    directory = written(tmp_path)
    assert sorted(p.name for p in directory.iterdir()) == NAMES
    for name in NAMES:
        text = (directory / name).read_text(encoding="utf-8")
        schema = json.loads(text)
        assert text == json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
    assert generated.run(tmp_path).status == "passed"


def test_a_changed_a_missing_and_an_unknown_file_fail(tmp_path: Path) -> None:
    directory = written(tmp_path)
    scenario = directory / "scenario.schema.json"
    scenario.write_text(scenario.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    taxonomy = directory / "taxonomy.schema.json"
    taxonomy.write_text(
        taxonomy.read_text(encoding="utf-8").replace('"labels"', '"names"'), encoding="utf-8"
    )
    (directory / "blueprint.schema.json").unlink()
    (directory / "old.schema.json").write_text("{}\n", encoding="utf-8")
    (directory / "notes").mkdir()
    outcome = generated.run(tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        "platform/schemas/catalog/blueprint.schema.json: missing (make schemas writes it)",
        "platform/schemas/catalog/scenario.schema.json: differs from a fresh generation"
        " (make schemas)",
        "platform/schemas/catalog/taxonomy.schema.json: differs from a fresh generation"
        " (make schemas)",
        "platform/schemas/catalog/notes: no generator writes it: remove it",
        "platform/schemas/catalog/old.schema.json: no generator writes it: remove it",
    ]


def test_a_file_that_cannot_be_read_fails_with_its_name(tmp_path: Path) -> None:
    directory = written(tmp_path)
    (directory / "scenario.schema.json").unlink()
    (directory / "scenario.schema.json").mkdir()
    outcome = generated.run(tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        "platform/schemas/catalog/scenario.schema.json: cannot be read (IsADirectoryError)"
    ]
    for path in sorted(directory.iterdir(), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()
    directory.write_text("no directory\n", encoding="utf-8")
    outcome = generated.run(tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"platform/schemas/catalog/{name}: cannot be read (NotADirectoryError)" for name in NAMES
    ]


def test_without_the_directory_every_file_is_missing(tmp_path: Path) -> None:
    outcome = generated.run(tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"platform/schemas/catalog/{name}: missing (make schemas writes it)" for name in NAMES
    ]


def test_the_command_line_checks_and_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    assert cli.main(["generated"]) == 1
    assert capsys.readouterr().out.count("missing (make schemas writes it)") == 4
    assert cli.main(["generated", "--write"]) == 0
    assert capsys.readouterr().out.count("wrote platform/schemas/catalog/") == 4
    assert cli.main(["generated"]) == 0
