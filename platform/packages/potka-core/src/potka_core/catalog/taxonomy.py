"""``taxonomy.yaml``: the labels a diagnosis may carry.

A label is a dotted name, the broadest part first: ``a.b.c`` lies below
``a.b`` and ``a``, whether the file lists those or not. Only the labels the
file lists can be answered by a model or accepted by a ground truth. Two
labels are always there: ``none`` (nothing is wrong) and ``unknown``
(something is wrong that no other label names); nothing lies below either.
The taxonomy has a version of its own. Its labels are the answer space of
a diagnosis; a label's description says what the label means to the people
who write and read a catalog.
"""

from __future__ import annotations

from typing import Final

from pydantic import model_validator

from potka_core.catalog.base import Count, Label, Model, Text

NONE: Final = "none"
UNKNOWN: Final = "unknown"


class LabelEntry(Model):
    """A label and what it means."""

    name: Label
    """The label: lowercase words joined by dots, the broadest first."""
    description: Text
    """What the label means."""


class Taxonomy(Model):
    """The labels a diagnosis may carry (taxonomy.yaml)."""

    version: Count
    """The taxonomy's version."""
    labels: tuple[LabelEntry, ...]
    """The labels, none and unknown among them, none of them twice."""

    @model_validator(mode="after")
    def _consistent(self) -> Taxonomy:
        names = self.names()
        twice = sorted({name for name in names if names.count(name) > 1})
        if twice:
            raise ValueError(f"label given twice: {', '.join(twice)}")
        for required in (NONE, UNKNOWN):
            if required not in names:
                raise ValueError(f"the label {required} is missing")
        below = sorted(n for n in names if n.split(".")[0] in (NONE, UNKNOWN) and "." in n)
        if below:
            raise ValueError(f"label below {NONE} or {UNKNOWN}: {', '.join(below)}")
        return self

    def names(self) -> tuple[str, ...]:
        """Every label, in the file's order."""
        return tuple(entry.name for entry in self.labels)
