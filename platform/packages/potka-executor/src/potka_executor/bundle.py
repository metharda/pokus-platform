"""The hash of a policy bundle: what names the bundle that made a decision.

A bundle is a directory: the decision's Rego with its tests, the data the
tool registry is compiled to, and the manifest that names its roots. Its
hash is the SHA-256 of the canonical JSON of the list of ``[path,
sha256]`` of every file in it, by path, without the one file that holds
the hash itself. The bundle carries that hash as data, so a decision can
say what its bundle states of itself; the release's bundle states the same
on disk, so the executor can tell a decision of another release's bundle
from its own.

The hash a bundle carries is its own word. A directory whose files were
changed while the file that holds the hash was left as it was still states
the old hash; `kept_hash` and `bundle_hash` of one directory differ then,
and whoever must know compares the two.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Final

from potka_core.canonical import canonical_json

HASH_FILE: Final = PurePosixPath("potka/bundle/data.json")
"""Where a bundle holds its hash: ``data.potka.bundle.hash`` to the policy engine."""
HASH_KEY: Final = "hash"


class BundleError(ValueError):
    """A directory is no bundle whose hash can be taken or read."""


def bundle_files(directory: Path) -> list[tuple[str, str]]:
    """The files of the bundle in DIRECTORY, each with the SHA-256 of its bytes, by path.

    Args:
        directory: The bundle's directory.

    Returns:
        ``(path, sha256)`` for every file below DIRECTORY but `HASH_FILE`,
        the path relative and with forward slashes, sorted by path.

    Raises:
        BundleError: DIRECTORY is no directory, holds a link or anything
            else that is neither a directory nor a regular file, or a
            part of it cannot be read: a file left out would be a file
            the hash says nothing of.
    """
    if not directory.is_dir() or directory.is_symlink():
        raise BundleError("the bundle is no directory")
    found: list[tuple[str, str]] = []
    try:
        # A link to a directory is among the names of files: it is not followed.
        for top, _, names in directory.walk(on_error=_refuse):
            for path in (top / name for name in names):
                name = path.relative_to(directory).as_posix()
                if path.is_symlink():
                    raise BundleError(f"the bundle holds a link: {name}")
                if not path.is_file():
                    raise BundleError(f"the bundle holds what is no file: {name}")
                if PurePosixPath(name) == HASH_FILE:
                    continue
                found.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    except OSError as exc:
        raise BundleError(f"a part of the bundle cannot be read ({type(exc).__name__})") from exc
    return sorted(found)


def _refuse(error: OSError) -> None:
    """A directory that cannot be listed ends the walk; left alone it would be skipped."""
    raise error


def bundle_hash(directory: Path) -> str:
    """The hash of the bundle in DIRECTORY.

    Args:
        directory: The bundle's directory.

    Returns:
        The SHA-256, in hexadecimal, of the canonical JSON of the list of
        ``[path, sha256]`` of `bundle_files`.

    Raises:
        BundleError: As `bundle_files`; or the bundle holds no file.
    """
    files = bundle_files(directory)
    if not files:
        raise BundleError("the bundle holds no file")
    return hashlib.sha256(canonical_json([list(entry) for entry in files])).hexdigest()


def hash_file(value: str) -> str:
    """The text of a bundle's `HASH_FILE` for the hash VALUE."""
    return json.dumps({HASH_KEY: value}, indent=2) + "\n"


def kept_hash(directory: Path) -> str:
    """The hash the bundle in DIRECTORY says it has.

    Args:
        directory: The bundle's directory.

    Returns:
        The hash in its `HASH_FILE`.

    Raises:
        BundleError: The file is missing, is a link or lies below one, is
            no JSON object with exactly the key ``hash``, or its value is
            no SHA-256 in hexadecimal.
    """
    # What `bundle_files` refuses in a bundle is refused on the way to this file too.
    on_the_way = [directory, *(directory / part for part in reversed(HASH_FILE.parents[:-1]))]
    if any(path.is_symlink() for path in (*on_the_way, directory / HASH_FILE)):
        raise BundleError(f"{HASH_FILE} is a link or lies below one")
    try:
        held = json.loads((directory / HASH_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleError(f"{HASH_FILE} is missing or is no JSON") from exc
    value = held.get(HASH_KEY) if isinstance(held, dict) and set(held) == {HASH_KEY} else None
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise BundleError(f"{HASH_FILE} does not hold one sha256 as its {HASH_KEY}")
    return value
