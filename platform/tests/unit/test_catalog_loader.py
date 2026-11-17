"""Reading a catalog checkout: the YAML subset, the layout and every file's problems."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from potka_core.catalog.loader import Catalog, CatalogError, load_catalog, parse_yaml
from potka_testing.catalog import scenario_data
from potka_testing.catalog_tree import catalog_files, write_catalog


def problems(root: Path) -> list[tuple[str, str]]:
    with pytest.raises(CatalogError) as caught:
        load_catalog(root)
    listed = "; ".join(f"{path}: {reason}" for path, reason in caught.value.problems)
    assert str(caught.value) == listed
    return list(caught.value.problems)


def changed(root: Path, name: str, change: Any) -> None:
    data = yaml.safe_load((root / name).read_text())
    change(data)
    (root / name).write_text(yaml.safe_dump(data, sort_keys=False))


def test_a_catalog_loads(tmp_path: Path) -> None:
    catalog = load_catalog(write_catalog(tmp_path))
    assert isinstance(catalog, Catalog)
    assert catalog.root == tmp_path.resolve()
    assert catalog.taxonomy.version == 1
    assert sorted(catalog.blueprints) == ["fixture-web"]
    assert catalog.blueprints["fixture-web"].nodes[0].alias == "web"
    assert sorted(catalog.overlays) == ["fixture-web-jobs"]
    assert catalog.overlays["fixture-web-jobs"].files == ("etc/fixture/report-builder.conf",)
    assert catalog.overlays["fixture-web-jobs"].install is True
    assert [(v.path, v.scenario.id, v.scenario.version) for v in catalog.scenarios] == [
        ("scenarios/FIXTURE-001/2", "FIXTURE-001", 2),
        ("scenarios/FIXTURE-CONTROL-000/1", "FIXTURE-CONTROL-000", 1),
    ]
    assert catalog.scenarios[0].ground_truth.fault.category == "fixture.config.syntax_error"
    assert catalog.scenarios[1].ground_truth.fault.category == "none"


def test_scenario_versions_are_ordered_by_id_and_number(tmp_path: Path) -> None:
    files = catalog_files()
    for version in (10, 9, 1):
        for name in ("scenario.yaml", "ground_truth.yaml"):
            data = dict(files[f"scenarios/FIXTURE-001/2/{name}"])
            if name == "scenario.yaml":
                data["version"] = version
            files[f"scenarios/FIXTURE-001/{version}/{name}"] = data
    catalog = load_catalog(write_catalog(tmp_path, files))
    assert [(v.scenario.id, v.scenario.version) for v in catalog.scenarios] == [
        ("FIXTURE-001", 1),
        ("FIXTURE-001", 2),
        ("FIXTURE-001", 9),
        ("FIXTURE-001", 10),
        ("FIXTURE-CONTROL-000", 1),
    ]


def test_a_catalog_of_a_taxonomy_alone_loads_and_other_root_entries_are_not_read(
    tmp_path: Path,
) -> None:
    files = {"taxonomy.yaml": catalog_files()["taxonomy.yaml"], "README.md": "not: [yaml"}
    write_catalog(tmp_path, files)
    (tmp_path / ".git").mkdir()
    (tmp_path / "notes").symlink_to("/nonexistent")
    catalog = load_catalog(tmp_path)
    assert (catalog.blueprints, catalog.overlays, catalog.scenarios) == ({}, {}, ())


def test_the_root_may_be_a_link_to_the_catalog(tmp_path: Path) -> None:
    write_catalog(tmp_path / "v1")
    (tmp_path / "current").symlink_to("v1")
    assert load_catalog(tmp_path / "current").root == (tmp_path / "v1").resolve()


def test_what_is_not_a_catalog_directory_is_refused(tmp_path: Path) -> None:
    assert problems(tmp_path / "missing") == [(".", "missing")]
    (tmp_path / "dangling").symlink_to(tmp_path / "missing")
    assert problems(tmp_path / "dangling") == [(".", "missing")]
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    assert problems(tmp_path / "loop") == [(".", "cannot be read (RuntimeError)")]
    (tmp_path / "file").write_text("x")
    assert problems(tmp_path / "file") == [(".", "not a directory")]
    (tmp_path / "empty").mkdir()
    assert problems(tmp_path / "empty") == [("taxonomy.yaml", "missing")]


def test_every_malformed_file_is_named_with_the_place(tmp_path: Path) -> None:
    root = write_catalog(tmp_path)
    changed(root, "taxonomy.yaml", lambda d: d["labels"].pop(0))
    changed(root, "blueprints/fixture-web.yaml", lambda d: d["nodes"][0].update(alias="Web"))
    changed(
        root,
        "scenarios/FIXTURE-001/2/scenario.yaml",
        lambda d: (d.update(split="s3cret"), d["inject"][0].update(primitive="s3cret")),
    )
    changed(
        root,
        "scenarios/FIXTURE-001/2/ground_truth.yaml",
        lambda d: d["fault"].update(location="s3cret $"),
    )
    (root / "scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml").write_text("fault: [s3cret\n")
    found = problems(root)
    assert found == [
        ("taxonomy.yaml", "Value error, the label none is missing"),
        (
            "blueprints/fixture-web.yaml",
            "nodes.0.alias: String should match pattern '^[a-z][a-z0-9]{0,14}$'",
        ),
        (
            "scenarios/FIXTURE-001/2/scenario.yaml",
            "split: Input should be 'train', 'dev' or 'test'",
        ),
        (
            "scenarios/FIXTURE-001/2/scenario.yaml",
            "inject.0.primitive: String should match pattern '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$'",
        ),
        (
            "scenarios/FIXTURE-001/2/ground_truth.yaml",
            "fault.location: Value error, the '$' at character 8 is neither '$$' nor the start"
            " of '${name}'",
        ),
        (
            "scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml",
            "not YAML of the catalog's subset: a syntax error (line 2, column 1)",
        ),
    ]
    assert "s3cret" not in str(found)


@pytest.mark.parametrize(
    ("text", "data"),
    [
        ("a: 1\nb: -2\nc: 1.5\nd: 1e3\ne: -0.5E-2\nf: 0\n", [1, -2, 1.5, 1000.0, -0.005, 0]),
        ("a: true\nb: false\nc: null\nd: ~\ne:\n", [True, False, None, None, None]),
        (
            "a: yes\nb: No\nc: on\nd: OFF\ne: True\nf: FALSE\ng: Null\nh: y\n",
            ["yes", "No", "on", "OFF", "True", "FALSE", "Null", "y"],
        ),
        (
            "a: 0x10\nb: 0o17\nc: 017\nd: 1_000\ne: +1\nf: 1:30\ng: .5\nh: 1.\ni: 0b11\n",
            ["0x10", "0o17", "017", "1_000", "+1", "1:30", ".5", "1.", "0b11"],
        ),
        ("a: -0\nb: -0.0\nc: 0e0\nd: -7\n", [0, -0.0, 0.0, -7]),
        ("a: 2026-11-08\nb: 2026-11-08T10:00:00Z\nc: .inf\nd: .NaN\ne: -.inf\n", None),
        ("a: '1'\nb: \"true\"\nc: '~'\nd: ''\n", ["1", "true", "~", ""]),
        ("a: |\n  two\n  lines\nb: >\n  one\n  line\n", ["two\nlines\n", "one line\n"]),
        ("a: &anchor [1, 2]\nb: {<<: 1}\n", [[1, 2], {"<<": 1}]),
        ("a: 123456789012345678901234567890\n", [123456789012345678901234567890]),
    ],
)
def test_a_plain_scalar_is_json_or_a_string(text: str, data: list[Any] | None) -> None:
    parsed = parse_yaml(text)
    assert isinstance(parsed, dict)
    if data is None:
        assert all(type(value) is str for value in parsed.values())
    else:
        assert list(parsed.values()) == data
        assert [type(value) for value in parsed.values()] == [type(value) for value in data]


def test_a_document_of_one_scalar_or_none() -> None:
    assert parse_yaml("") is None
    assert parse_yaml("# a comment only\n") is None
    assert parse_yaml("text") == "text"
    assert parse_yaml("- 1\n- [a, {b: 2}]\n") == [1, ["a", {"b": 2}]]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("a: 1\na: 2\n", "a key given twice (line 2, column 1)"),
        ("x:\n  - {a: 1, b: 2, a: 3}\n", "a key given twice (line 2, column 18)"),
        ("1: a\n", "a key that is not a string (line 1, column 1)"),
        ("true: a\n", "a key that is not a string (line 1, column 1)"),
        ("~: a\n", "a key that is not a string (line 1, column 1)"),
        ("? [a, b]\n: c\n", "a key that is not a string (line 1, column 3)"),
        ("a: &x [1]\nb: *x\n", "an alias (line 2, column 4)"),
        ("a: !!python/object/apply:os.system [x]\n", "a tag (line 1, column 4)"),
        ("a: !!binary aGk=\n", "a tag (line 1, column 4)"),
        ("a: !!str 1\n", "a tag (line 1, column 4)"),
        ("a: !!set {x, y}\n", "a tag (line 1, column 4)"),
        ("a: !custom x\n", "a tag (line 1, column 4)"),
        ("a: ! x\n", "a tag (line 1, column 4)"),
        ("!!map {a: 1}\n", "a tag (line 1, column 1)"),
        ("a: 1\n---\nb: 2\n", "more than one document (line 2, column 1)"),
        ("a: [1\n", "a syntax error (line 2, column 1)"),
        ("a:\n\t- 1\n", "a syntax error (line 2, column 1)"),
        ("a: @b\n", "a syntax error (line 1, column 4)"),
        ("a: !s3cret!x value\n", "a syntax error (line 1, column 4)"),
        ("a: &x 1\nb: &x 2\n", "a syntax error (line 2, column 4)"),
        ("a: 1\n\x01b: 2\n", "a syntax error"),
        ('a: 1\n"a": 2\n', "a key given twice (line 2, column 1)"),
        ("a: " + "9" * 5000 + "\n", "a number too large"),
        ("a: 1e999\n", "a number too large"),
        ("a: -1e999\n", "a number too large"),
        ("a: " + "[" * 2000 + "]" * 2000 + "\n", "nested too deeply"),
    ],
)
def test_yaml_outside_the_subset_is_refused_with_its_place(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=r"^not YAML of the catalog's subset: ") as caught:
        parse_yaml(text)
    assert str(caught.value) == f"not YAML of the catalog's subset: {reason}"


def test_a_yaml_error_does_not_show_the_text() -> None:
    for text in (
        "s3cret: [s3cret\n",
        "s3cret: 1\ns3cret: 2\n",
        "a: *s3cret\n",
        "a: !s3cret x\n",
        "a: !s3cret!x y\n",
        "a: &s3cret 1\nb: &s3cret 2\n",
        "a: 's3cret\n",
        "s3cret\n: [\n",
    ):
        with pytest.raises(ValueError, match="not YAML") as caught:
            parse_yaml(text)
        assert "s3cret" not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__


def test_what_cannot_be_read_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = write_catalog(tmp_path).resolve()
    read_text, listdir, scandir = Path.read_text, os.listdir, os.scandir

    def refuse(wrapped: Any, *denied: str) -> Any:
        def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
            if os.path.relpath(path, root) in denied:
                raise PermissionError(13, "Permission denied", str(path))
            return wrapped(path, *args, **kwargs)

        return guarded

    monkeypatch.setattr(Path, "read_text", refuse(read_text, "taxonomy.yaml"))
    monkeypatch.setattr(os, "listdir", refuse(listdir, "scenarios/FIXTURE-001", "blueprints"))
    assert problems(root) == [
        ("taxonomy.yaml", "cannot be read (PermissionError)"),
        ("blueprints", "cannot be read (PermissionError)"),
        ("scenarios/FIXTURE-001", "cannot be read (PermissionError)"),
    ]
    monkeypatch.setattr(os, "listdir", refuse(listdir, "blueprints/fixture-web-jobs"))
    monkeypatch.setattr(Path, "read_text", read_text)
    assert problems(root) == [("blueprints/fixture-web-jobs", "cannot be read (PermissionError)")]
    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(
        os, "scandir", refuse(scandir, "blueprints/fixture-web-jobs/overlay/files/etc")
    )
    assert problems(root) == [
        ("blueprints/fixture-web-jobs/overlay/files/etc", "cannot be read (PermissionError)")
    ]
    monkeypatch.setattr(os, "scandir", scandir)
    lstat = os.lstat
    denied = (
        "taxonomy.yaml",
        "blueprints/fixture-web-jobs",
        "blueprints/fixture-web.yaml",
        "scenarios/FIXTURE-001",
        "scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml",
    )
    monkeypatch.setattr(os, "lstat", refuse(lstat, *denied))
    assert problems(root) == [(name, "cannot be read (PermissionError)") for name in denied]
    monkeypatch.setattr(os, "lstat", refuse(lstat, "blueprints", "scenarios"))
    assert problems(root) == [
        ("blueprints", "cannot be read (PermissionError)"),
        ("scenarios", "cannot be read (PermissionError)"),
    ]


def test_an_entry_whose_name_cannot_be_printed_is_refused(tmp_path: Path) -> None:
    root = write_catalog(tmp_path)
    (root / "blueprints" / "two\nlines.yaml").write_text("x")
    (root / "scenarios" / "FIXTURE-\t01").mkdir()
    (root / "scenarios" / "FIXTURE-001" / "2\x1b").mkdir()
    os.mkdir(os.fsencode(root / "scenarios") + b"/FIXTURE-\xff01")
    with open(os.fsencode(root / "blueprints") + b"/latin-\xe9.yaml", "w") as handle:
        handle.write("x")
    found = problems(root)
    assert found == [
        ("blueprints/latin-?.yaml", "a name that is not printable UTF-8"),
        ("blueprints/two?lines.yaml", "a name that is not printable UTF-8"),
        ("scenarios/FIXTURE-?01", "a name that is not printable UTF-8"),
        ("scenarios/FIXTURE-?01", "a name that is not printable UTF-8"),
        ("scenarios/FIXTURE-001/2?", "a name that is not printable UTF-8"),
    ]
    assert all(path.isprintable() and path.encode() for path, _ in found)


def test_a_root_path_that_cannot_be_a_path_is_refused() -> None:
    assert problems(Path("no\x00catalog")) == [(".", "cannot be read (ValueError)")]


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    root = write_catalog(tmp_path)
    (root / "taxonomy.yaml").write_bytes(b"version: 1\nlabels: [\xff]\n")
    assert problems(root) == [("taxonomy.yaml", "not UTF-8")]


def test_a_misnamed_file_or_directory_is_refused(tmp_path: Path) -> None:
    files = catalog_files()
    files["blueprints/other.yaml"] = files["blueprints/fixture-web.yaml"]
    files["scenarios/FIXTURE-002/2/scenario.yaml"] = scenario_data()
    files["scenarios/FIXTURE-002/2/ground_truth.yaml"] = files[
        "scenarios/FIXTURE-001/2/ground_truth.yaml"
    ]
    files["scenarios/FIXTURE-001/3/scenario.yaml"] = scenario_data()
    files["scenarios/FIXTURE-001/3/ground_truth.yaml"] = files[
        "scenarios/FIXTURE-001/2/ground_truth.yaml"
    ]
    for version in ("02", "v2", "0", "2.0"):
        files[f"scenarios/FIXTURE-001/{version}/scenario.yaml"] = scenario_data()
    assert problems(write_catalog(tmp_path, files)) == [
        ("blueprints/other.yaml", "the file of the blueprint fixture-web has another name"),
        ("scenarios/FIXTURE-001/0", "a version's directory is named by a positive integer"),
        ("scenarios/FIXTURE-001/02", "a version's directory is named by a positive integer"),
        ("scenarios/FIXTURE-001/2.0", "a version's directory is named by a positive integer"),
        (
            "scenarios/FIXTURE-001/3/scenario.yaml",
            "the id and the version are not the names of the file's directories",
        ),
        ("scenarios/FIXTURE-001/v2", "a version's directory is named by a positive integer"),
        (
            "scenarios/FIXTURE-002/2/scenario.yaml",
            "the id and the version are not the names of the file's directories",
        ),
    ]


def test_every_unexpected_or_missing_entry_is_named(tmp_path: Path) -> None:
    files = catalog_files()
    files["blueprints/notes.txt"] = "x"
    files["blueprints/fixture-web.yml"] = files["blueprints/fixture-web.yaml"]
    files["blueprints/fixture-web-jobs/README.md"] = "x"
    files["blueprints/empty-derived/overlay/README.md"] = "x"
    files["scenarios/README.md"] = "x"
    files["scenarios/FIXTURE-001/notes.txt"] = "x"
    files["scenarios/FIXTURE-001/2/scenario.yml"] = "x"
    files["scenarios/FIXTURE-001/2/extra"] = "x"
    del files["scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml"]
    root = write_catalog(tmp_path, files)
    (root / "blueprints" / "no-overlay").mkdir()
    (root / "scenarios" / "FIXTURE-003" / "1").mkdir(parents=True)
    (root / "scenarios" / "FIXTURE-004").mkdir()
    assert problems(root) == [
        (
            "blueprints/empty-derived/overlay/README.md",
            "an overlay holds only files/ and install.sh",
        ),
        (
            "blueprints/fixture-web-jobs/README.md",
            "a derived image's directory holds overlay/ only",
        ),
        (
            "blueprints/fixture-web.yml",
            "neither a blueprint's .yaml file nor a derived image's directory",
        ),
        ("blueprints/no-overlay", "a derived image's directory without overlay/"),
        (
            "blueprints/notes.txt",
            "neither a blueprint's .yaml file nor a derived image's directory",
        ),
        ("scenarios/FIXTURE-001/2/extra", "not scenario.yaml or ground_truth.yaml"),
        ("scenarios/FIXTURE-001/2/scenario.yml", "not scenario.yaml or ground_truth.yaml"),
        ("scenarios/FIXTURE-001/notes.txt", "a file, not a directory"),
        ("scenarios/FIXTURE-003/1/scenario.yaml", "missing"),
        ("scenarios/FIXTURE-003/1/ground_truth.yaml", "missing"),
        ("scenarios/FIXTURE-004", "a scenario's directory without a version"),
        ("scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml", "missing"),
        ("scenarios/README.md", "a file, not a directory"),
    ]


def test_a_derived_images_directory_is_named_like_a_derived_image(tmp_path: Path) -> None:
    files = catalog_files()
    for name in ("Weird Name", "jobs_v2", "-jobs", "jobs-", "a" * 64):
        files[f"blueprints/{name}/overlay/install.sh"] = "true\n"
    files["blueprints/" + "a" * 63 + "/overlay/install.sh"] = "true\n"
    root = write_catalog(tmp_path, files)
    (root / "blueprints" / "empty").mkdir()
    assert problems(root) == [
        ("blueprints/-jobs", "not the name of a derived image"),
        ("blueprints/Weird Name", "not the name of a derived image"),
        ("blueprints/" + "a" * 64, "not the name of a derived image"),
        ("blueprints/empty", "a derived image's directory without overlay/"),
        ("blueprints/jobs-", "not the name of a derived image"),
        ("blueprints/jobs_v2", "not the name of a derived image"),
    ]


def test_no_link_below_the_root_is_followed(tmp_path: Path) -> None:
    outside = write_catalog(tmp_path / "outside")
    root = write_catalog(tmp_path / "catalog")
    for name in (
        "taxonomy.yaml",
        "blueprints/fixture-web.yaml",
        "scenarios/FIXTURE-001/2/ground_truth.yaml",
    ):
        (root / name).unlink()
        (root / name).symlink_to(outside / name)
    os.rename(root / "scenarios/FIXTURE-CONTROL-000", tmp_path / "moved")
    (root / "scenarios/FIXTURE-CONTROL-000").symlink_to(tmp_path / "moved")
    os.rename(root / "scenarios/FIXTURE-001/2/scenario.yaml", tmp_path / "s.yaml")
    (root / "scenarios/FIXTURE-001/2/scenario.yaml").symlink_to(tmp_path / "s.yaml")
    (root / "scenarios/FIXTURE-001/1").symlink_to(outside / "scenarios/FIXTURE-001/2")
    (root / "blueprints/linked").symlink_to(outside / "blueprints/fixture-web-jobs")
    (root / "blueprints/fixture-web-jobs/overlay/files/etc/passwd").symlink_to("/etc/passwd")
    (root / "blueprints/jobs-two").mkdir()
    (root / "blueprints/jobs-two/overlay").symlink_to(
        outside / "blueprints/fixture-web-jobs/overlay"
    )
    assert problems(root) == [
        ("taxonomy.yaml", "a link, not a file"),
        (
            "blueprints/fixture-web-jobs/overlay/files/etc/passwd",
            "a symbolic link, not a directory or a regular file",
        ),
        ("blueprints/fixture-web.yaml", "a link, not a file or a directory"),
        ("blueprints/jobs-two/overlay", "a symbolic link, not a directory"),
        ("blueprints/linked", "a link, not a file or a directory"),
        ("scenarios/FIXTURE-001/1", "a link, not a directory"),
        ("scenarios/FIXTURE-001/2/scenario.yaml", "a link, not a file"),
        ("scenarios/FIXTURE-001/2/ground_truth.yaml", "a link, not a file"),
        ("scenarios/FIXTURE-CONTROL-000", "a link, not a directory"),
    ]
    for top in ("blueprints", "scenarios"):
        linked = write_catalog(tmp_path / f"linked-{top}")
        os.rename(linked / top, tmp_path / f"real-{top}")
        (linked / top).symlink_to(tmp_path / f"real-{top}")
        assert problems(linked) == [(top, "a link, not a directory")]
