"""The library's index: every fault primitive and every probe it has, by name."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from potka_primitives import disk, files, nginx, services, units
from potka_primitives.base import Primitive, Probe, Step
from potka_primitives.errors import UnknownName


def index[S: Step[Any]](classes: Iterable[type[S]]) -> Mapping[str, type[S]]:
    """CLASSES by their names, read-only.

    Raises:
        ValueError: Two of them have one name.
    """
    found: dict[str, type[S]] = {}
    for one in classes:
        if one.name in found:
            raise ValueError(f"two steps are named {one.name}")
        found[one.name] = one
    return MappingProxyType(dict(sorted(found.items())))


PRIMITIVES: Mapping[str, type[Primitive[Any]]] = index(
    (
        *disk.PRIMITIVES,
        *files.PRIMITIVES,
        *nginx.PRIMITIVES,
        *services.PRIMITIVES,
        *units.PRIMITIVES,
    )
)
PROBES: Mapping[str, type[Probe[Any]]] = index((*disk.PROBES,))


def primitive(name: str) -> Primitive[Any]:
    """The fault primitive of that name.

    Raises:
        UnknownName: The library has none.
    """
    found = PRIMITIVES.get(name)
    if found is None:
        raise UnknownName(name)
    return found()


def probe(name: str) -> Probe[Any]:
    """The probe of that name.

    Raises:
        UnknownName: The library has none.
    """
    found = PROBES.get(name)
    if found is None:
        raise UnknownName(name)
    return found()
