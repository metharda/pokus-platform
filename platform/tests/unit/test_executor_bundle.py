"""The hash of a policy bundle: every file of it, in one order, without the file that holds it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from potka_core.canonical import canonical_json
from potka_executor.bundle import (
    HASH_FILE,
    BundleError,
    bundle_files,
    bundle_hash,
    hash_file,
    kept_hash,
)

FILES = {
    ".manifest": b'{"roots": ["potka"]}\n',
    "potka/action.rego": b"package potka.action\n",
    "potka/action_test.rego": b"package potka.action_test\n",
    "tools/data.json": b"{}\n",
}


def bundle(root: Path, files: dict[str, bytes], order: list[str] | None = None) -> Path:
    for name in order or list(files):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(files[name])
    return root


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_the_files_are_named_from_the_bundles_root_and_sorted(tmp_path: Path) -> None:
    assert bundle_files(bundle(tmp_path, FILES)) == [
        (".manifest", digest(FILES[".manifest"])),
        ("potka/action.rego", digest(FILES["potka/action.rego"])),
        ("potka/action_test.rego", digest(FILES["potka/action_test.rego"])),
        ("tools/data.json", digest(FILES["tools/data.json"])),
    ]


def test_the_hash_is_of_the_canonical_list_of_paths_and_digests(tmp_path: Path) -> None:
    listed = [[name, digest(content)] for name, content in sorted(FILES.items())]
    assert (
        bundle_hash(bundle(tmp_path, FILES)) == hashlib.sha256(canonical_json(listed)).hexdigest()
    )


def test_the_file_that_holds_the_hash_is_not_hashed(tmp_path: Path) -> None:
    before = bundle_hash(bundle(tmp_path, FILES))
    (tmp_path / HASH_FILE).parent.mkdir(parents=True)
    (tmp_path / HASH_FILE).write_text(hash_file(before))
    assert bundle_hash(tmp_path) == before
    assert kept_hash(tmp_path) == before
    (tmp_path / HASH_FILE).write_text(hash_file("0" * 64))
    assert bundle_hash(tmp_path) == before


def test_only_that_one_path_is_left_out(tmp_path: Path) -> None:
    before = bundle_hash(bundle(tmp_path, FILES))
    other = tmp_path / "tools" / "bundle" / "data.json"
    other.parent.mkdir(parents=True)
    other.write_text(hash_file(before))
    assert bundle_hash(tmp_path) != before


@settings(max_examples=30, deadline=None)
@given(st.permutations(sorted(FILES)))
def test_the_order_the_files_were_written_in_changes_nothing(order: list[str]) -> None:
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        assert bundle_hash(bundle(Path(first), FILES, order)) == bundle_hash(
            bundle(Path(second), FILES)
        )


@pytest.mark.parametrize("name", sorted(FILES))
def test_a_changed_byte_of_any_file_changes_the_hash(tmp_path: Path, name: str) -> None:
    before = bundle_hash(bundle(tmp_path, FILES))
    (tmp_path / name).write_bytes(FILES[name] + b" ")
    assert bundle_hash(tmp_path) != before


def test_a_file_more_less_or_renamed_changes_the_hash(tmp_path: Path) -> None:
    before = bundle_hash(bundle(tmp_path, FILES))
    (tmp_path / "potka" / "other.rego").write_bytes(b"package potka.other\n")
    more = bundle_hash(tmp_path)
    (tmp_path / "potka" / "other.rego").unlink()
    (tmp_path / "tools" / "data.json").rename(tmp_path / "tools" / "data2.json")
    renamed = bundle_hash(tmp_path)
    (tmp_path / "tools" / "data2.json").unlink()
    assert len({before, more, renamed, bundle_hash(tmp_path)}) == 4


def test_an_empty_directory_adds_nothing(tmp_path: Path) -> None:
    before = bundle_hash(bundle(tmp_path, FILES))
    (tmp_path / "empty").mkdir()
    assert bundle_hash(tmp_path) == before


def test_a_bundle_holds_no_link(tmp_path: Path) -> None:
    bundle(tmp_path / "bundle", FILES)
    (tmp_path / "outside").write_bytes(b"x")
    (tmp_path / "bundle" / "potka" / "linked.rego").symlink_to(tmp_path / "outside")
    with pytest.raises(BundleError, match=re.escape("holds a link: potka/linked.rego")):
        bundle_hash(tmp_path / "bundle")


def test_a_linked_directory_is_no_bundle(tmp_path: Path) -> None:
    bundle(tmp_path / "bundle", FILES)
    (tmp_path / "link").symlink_to(tmp_path / "bundle")
    with pytest.raises(BundleError, match="no directory"):
        bundle_hash(tmp_path / "link")


def test_a_bundle_holds_only_files(tmp_path: Path) -> None:
    bundle(tmp_path, FILES)
    os.mkfifo(tmp_path / "potka" / "pipe")
    with pytest.raises(BundleError, match="what is no file: potka/pipe"):
        bundle_hash(tmp_path)


def test_a_linked_directory_inside_a_bundle_is_a_link_and_is_not_followed(tmp_path: Path) -> None:
    bundle(tmp_path / "bundle", FILES)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "more.rego").write_bytes(b"package more\n")
    (tmp_path / "bundle" / "potka" / "linked").symlink_to(tmp_path / "outside")
    with pytest.raises(BundleError, match=re.escape("holds a link: potka/linked")):
        bundle_hash(tmp_path / "bundle")


def test_a_directory_that_cannot_be_listed_is_no_part_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle(tmp_path, FILES)
    whole = bundle_hash(tmp_path)
    listing = os.scandir

    def scandir(path: os.PathLike[str] | str) -> Any:
        if Path(path).name == "tools":
            raise PermissionError(13, "Permission denied", str(path))
        return listing(path)

    monkeypatch.setattr(os, "scandir", scandir)
    # Left alone, the walk would skip the directory, and the hash would be of the rest.
    with pytest.raises(BundleError) as refused:
        bundle_hash(tmp_path)
    assert str(refused.value) == "a part of the bundle cannot be read (PermissionError)"
    monkeypatch.undo()
    assert bundle_hash(tmp_path) == whole


def test_a_file_that_cannot_be_read_is_no_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle(tmp_path, FILES)
    reading = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path.name == "action.rego":
            raise PermissionError(13, "Permission denied", str(path))
        return reading(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(BundleError, match=re.escape("cannot be read (PermissionError)")):
        bundle_files(tmp_path)


def test_what_is_no_directory_or_holds_nothing_is_no_bundle(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="no directory"):
        bundle_hash(tmp_path / "absent")
    (tmp_path / "file").write_bytes(b"x")
    with pytest.raises(BundleError, match="no directory"):
        bundle_hash(tmp_path / "file")
    (tmp_path / "empty").mkdir()
    with pytest.raises(BundleError, match="holds no file"):
        bundle_hash(tmp_path / "empty")
    (tmp_path / "only").mkdir()
    (tmp_path / "only" / HASH_FILE).parent.mkdir(parents=True)
    (tmp_path / "only" / HASH_FILE).write_text(hash_file("0" * 64))
    with pytest.raises(BundleError, match="holds no file"):
        bundle_hash(tmp_path / "only")


def test_the_hash_files_text_is_one_json_object_with_the_hash() -> None:
    assert json.loads(hash_file("a" * 64)) == {"hash": "a" * 64}
    assert hash_file("a" * 64).endswith("}\n")


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "not json",
        "[]",
        "{}",
        json.dumps({"hash": "a" * 63}),
        json.dumps({"hash": "A" * 64}),
        json.dumps({"hash": 1}),
        json.dumps({"hash": "a" * 64, "more": 1}),
        json.dumps({"sha256": "a" * 64}),
    ],
)
def test_a_hash_file_of_another_form_is_refused(tmp_path: Path, text: str | None) -> None:
    bundle(tmp_path, FILES)
    if text is not None:
        (tmp_path / HASH_FILE).parent.mkdir(parents=True)
        (tmp_path / HASH_FILE).write_text(text)
    with pytest.raises(BundleError):
        kept_hash(tmp_path)


def test_a_hash_file_that_is_a_link_or_lies_below_one_is_refused(tmp_path: Path) -> None:
    held = hash_file("a" * 64)
    (tmp_path / "elsewhere" / "bundle").mkdir(parents=True)
    (tmp_path / "elsewhere" / "bundle" / "data.json").write_text(held)
    assert kept_hash_of(tmp_path / "plain", held) == "a" * 64

    file_linked = bundle(tmp_path / "file", FILES)
    (file_linked / HASH_FILE).parent.mkdir(parents=True)
    (file_linked / HASH_FILE).symlink_to(tmp_path / "elsewhere" / "bundle" / "data.json")
    directory_linked = bundle(tmp_path / "directory", FILES)
    (directory_linked / HASH_FILE.parent).symlink_to(tmp_path / "elsewhere" / "bundle")
    root_linked = tmp_path / "root"
    root_linked.symlink_to(tmp_path / "plain")
    for directory in (file_linked, directory_linked, root_linked):
        # The link's target holds a hash of the right form: only the link is at fault.
        assert json.loads((directory / HASH_FILE).read_text()) == {"hash": "a" * 64}
        with pytest.raises(BundleError, match="is a link or lies below one"):
            kept_hash(directory)


def kept_hash_of(directory: Path, held: str) -> str:
    bundle(directory, FILES)
    (directory / HASH_FILE).parent.mkdir(parents=True)
    (directory / HASH_FILE).write_text(held)
    return kept_hash(directory)
