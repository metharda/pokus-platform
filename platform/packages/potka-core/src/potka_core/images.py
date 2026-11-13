"""Golden images: the table of the infrastructure's blueprints, and which images count.

Nothing the platform decides comes from an image's properties: every
service with access to the image store can rewrite them. Two sources are
trusted instead:

* The golden images table, ``infra-data/golden-images.json`` in the
  release: for each of the infrastructure's blueprints its version, its
  type, whether it needs nesting, and its packages, generated from the
  blueprints at the pinned commit. A blueprint or a version the table does
  not list is not golden.
* The session's environment fingerprint, computed by the infrastructure as
  root: its ``software.golden_images`` maps an alias to the image's
  fingerprint, type, nesting and packages. `golden_images` keeps only the
  entries that pass every check; any other entry is not a golden image,
  whatever its alias says, and is never launched.

A service reads the table only from its release (`load_table` without an
argument).
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TABLE = Path("infra-data") / "golden-images.json"
Kind = Literal["container", "vm"]
# <blueprint>/<version>: two segments, each beginning and ending with a letter or a digit
# and holding only printable ASCII characters other than the space and the slash.
_SEGMENT = r"[0-9A-Za-z](?:[!-.0-~]*[0-9A-Za-z])?"
_ALIAS = re.compile(rf"(?P<blueprint>{_SEGMENT})/(?P<version>{_SEGMENT})")


def alias_segment(text: str) -> bool:
    """Whether TEXT can be a segment of an image alias.

    Args:
        text: A blueprint's name, a version, or a derived image's name.

    Returns:
        True when TEXT begins and ends with a letter or a digit and holds
        nothing but printable ASCII characters, without a space or a slash.
    """
    return re.fullmatch(_SEGMENT, text) is not None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class FromImage(_Strict):
    """A blueprint built from an operating system image."""

    image: str


class FromBlueprint(_Strict):
    """A blueprint built on another blueprint."""

    blueprint: str


class GoldenBlueprint(_Strict):
    """One of the infrastructure's blueprints, as the table has it."""

    version: str
    type: Kind
    nesting: bool
    base: FromImage | FromBlueprint
    packages: tuple[str, ...]
    effective_packages: tuple[str, ...]

    def carries(self, package: str) -> bool:
        """Whether an image of the blueprint has the package PACKAGE.

        Args:
            package: A package's name.

        Returns:
            True when the blueprint or one it is built on installs it; a
            version an entry carries (``name=version``) is not compared.
        """
        return any(entry.partition("=")[0] == package for entry in self.effective_packages)


class GoldenImages(_Strict):
    """The golden images table."""

    infra_pin: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    blueprints: Annotated[dict[str, GoldenBlueprint], Field(min_length=1)]

    def blueprint(self, name: str, version: str) -> GoldenBlueprint | None:
        """The blueprint NAME when the table lists it at VERSION, else None.

        Args:
            name: A blueprint's name.
            version: A version.

        Returns:
            The blueprint, or None when the table has no such blueprint or
            lists it at another version.
        """
        found = self.blueprints.get(name)
        return found if found is not None and found.version == version else None

    def everywhere(self, package: str) -> bool:
        """Whether every golden image has the package PACKAGE.

        Args:
            package: A package's name.

        Returns:
            True when every blueprint of the table carries it.
        """
        return all(blueprint.carries(package) for blueprint in self.blueprints.values())


def load_table(directory: Path | None = None) -> GoldenImages:
    """Read the golden images table.

    Args:
        directory: The directory that holds ``golden-images.json``. None, as
            every service calls it, is ``infra-data`` in the release: the
            directory beside the running environment (``.venv``).

    Returns:
        The table.

    Raises:
        OSError: The file cannot be read.
        pydantic.ValidationError: The file is not a table.
    """
    path = Path(sys.prefix).parent / TABLE if directory is None else directory / TABLE.name
    return GoldenImages.model_validate_json(path.read_bytes(), strict=True)


class Package(_Strict):
    """A package installed in a golden image."""

    name: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    architecture: Annotated[str, Field(min_length=1)]


class GoldenEntry(_Strict):
    """One entry of the fingerprint's ``software.golden_images``, as the contract has it.

    The contract's fingerprint is a string. An image is launched by its
    whole fingerprint, a sha256, so only 64 lowercase hexadecimal digits
    are one here: the image store takes a shorter text as the start of any
    fingerprint.
    """

    fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    type: Kind
    nesting: bool
    packages: tuple[Package, ...] | None


@dataclass(frozen=True)
class GoldenImage:
    """A golden image that may be launched.

    Attributes:
        alias: Its alias, ``<blueprint>/<version>``.
        blueprint: Its blueprint's name.
        version: Its blueprint's version.
        fingerprint: The image's fingerprint, by which it is launched.
        type: ``container`` or ``vm``.
        nesting: Whether an instance of it is launched with nesting.
        packages: The packages its build installed.
    """

    alias: str
    blueprint: str
    version: str
    fingerprint: str
    type: Kind
    nesting: bool
    packages: tuple[Package, ...]


@dataclass(frozen=True)
class Selection:
    """The golden images of a session.

    Attributes:
        images: The images that passed every check, by alias (read-only).
        refused: Every other entry's alias, with the check it failed
            (read-only).
    """

    images: Mapping[str, GoldenImage]
    refused: Mapping[str, str]


def _checked(alias: object, entry: Any, table: GoldenImages) -> GoldenImage | str:
    form = _ALIAS.fullmatch(alias) if isinstance(alias, str) else None
    if form is None or not isinstance(alias, str):
        return "the alias is not <blueprint>/<version>"
    blueprint = table.blueprint(form["blueprint"], form["version"])
    if blueprint is None:
        return "the blueprint and version are not in the golden images table"
    try:
        # As JSON, so that every value has exactly its JSON type: no "true" for true.
        valid = GoldenEntry.model_validate_json(json.dumps(entry), strict=True)
    except (TypeError, ValueError, RecursionError):
        return (
            "the entry does not have the contract's form (a whole fingerprint, type, nesting"
            " and packages); a fingerprint of an older form must be computed again"
        )
    if valid.packages is None:
        return "the entry has no package list"
    if (valid.type, valid.nesting) != (blueprint.type, blueprint.nesting):
        return "the entry's type or nesting is not the table's"
    return GoldenImage(
        alias=alias,
        blueprint=form["blueprint"],
        version=form["version"],
        fingerprint=valid.fingerprint,
        type=valid.type,
        nesting=valid.nesting,
        packages=valid.packages,
    )


def golden_images(entries: Mapping[str, Any], table: GoldenImages) -> Selection:
    """The golden images among the entries of a session's environment fingerprint.

    An entry counts only when its alias is ``<blueprint>/<version>`` (two
    segments, each beginning and ending with a letter or a digit), the
    table lists that blueprint at that version, the entry has the
    contract's form (a whole fingerprint of 64 hexadecimal digits, a type,
    a nesting flag, packages, and nothing else), its package list is not
    null, and its type and nesting are the table's. An entry of an older
    form of the contract, without type or nesting, is refused like any
    other that lacks them.

    Args:
        entries: ``fingerprint.software.golden_images`` as read from JSON:
            each alias with its entry.
        table: The golden images table.

    Returns:
        The images that count, and every refused alias with its reason.
    """
    images: dict[str, GoldenImage] = {}
    refused: dict[str, str] = {}
    for alias, entry in entries.items():
        checked = _checked(alias, entry, table)
        if isinstance(checked, GoldenImage):
            images[alias] = checked
        else:
            refused[alias] = checked
    return Selection(images=MappingProxyType(images), refused=MappingProxyType(refused))
