from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from golden import BLUEPRINTS, blueprint, infra, platform, put

from potka_ci import golden_images
from potka_ci.golden_images import TableError, generate, read_blueprints, render, table

EXPECTED = {
    "leaf": {
        "version": "10.0.1",
        "type": "container",
        "nesting": True,
        "base": {"blueprint": "middle"},
        "packages": ["app-server"],
        "effective_packages": ["app-server", "curl", "less", "nginx=1.24.0-2", "python3"],
    },
    "middle": {
        "version": "2.3.4",
        "type": "container",
        "nesting": False,
        "base": {"blueprint": "os-root"},
        "packages": ["nginx=1.24.0-2", "curl"],
        "effective_packages": ["curl", "less", "nginx=1.24.0-2", "python3"],
    },
    "os-root": {
        "version": "2.3.4",
        "type": "container",
        "nesting": False,
        "base": {"image": "some-os-24.04"},
        "packages": ["curl", "python3", "less"],
        "effective_packages": ["curl", "less", "python3"],
    },
    "sibling": {
        "version": "2.3.4",
        "type": "vm",
        "nesting": False,
        "base": {"blueprint": "os-root"},
        "packages": [],
        "effective_packages": ["curl", "less", "python3"],
    },
}


def problems(run: Any) -> list[str]:
    with pytest.raises(TableError) as caught:
        run()
    assert str(caught.value) == "; ".join(caught.value.problems)
    return caught.value.problems


def test_the_table_lists_every_blueprint_with_its_own_and_its_effective_packages(
    tmp_path: Path,
) -> None:
    repo = infra(tmp_path / "infra")
    head = repo.git("rev-parse", "HEAD")
    text = generate(platform(tmp_path / "platform", head), repo.path)
    assert json.loads(text) == {"infra_pin": head, "blueprints": EXPECTED}
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert table(read_blueprints(repo.path), "a" * 40)["infra_pin"] == "a" * 40


def test_the_blueprint_the_others_are_built_on_is_found_by_its_base_not_its_name(
    tmp_path: Path,
) -> None:
    renamed = [
        blueprint("zz-last", None, "bash"),
        blueprint("base", "zz-last", "vim"),
        blueprint("aa-first", "base"),
    ]
    made = table(read_blueprints(infra(tmp_path / "infra", renamed).path), "b" * 40)
    assert {name: entry["effective_packages"] for name, entry in made["blueprints"].items()} == {
        "aa-first": ["bash", "vim"],
        "base": ["bash", "vim"],
        "zz-last": ["bash"],
    }
    assert made["blueprints"]["zz-last"]["base"] == {"image": "some-os-24.04"}


def test_the_text_keeps_other_scripts_and_ends_with_a_newline() -> None:
    text = render({"infra_pin": "c" * 40, "blueprints": {"b": {"base": "süß-ось"}, "a": {}}})
    assert text == (
        '{\n  "blueprints": {\n    "a": {},\n    "b": {\n      "base": "süß-ось"\n    }\n  },\n'
        f'  "infra_pin": "{"c" * 40}"\n}}\n'
    )


def test_a_base_in_another_script_reaches_the_table_as_it_is_written(tmp_path: Path) -> None:
    named = [blueprint("root", None, base={"image": "система-24.04"}), blueprint("leaf", "root")]
    repo = infra(tmp_path / "infra", named)
    head = repo.git("rev-parse", "HEAD")
    text = generate(platform(tmp_path / "platform", head), repo.path)
    assert '"image": "система-24.04"' in text
    assert json.loads(text)["blueprints"]["root"]["base"] == {"image": "система-24.04"}


@pytest.mark.parametrize(
    ("blueprints", "expected"),
    [
        ([], ["no images/*/image.yaml in the checkout"]),
        (
            [blueprint("one", None), blueprint("two", None), blueprint("three", "one")],
            ["exactly one blueprint is built from an image, not 2: one, two"],
        ),
        (
            [blueprint("one", "two"), blueprint("two", "one")],
            ["exactly one blueprint is built from an image, not 0"],
        ),
        (
            [blueprint("root", None), blueprint("one", "two"), blueprint("two", "one")],
            [
                "one is built on itself: one on two on one",
                "two is built on itself: two on one on two",
            ],
        ),
        (
            [blueprint("root", None), blueprint("one", "one")],
            ["one is built on itself: one on one"],
        ),
        (
            [
                blueprint("root", None),
                blueprint("one", "two"),
                blueprint("two", "one"),
                blueprint("three", "one"),
                blueprint("four", "three"),
            ],
            [
                "one is built on itself: one on two on one",
                "two is built on itself: two on one on two",
            ],
        ),
        (
            [blueprint("root", None), blueprint("one", "gone"), blueprint("two", "one")],
            ["one is built on gone, which is no blueprint"],
        ),
        (
            [
                blueprint("root", None),
                blueprint("one", "gone"),
                blueprint("two", "lost"),
                blueprint("three", "two"),
            ],
            [
                "one is built on gone, which is no blueprint",
                "two is built on lost, which is no blueprint",
            ],
        ),
        (
            [blueprint("root", None, extra=1)],
            [
                "images/root/image.yaml: the file: Additional properties are not allowed"
                " ('extra' was unexpected)"
            ],
        ),
        (
            [blueprint("root", None, version="1.0"), blueprint("leaf", "root", nesting="yes")],
            [
                "images/leaf/image.yaml: nesting: 'yes' is not of type 'boolean'",
                "images/root/image.yaml: version: '1.0' does not match"
                " '^[0-9]+\\\\.[0-9]+\\\\.[0-9]+$'",
            ],
        ),
        (
            [blueprint("root", None, base={"image": "x", "blueprint": "y"})],
            None,
        ),
    ],
)
def test_blueprints_that_make_no_table_are_refused(
    tmp_path: Path, blueprints: list[dict[str, Any]], expected: list[str] | None
) -> None:
    repo = infra(tmp_path / "infra", blueprints)
    found = problems(lambda: table(read_blueprints(repo.path), "d" * 40))
    if expected is None:
        assert len(found) == 1 and found[0].startswith("images/root/image.yaml: base: ")
    else:
        assert found == expected


def test_a_blueprint_outside_its_own_directory_or_unreadable_is_refused(tmp_path: Path) -> None:
    repo = infra(tmp_path / "infra")
    put(repo.path, blueprint("other", "os-root"), directory="misplaced")
    (repo.path / "images" / "broken").mkdir()
    (repo.path / "images" / "broken" / "image.yaml").write_text("name: [\n")
    (repo.path / "images" / "binary").mkdir()
    (repo.path / "images" / "binary" / "image.yaml").write_bytes(b"\xff\xfe")
    (repo.path / "images" / "no-blueprint-here").mkdir()
    assert problems(lambda: read_blueprints(repo.path)) == [
        "images/binary/image.yaml cannot be read: UnicodeDecodeError",
        "images/broken/image.yaml cannot be read: ParserError",
        "images/misplaced/image.yaml: the blueprint other is not in its own directory",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "os-root\n"},
        {"version": "2.3.4\n"},
        {"packages": ["curl\n"]},
        {"base": {"image": "some-os\n"}},
        {"base": {"image": " some-os"}},
        {"base": {"image": "some-os "}},
        {"base": {"image": "\tsome-os"}},
        {"base": {"image": "some\u0007os"}},
        {"base": {"image": "some\nos"}},
        {"base": {"image": "some\u200bos"}},
    ],
)
def test_a_value_with_white_space_at_an_end_or_an_unprintable_character_is_refused(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    repo = infra(tmp_path / "infra", [blueprint("os-root", None, "curl") | changes])
    assert problems(lambda: read_blueprints(repo.path)) == [
        "images/os-root/image.yaml: a value with white space at an end or an unprintable one"
    ]


@pytest.mark.parametrize("below", [" os-root", "os-root\n", "os\u0007root"])
def test_a_base_blueprint_s_name_with_white_space_or_an_unprintable_character_is_refused(
    tmp_path: Path, below: str
) -> None:
    repo = infra(tmp_path / "infra", [blueprint("os-root", None), blueprint("leaf", below)])
    assert problems(lambda: read_blueprints(repo.path)) == [
        "images/leaf/image.yaml: a value with white space at an end or an unprintable one"
    ]


@pytest.mark.parametrize(
    ("schema", "reason"),
    [
        ({"properties": {"name": {"pattern": "["}}}, "'[' is not a 'regex'"),
        ({"type": 7}, "7 is not valid under any of the given schemas"),
        ([], "[] is not of type 'object', 'boolean'"),
    ],
)
def test_a_schema_that_is_no_json_schema_is_refused(
    tmp_path: Path, schema: Any, reason: str
) -> None:
    repo = infra(tmp_path / "infra")
    (repo.path / golden_images.SCHEMA).write_text(json.dumps(schema), encoding="utf-8")
    assert problems(lambda: read_blueprints(repo.path)) == [
        f"tools/lockcheck/schema/image-blueprint.schema.json is no JSON Schema: {reason}"
    ]


def test_without_the_schema_nothing_is_generated(tmp_path: Path) -> None:
    repo = infra(tmp_path / "infra")
    schema = repo.path / golden_images.SCHEMA
    schema.write_text("{", encoding="utf-8")
    assert problems(lambda: read_blueprints(repo.path))[0].startswith(
        "tools/lockcheck/schema/image-blueprint.schema.json cannot be read: "
    )
    schema.unlink()
    assert problems(lambda: read_blueprints(repo.path))[0].startswith(
        "tools/lockcheck/schema/image-blueprint.schema.json cannot be read: "
    )


def test_the_checkout_is_at_the_pin_with_its_blueprints_unchanged(tmp_path: Path) -> None:
    repo = infra(tmp_path / "infra")
    head = repo.git("rev-parse", "HEAD")
    root = platform(tmp_path / "platform", head)
    assert json.loads(generate(root, repo.path))["infra_pin"] == head
    (repo.path / "README.md").write_text("a change elsewhere\n")
    assert json.loads(generate(root, repo.path))["infra_pin"] == head
    changed = "the pokus-infrastructure checkout has changes under its blueprints or their schema"
    put(repo.path, blueprint("added", "os-root"))
    assert problems(lambda: generate(root, repo.path)) == [changed]
    repo.git("add", "-A")
    assert problems(lambda: generate(root, repo.path)) == [changed]
    later = repo.commit("One more blueprint")
    assert problems(lambda: generate(root, repo.path)) == [
        f"the pokus-infrastructure checkout is at {later}, not at INFRA_PIN {head}"
    ]
    (root / "INFRA_PIN").write_text(later + "\n")
    assert set(json.loads(generate(root, repo.path))["blueprints"]) == {
        *(data["name"] for data in BLUEPRINTS),
        "added",
    }
    put(repo.path, blueprint("os-root", None, "curl", "python3", "less", "vim"))
    assert problems(lambda: generate(root, repo.path)) == [changed]
    repo.git("checkout", "-q", "--", "images")
    schema = repo.path / golden_images.SCHEMA
    schema.write_text(schema.read_text() + "\n")
    assert problems(lambda: generate(root, repo.path)) == [changed]


def test_an_untracked_or_an_ignored_blueprint_is_a_change_whatever_git_is_set_to_show(
    tmp_path: Path,
) -> None:
    repo = infra(tmp_path / "infra")
    (repo.path / ".gitignore").write_text("images/hidden/\n")
    repo.git("add", ".gitignore")
    head = repo.commit("Ignore a directory")
    root = platform(tmp_path / "platform", head)
    names = {data["name"] for data in BLUEPRINTS}
    assert set(json.loads(generate(root, repo.path))["blueprints"]) == names
    changed = "the pokus-infrastructure checkout has changes under its blueprints or their schema"
    repo.git("config", "status.showUntrackedFiles", "no")
    extra = put(repo.path, blueprint("extra", "os-root"))
    assert repo.git("status", "--porcelain") == ""
    assert problems(lambda: generate(root, repo.path)) == [changed]
    extra.unlink()
    assert set(json.loads(generate(root, repo.path))["blueprints"]) == names
    put(repo.path, blueprint("hidden", "os-root"))
    assert repo.git("status", "--porcelain", "--untracked-files=all") == ""
    assert problems(lambda: generate(root, repo.path)) == [changed]


def test_a_pin_that_is_no_commit_hash_or_a_directory_that_is_no_checkout(tmp_path: Path) -> None:
    repo = infra(tmp_path / "infra")
    root = platform(tmp_path / "platform", "main")
    assert problems(lambda: generate(root, repo.path)) == [
        "INFRA_PIN: not one full commit hash on one line"
    ]
    (root / "INFRA_PIN").write_text("e" * 40 + "\n")
    (tmp_path / "plain").mkdir()
    found = problems(lambda: generate(root, tmp_path / "plain"))
    assert len(found) == 1 and "rev-parse" in found[0]
