from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from golden import infra, platform

from potka_ci import __main__ as cli
from potka_ci import generated
from potka_ci.outcome import Outcome

ROOT = Path(__file__).resolve().parents[2]
NAMES = [
    "blueprint.schema.json",
    "ground_truth.schema.json",
    "scenario.schema.json",
    "taxonomy.schema.json",
]
REGISTRY = "platform/schemas/tools/registry.schema.json"
MODEL_TOOLS = "platform/schemas/tools/model-tools.json"
POLICY_TOOLS = "platform/policy/tools/data.json"
POLICY_REGISTRY = "platform/policy/registry/data.json"
TOOLS_PAGE = "platform/docs/tools.md"
COMPILED = [MODEL_TOOLS, POLICY_TOOLS, POLICY_REGISTRY, TOOLS_PAGE]
TABLE = "platform/infra-data/golden-images.json"
NO_CHECKOUT = "no pokus-infrastructure checkout (INFRA=<path>)"


def written(root: Path) -> Path:
    outcome = generated.write(root, "schemas")
    assert outcome.status == "passed"
    assert outcome.lines == [
        *(f"wrote platform/schemas/catalog/{name}" for name in NAMES),
        f"wrote {REGISTRY}",
    ]
    shutil.copytree(ROOT / generated.REGISTRY, root / generated.REGISTRY)
    outcome = generated.write(root, "tools")
    assert (outcome.status, outcome.lines) == ("passed", [f"wrote {name}" for name in COMPILED])
    return root / generated.SCHEMAS


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A working tree with its schemas written, and a synthetic checkout at its pin."""
    repo = infra(tmp_path / "infra")
    root = platform(tmp_path / "platform", repo.git("rev-parse", "HEAD"))
    written(root)
    return root, repo.path


def test_the_committed_files_match_a_fresh_generation(infra_checkout: Path) -> None:
    outcome = generated.run(ROOT, infra_checkout)
    assert outcome.lines == ["10 generated file(s) match a fresh generation"]
    assert outcome.status == "passed"
    assert sorted(p.name for p in (ROOT / generated.SCHEMAS).iterdir()) == NAMES
    assert sorted(p.name for p in (ROOT / generated.TOOL_SCHEMAS).iterdir()) == [
        Path(MODEL_TOOLS).name,
        Path(REGISTRY).name,
    ]
    for name in (POLICY_TOOLS, POLICY_REGISTRY):
        assert [p.name for p in (ROOT / name).parent.iterdir()] == ["data.json"]
    assert [generated.TARGETS[Path(name)] for name in COMPILED] == ["tools"] * 4
    assert [p.name for p in (ROOT / "platform" / "infra-data").iterdir()] == ["golden-images.json"]


def test_without_a_checkout_the_schemas_are_compared_and_the_check_is_skipped() -> None:
    outcome = generated.run(ROOT)
    assert outcome.status == "skipped"
    assert outcome.lines == [
        f"9 generated file(s) match a fresh generation; {TABLE} was not compared: {NO_CHECKOUT}"
    ]


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
    # The tool registry's schema has the same form; a registry file is a list of entries.
    text = (tmp_path / REGISTRY).read_text(encoding="utf-8")
    schema = json.loads(text)
    assert text == json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert (schema["$schema"], schema["type"]) == (
        "https://json-schema.org/draft/2020-12/schema",
        "array",
    )
    assert schema["$defs"]["Tool"]["additionalProperties"] is False
    # What the registry is compiled to: three JSON files of that form, and the page.
    for name in COMPILED[:3]:
        text = (tmp_path / name).read_text(encoding="utf-8")
        loaded = json.loads(text)
        assert text == json.dumps(loaded, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert [one["type"] for one in json.loads((tmp_path / MODEL_TOOLS).read_text())] == [
        "function"
    ] * 36
    assert len(json.loads((tmp_path / POLICY_TOOLS).read_text())) == 41
    assert sorted(json.loads((tmp_path / POLICY_REGISTRY).read_text())) == ["hash", "version"]
    assert (tmp_path / TOOLS_PAGE).read_text(encoding="utf-8").startswith("---\ntitle: ")
    assert generated.run(tmp_path).status == "skipped"


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
    registry = tmp_path / REGISTRY
    registry.write_text(registry.read_text(encoding="utf-8").replace("Tool", "Entry"))
    (registry.parent / "old.json").write_text("{}\n", encoding="utf-8")
    page = tmp_path / TOOLS_PAGE
    page.write_text(page.read_text(encoding="utf-8") + "A line by hand.\n", encoding="utf-8")
    (tmp_path / POLICY_REGISTRY).unlink()
    (tmp_path / POLICY_REGISTRY).parent.joinpath("policy.rego").write_text("", encoding="utf-8")
    (tmp_path / POLICY_TOOLS).parent.joinpath("more").mkdir()
    outcome = generated.run(tmp_path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        "platform/schemas/catalog/blueprint.schema.json: missing (make schemas writes it)",
        "platform/schemas/catalog/scenario.schema.json: differs from a fresh generation"
        " (make schemas)",
        "platform/schemas/catalog/taxonomy.schema.json: differs from a fresh generation"
        " (make schemas)",
        f"{REGISTRY}: differs from a fresh generation (make schemas)",
        f"{POLICY_REGISTRY}: missing (make tools writes it)",
        f"{TOOLS_PAGE}: differs from a fresh generation (make tools)",
        "platform/schemas/catalog/notes: no generator writes it: remove it",
        "platform/schemas/catalog/old.schema.json: no generator writes it: remove it",
        "platform/schemas/tools/old.json: no generator writes it: remove it",
        "platform/policy/tools/more: no generator writes it: remove it",
        "platform/policy/registry/policy.rego: no generator writes it: remove it",
    ]


def test_a_registry_that_cannot_be_loaded_fails_with_its_problems(tmp_path: Path) -> None:
    written(tmp_path)
    registry = tmp_path / generated.REGISTRY
    (registry / "VERSION").write_text("0\n", encoding="utf-8")
    (registry / "notes.txt").write_text("\n", encoding="utf-8")
    said = (
        "platform/tools/registry: VERSION: not one positive integer;"
        " notes.txt: neither VERSION nor a .yaml file"
    )
    outcome = generated.run(tmp_path)
    assert (outcome.status, outcome.lines) == ("failed", [said])
    # Its compiled files cannot be compared then, and a missing one is still missed.
    (tmp_path / MODEL_TOOLS).unlink()
    assert generated.run(tmp_path).lines == [said, f"{MODEL_TOOLS}: missing (make tools writes it)"]
    outcome = generated.write(tmp_path, "tools")
    assert (outcome.status, outcome.lines) == ("failed", [said])
    assert not (tmp_path / MODEL_TOOLS).exists()
    # A registry that changes changes what it is compiled to.
    (registry / "VERSION").write_text("2\n", encoding="utf-8")
    (registry / "notes.txt").unlink()
    assert generated.write(tmp_path, "tools").status == "passed"
    assert json.loads((tmp_path / POLICY_REGISTRY).read_text())["version"] == 2
    (registry / "VERSION").write_text("3\n", encoding="utf-8")
    assert generated.run(tmp_path).lines == [
        f"{POLICY_REGISTRY}: differs from a fresh generation (make tools)",
        f"{TOOLS_PAGE}: differs from a fresh generation (make tools)",
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
        "platform/tools/registry: .: cannot be read (FileNotFoundError)",
        *(f"platform/schemas/catalog/{name}: missing (make schemas writes it)" for name in NAMES),
        f"{REGISTRY}: missing (make schemas writes it)",
        *(f"{name}: missing (make tools writes it)" for name in COMPILED),
    ]


def test_the_table_is_written_and_compared_with_the_checkout(checkout: tuple[Path, Path]) -> None:
    root, repo = checkout
    outcome = generated.run(root, repo)
    assert outcome.status == "failed"
    assert outcome.lines == [f"{TABLE}: missing (make golden-images writes it)"]
    outcome = generated.write(root, "golden-images", repo)
    assert (outcome.status, outcome.lines) == ("passed", [f"wrote {TABLE}"])
    table = json.loads((root / TABLE).read_text(encoding="utf-8"))
    assert sorted(table["blueprints"]) == ["leaf", "middle", "os-root", "sibling"]
    outcome = generated.run(root, repo)
    assert (outcome.status, outcome.lines) == (
        "passed",
        ["10 generated file(s) match a fresh generation"],
    )
    assert generated.run(root).status == "skipped"


def test_a_changed_table_and_another_file_beside_it_fail(checkout: tuple[Path, Path]) -> None:
    root, repo = checkout
    generated.write(root, "golden-images", repo)
    table = root / TABLE
    table.write_text(table.read_text().replace('"nesting": true', '"nesting": false'))
    (table.parent / "notes.txt").write_text("x")
    outcome = generated.run(root, repo)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"{TABLE}: differs from a fresh generation (make golden-images)",
        "platform/infra-data/notes.txt: no generator writes it: remove it",
    ]
    # Without a checkout a stale table is not noticed, but a stray file beside it is.
    outcome = generated.run(root)
    assert outcome.status == "failed"
    assert outcome.lines == ["platform/infra-data/notes.txt: no generator writes it: remove it"]


def test_a_checkout_that_makes_no_table_fails(checkout: tuple[Path, Path]) -> None:
    root, repo = checkout
    generated.write(root, "golden-images", repo)
    (root / "INFRA_PIN").write_text("f" * 40 + "\n")
    outcome = generated.run(root, repo)
    assert outcome.status == "failed"
    assert len(outcome.lines) == 1
    assert outcome.lines[0].startswith(f"{TABLE}: the pokus-infrastructure checkout is at ")
    assert outcome.lines[0].endswith(f", not at INFRA_PIN {'f' * 40}")
    outcome = generated.write(root, "golden-images", repo)
    assert outcome.status == "failed"
    assert outcome.lines[0].startswith("the pokus-infrastructure checkout is at ")
    outcome = generated.write(root, "golden-images")
    assert (outcome.status, outcome.lines) == ("skipped", [NO_CHECKOUT])


def test_the_command_line_checks_and_writes(
    checkout: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, repo = checkout
    monkeypatch.setattr(cli, "ROOT", root)
    # The extract's own check is not this test's: here it finds nothing and says nothing.
    monkeypatch.setattr(generated, "extract", lambda root, incus=None: Outcome("passed"))
    assert cli.main(["generated"]) == 2
    out, err = capsys.readouterr()
    assert out.startswith("9 generated file(s) match a fresh generation; ")
    assert err == "generated: skipped\n"
    (root / generated.SCHEMAS / "scenario.schema.json").unlink()
    assert cli.main(["generated", "--infra", str(repo)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "platform/schemas/catalog/scenario.schema.json: missing (make schemas writes it)",
        f"{TABLE}: missing (make golden-images writes it)",
    ]
    assert cli.main(["generated", "--write", "golden-images"]) == 2
    assert capsys.readouterr().out == NO_CHECKOUT + "\n"
    assert cli.main(["generated", "--write", "golden-images", "--infra", str(repo)]) == 0
    assert capsys.readouterr().out == f"wrote {TABLE}\n"
    assert cli.main(["generated", "--write", "schemas"]) == 0
    assert capsys.readouterr().out.count("wrote platform/schemas/catalog/") == 4
    (root / TOOLS_PAGE).unlink()
    assert cli.main(["generated", "--write", "tools"]) == 0
    assert capsys.readouterr().out == "".join(f"wrote {name}\n" for name in COMPILED)
    assert cli.main(["generated", "--infra", str(repo)]) == 0
    assert capsys.readouterr().out == "10 generated file(s) match a fresh generation\n"
    with pytest.raises(SystemExit):
        cli.main(["generated", "--write", "everything"])


RECORDER = "import json, sys\njson.dump(sys.argv[2:], open(sys.argv[1], 'w'))\n"


def test_make_tools_writes_what_the_registry_is_compiled_to(tmp_path: Path) -> None:
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    inherited = ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "INFRA", "INCUS")
    environ = {name: value for name, value in os.environ.items() if name not in inherited}
    make = ["make", "-s", "-C", str(ROOT / "platform"), "tools"]
    check = f"CHECK={sys.executable} {script} {record}"
    done = subprocess.run([*make, check], env=environ, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == ["generated", "--write", "tools"]


@pytest.mark.parametrize(
    ("target", "argv"),
    [
        ("generated", ["generated", "--infra", "/infra"]),
        ("golden-images", ["generated", "--write", "golden-images", "--infra", "/infra"]),
    ],
)
def test_the_make_targets_refuse_to_run_without_the_checkout(
    tmp_path: Path, target: str, argv: list[str]
) -> None:
    # Without INFRA the check would be skipped, and a skip must never look like a pass.
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    make = ["make", "-s", "-C", str(ROOT / "platform"), target]
    check = f"CHECK={sys.executable} {script} {record}"
    # A make that runs these tests (make test INFRA=..., the gate) hands its own INFRA
    # down through the environment: the make started here must not inherit it.
    inherited = ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "MAKELEVEL", "INFRA", "INCUS")
    environ = {name: value for name, value in os.environ.items() if name not in inherited}
    done = subprocess.run([*make, check], env=environ, capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert done.stdout == (
        f"{target}: INFRA=<path of the pokus-infrastructure checkout at INFRA_PIN>\n"
    )
    assert not record.exists()
    done = subprocess.run(
        [*make, check, "INFRA=/infra"], env=environ, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text(encoding="utf-8")) == argv
