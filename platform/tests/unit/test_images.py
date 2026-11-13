from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_core import images
from potka_core.images import (
    GoldenImage,
    GoldenImages,
    Package,
    alias_segment,
    golden_images,
    load_table,
)
from pydantic import ValidationError

PLATFORM = Path(__file__).resolve().parents[2]
PIN = "0123456789abcdef0123456789abcdef01234567"
# A table of blueprints that are not the infrastructure's: nothing here depends on its names.
TABLE: dict[str, Any] = {
    "infra_pin": PIN,
    "blueprints": {
        "ground": {
            "version": "2.0.0",
            "type": "container",
            "nesting": False,
            "base": {"image": "some-os"},
            "packages": ["curl", "python3=3.12.3-1"],
            "effective_packages": ["curl", "python3=3.12.3-1"],
        },
        "web": {
            "version": "2.1.0",
            "type": "container",
            "nesting": False,
            "base": {"blueprint": "ground"},
            "packages": ["nginx"],
            "effective_packages": ["curl", "nginx", "python3=3.12.3-1"],
        },
        "nested": {
            "version": "3",
            "type": "container",
            "nesting": True,
            "base": {"blueprint": "ground"},
            "packages": [],
            "effective_packages": ["curl", "python3=3.12.3-1"],
        },
        "machine": {
            "version": "1.0.0-rc.1",
            "type": "vm",
            "nesting": False,
            "base": {"blueprint": "ground"},
            "packages": ["nftables"],
            "effective_packages": ["curl", "nftables", "python3=3.12.3-1"],
        },
    },
}
FINGERPRINT = "ab" * 32
PACKAGES = [
    {"name": "curl", "version": "8.5.0-2", "architecture": "amd64"},
    {"name": "tzdata", "version": "2025b", "architecture": "all"},
]
NOT_AN_ALIAS = "the alias is not <blueprint>/<version>"
NOT_IN_THE_TABLE = "the blueprint and version are not in the golden images table"
NOT_AN_ENTRY = (
    "the entry does not have the contract's form (a whole fingerprint, type, nesting and"
    " packages); a fingerprint of an older form must be computed again"
)
NO_PACKAGES = "the entry has no package list"
NOT_THE_TABLE_S = "the entry's type or nesting is not the table's"


def table(data: dict[str, Any] = TABLE) -> GoldenImages:
    return GoldenImages.model_validate_json(json.dumps(data), strict=True)


def entry(**changes: Any) -> dict[str, Any]:
    data = {"fingerprint": FINGERPRINT, "type": "container", "nesting": False, "packages": PACKAGES}
    return copy.deepcopy({**data, **changes})


def without(key: str) -> dict[str, Any]:
    data = entry()
    del data[key]
    return data


def test_the_table_of_the_release_is_read_from_beside_the_environment() -> None:
    assert Path(sys.prefix).parent == PLATFORM
    found = load_table()
    assert found == load_table(PLATFORM / "infra-data")
    assert found.model_dump(mode="json") == json.loads(
        (PLATFORM / "infra-data" / "golden-images.json").read_text(encoding="utf-8")
    )
    assert len(found.blueprints) >= 2


def test_a_table_is_read_from_the_directory_given(tmp_path: Path) -> None:
    (tmp_path / "golden-images.json").write_text(json.dumps(TABLE), encoding="utf-8")
    assert load_table(tmp_path) == table()
    with pytest.raises(OSError, match=r"golden-images\.json"):
        load_table(tmp_path / "missing")


@pytest.mark.parametrize(
    "change",
    [
        {"infra_pin": PIN[:-1]},
        {"infra_pin": PIN.upper()},
        {"infra_pin": PIN + "\n"},
        {"blueprints": {}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "type": "pod"}}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "nesting": "false"}}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "nesting": 0}}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "base": {}}}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "base": {"image": "a", "x": 1}}}},
        {"blueprints": {"web": {**TABLE["blueprints"]["web"], "digest": "x"}}},
        {
            "blueprints": {
                "web": {k: v for k, v in TABLE["blueprints"]["web"].items() if k != "nesting"}
            }
        },
        {"extra": 1},
    ],
)
def test_a_file_that_is_not_a_table_is_refused(change: dict[str, Any], tmp_path: Path) -> None:
    (tmp_path / "golden-images.json").write_text(json.dumps({**TABLE, **change}), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_table(tmp_path)


def test_a_blueprint_is_found_only_at_its_version() -> None:
    found = table()
    assert found.blueprint("web", "2.1.0") is found.blueprints["web"]
    assert found.blueprint("web", "2.0.0") is None
    assert found.blueprint("web", "2.1.0 ") is None
    assert found.blueprint("Web", "2.1.0") is None
    assert found.blueprint("other", "2.1.0") is None


def test_a_package_is_carried_by_its_name_whatever_its_version() -> None:
    found = table()
    web = found.blueprints["web"]
    assert web.carries("nginx")
    assert web.carries("curl")
    assert web.carries("python3")
    assert not web.carries("python3=3.12.3-1")
    assert not web.carries("python")
    assert not web.carries("nftables")
    assert found.blueprints["machine"].carries("nftables")
    assert found.everywhere("curl")
    assert found.everywhere("python3")
    assert not found.everywhere("nftables")
    assert not found.everywhere("nginx")


def test_an_entry_that_passes_every_check_is_a_golden_image() -> None:
    entries = {
        "web/2.1.0": entry(),
        "nested/3": entry(fingerprint="cd" * 32, nesting=True, packages=[]),
        "machine/1.0.0-rc.1": entry(fingerprint="ef" * 32, type="vm"),
    }
    found = golden_images(entries, table())
    assert found.refused == {}
    assert found.images == {
        "web/2.1.0": GoldenImage(
            alias="web/2.1.0",
            blueprint="web",
            version="2.1.0",
            fingerprint=FINGERPRINT,
            type="container",
            nesting=False,
            packages=(
                Package(name="curl", version="8.5.0-2", architecture="amd64"),
                Package(name="tzdata", version="2025b", architecture="all"),
            ),
        ),
        "nested/3": GoldenImage(
            alias="nested/3",
            blueprint="nested",
            version="3",
            fingerprint="cd" * 32,
            type="container",
            nesting=True,
            packages=(),
        ),
        "machine/1.0.0-rc.1": GoldenImage(
            alias="machine/1.0.0-rc.1",
            blueprint="machine",
            version="1.0.0-rc.1",
            fingerprint="ef" * 32,
            type="vm",
            nesting=False,
            packages=(
                Package(name="curl", version="8.5.0-2", architecture="amd64"),
                Package(name="tzdata", version="2025b", architecture="all"),
            ),
        ),
    }


@pytest.mark.parametrize(
    ("text", "segment"),
    [
        ("web", True),
        ("w", True),
        ("7", True),
        ("web-nginx-flask", True),
        ("1.0.0-rc.1", True),
        ("We.b_x~+1", True),
        ("a!b", True),
        ("W e.b_x", False),
        ("a\tb", False),
        ("a\nb", False),
        ("a\x00b", False),
        ("a\x7fb", False),
        ("a\u00e9b", False),
        ("", False),
        ("-web", False),
        ("web-", False),
        (".1", False),
        ("1.", False),
        ("web/1", False),
        ("/", False),
        ("web\n", False),
        ("\nweb", False),
        (" web", False),
        ("éweb", False),
        ("webé", False),
    ],
)
def test_an_alias_segment_begins_and_ends_with_a_letter_or_a_digit(
    text: str, segment: bool
) -> None:
    assert alias_segment(text) is segment


@pytest.mark.parametrize(
    "alias",
    [
        "web",
        "web/",
        "/2.1.0",
        "/",
        "",
        "web/2.1.0/",
        "/web/2.1.0",
        "web//2.1.0",
        "potka/web/2.1.0",
        "web/2.1.0/1",
        "-web/2.1.0",
        "web-/2.1.0",
        "web/.2.1.0",
        "web/2.1.0.",
        "web/2.1.0\n",
        "\nweb/2.1.0",
        " web/2.1.0",
        "web/2.1.0 ",
        "éweb/2.1.0",
        "webé/2.1.0",
        "web/2.1.é",
        "we b/2.1.0",
        "wéb/2.1.0",
        "w\nb/2.1.0",
        "web/2.1 .0",
    ],
)
def test_an_alias_of_another_form_is_refused(alias: str) -> None:
    data = copy.deepcopy(TABLE)
    # Whatever the table lists: the alias's form is checked first.
    for name in (
        "-web",
        "web-",
        "éweb",
        "webé",
        " web",
        "\nweb",
        "potka",
        "",
        "we b",
        "wéb",
        "w\nb",
    ):
        data["blueprints"][name] = TABLE["blueprints"]["web"]
    found = golden_images({alias: entry()}, table(data))
    assert found.images == {}
    assert found.refused == {alias: NOT_AN_ALIAS}


@pytest.mark.parametrize(
    "alias",
    ["web/2.0.0", "web/2.1", "web/2.1.00", "Web/2.1.0", "other/2.1.0", "ground/2.1.0", "w.e_b/2"],
)
def test_a_blueprint_or_a_version_the_table_does_not_list_is_refused(alias: str) -> None:
    found = golden_images({alias: entry()}, table())
    assert found.images == {}
    assert found.refused == {alias: NOT_IN_THE_TABLE}


@pytest.mark.parametrize(
    "data",
    [
        FINGERPRINT,
        None,
        [],
        {},
        {"fingerprint": FINGERPRINT, "packages": PACKAGES},
        without("fingerprint"),
        without("type"),
        without("nesting"),
        without("packages"),
        entry(fingerprint=None),
        entry(fingerprint=""),
        entry(fingerprint=7),
        entry(fingerprint="ab"),
        entry(fingerprint="ab" * 31 + "a"),
        entry(fingerprint="ab" * 32 + "a"),
        entry(fingerprint="AB" * 32),
        entry(fingerprint="ag" * 32),
        entry(fingerprint="ab" * 32 + "\n"),
        entry(fingerprint=" " + "ab" * 32),
        entry(fingerprint=" " * 64),
        entry(type="pod"),
        entry(type=None),
        entry(nesting="false"),
        entry(nesting=0),
        entry(nesting=None),
        entry(packages="curl"),
        entry(packages=["curl"]),
        entry(packages=[{"name": "curl", "version": "8.5.0-2"}]),
        entry(packages=[{"name": "", "version": "8.5.0-2", "architecture": "amd64"}]),
        entry(packages=[{"name": "curl", "version": "", "architecture": "amd64"}]),
        entry(packages=[{"name": "curl", "version": "8.5.0-2", "architecture": ""}]),
        entry(packages=[{**PACKAGES[0], "status": "installed"}]),
        entry(requirements={"nesting": True}),
        entry(packages=[{**PACKAGES[0], "version": object()}]),
        entry(packages=[{**PACKAGES[0], "version": float("nan")}]),
    ],
)
def test_an_entry_of_another_form_is_refused(data: Any) -> None:
    found = golden_images({"web/2.1.0": data}, table())
    assert found.images == {}
    assert found.refused == {"web/2.1.0": NOT_AN_ENTRY}


def test_an_entry_too_deep_to_read_is_refused_and_the_others_are_judged() -> None:
    deep: Any = []
    for _ in range(100_000):
        deep = [deep]
    entries = {"web/2.1.0": {**entry(), "packages": deep}, "nested/3": entry(nesting=True)}
    found = golden_images(entries, table())
    assert list(found.images) == ["nested/3"]
    assert found.refused == {"web/2.1.0": NOT_AN_ENTRY}


def test_an_alias_that_is_no_text_is_refused() -> None:
    entries: Any = {7: entry(), None: entry(), ("web", "2.1.0"): entry()}
    found = golden_images(entries, table())
    assert found.images == {}
    assert found.refused == dict.fromkeys(entries, NOT_AN_ALIAS)


def test_the_selection_cannot_be_changed() -> None:
    found = golden_images({"web/2.1.0": entry(), "web/2.0.0": entry()}, table())
    image = found.images["web/2.1.0"]
    changes: Any = found
    with pytest.raises(TypeError):
        changes.images["web/2.0.0"] = image
    with pytest.raises(TypeError):
        del changes.refused["web/2.0.0"]
    with pytest.raises(AttributeError):
        changes.images = {}
    with pytest.raises(AttributeError):
        image.nesting = True  # type: ignore[misc]
    assert list(found.images) == ["web/2.1.0"]
    assert list(found.refused) == ["web/2.0.0"]


def test_an_entry_without_a_package_list_is_refused() -> None:
    found = golden_images({"web/2.1.0": entry(packages=None)}, table())
    assert found.images == {}
    assert found.refused == {"web/2.1.0": NO_PACKAGES}


@pytest.mark.parametrize(
    ("alias", "changes"),
    [
        ("web/2.1.0", {"type": "vm"}),
        ("web/2.1.0", {"nesting": True}),
        ("web/2.1.0", {"type": "vm", "nesting": True}),
        ("nested/3", {}),
        ("machine/1.0.0-rc.1", {}),
        ("machine/1.0.0-rc.1", {"type": "vm", "nesting": True}),
    ],
)
def test_an_entry_whose_type_or_nesting_is_not_the_table_s_is_refused(
    alias: str, changes: dict[str, Any]
) -> None:
    found = golden_images({alias: entry(**changes)}, table())
    assert found.images == {}
    assert found.refused == {alias: NOT_THE_TABLE_S}


def test_each_entry_is_judged_on_its_own() -> None:
    entries = {
        "web/2.1.0": entry(),
        "web/2.0.0": entry(),
        "ground/2.0.0": entry(packages=None),
        "nested/3": entry(nesting=True, type="vm"),
        "potka/web/1": entry(),
        "machine/1.0.0-rc.1": {"fingerprint": FINGERPRINT, "packages": PACKAGES},
    }
    found = golden_images(entries, table())
    assert list(found.images) == ["web/2.1.0"]
    assert found.refused == {
        "web/2.0.0": NOT_IN_THE_TABLE,
        "ground/2.0.0": NO_PACKAGES,
        "nested/3": NOT_THE_TABLE_S,
        "potka/web/1": NOT_AN_ALIAS,
        "machine/1.0.0-rc.1": NOT_AN_ENTRY,
    }
    assert golden_images({}, table()) == images.Selection(images={}, refused={})


ALIASES = st.one_of(
    st.sampled_from([f"{name}/{b['version']}" for name, b in TABLE["blueprints"].items()]),
    st.sampled_from(["web/2.0.0", "other/1", "potka/web/1", "web", "nested/3/", ""]),
    st.text(max_size=12),
)
ENTRIES = st.one_of(
    st.fixed_dictionaries(
        {
            "fingerprint": st.one_of(st.just(FINGERPRINT), st.none(), st.just("")),
            "type": st.sampled_from(["container", "vm", "pod"]),
            "nesting": st.one_of(st.booleans(), st.just("true")),
            "packages": st.one_of(st.none(), st.just([]), st.just(PACKAGES)),
        }
    ),
    st.just({"fingerprint": FINGERPRINT, "packages": PACKAGES}),
    st.none(),
)


@given(entries=st.dictionaries(ALIASES, ENTRIES, max_size=6))
def test_an_accepted_image_is_always_a_blueprint_of_the_table_as_the_table_has_it(
    entries: dict[str, Any],
) -> None:
    found = golden_images(entries, table())
    assert sorted([*found.images, *found.refused]) == sorted(entries)
    assert not set(found.images) & set(found.refused)
    for alias, image in found.images.items():
        listed = TABLE["blueprints"][image.blueprint]
        assert alias == image.alias == f"{image.blueprint}/{listed['version']}"
        assert image.version == listed["version"]
        assert (image.type, image.nesting) == (listed["type"], listed["nesting"])
        given = entries[alias]
        assert image.fingerprint == given["fingerprint"] == FINGERPRINT
        # The entry's own values, which are the table's: an entry that says otherwise is refused.
        assert (image.type, image.nesting) == (given["type"], given["nesting"])
        assert type(given["nesting"]) is bool
        assert given["packages"] is not None
        assert [package.model_dump() for package in image.packages] == given["packages"]


def table_readers(packages: Path) -> tuple[set[str], set[str]]:
    """The modules below PACKAGES that call load_table, and those that give it an argument."""
    readers: set[str] = set()
    with_argument: set[str] = set()
    for path in sorted(packages.glob("*/src/**/*.py")):
        name = path.relative_to(packages).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            callee = called.id if isinstance(called, ast.Name) else getattr(called, "attr", None)
            if callee == "load_table":
                readers.add(name)
                if node.args or node.keywords:
                    with_argument.add(name)
    return readers, with_argument


def test_only_the_command_reads_a_table_from_a_directory_given(tmp_path: Path) -> None:
    # A service reads the table of its release: load_table() without an argument.
    sources = {
        "a/src/a/plain.py": "from potka_core.images import load_table\ntable = load_table()\n",
        "a/src/a/named.py": "from potka_core import images\ntable = images.load_table(None)\n",
        "b/src/b/deep/keyword.py": "def f(d):\n    return load_table(directory=d)\n",
        "b/src/b/none.py": "load = 1\n",
        "b/tests/other.py": "load_table(1)\n",
    }
    for name, text in sources.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert table_readers(tmp_path) == (
        {"a/src/a/plain.py", "a/src/a/named.py", "b/src/b/deep/keyword.py"},
        {"a/src/a/named.py", "b/src/b/deep/keyword.py"},
    )
    _, with_argument = table_readers(PLATFORM / "packages")
    assert with_argument <= {"potka-cli/src/potka_cli/main.py"}
