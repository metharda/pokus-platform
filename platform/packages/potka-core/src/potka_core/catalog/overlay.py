"""A derived image's overlay: the directory ``blueprints/<derived name>/overlay/``.

The overlay is what a derived image adds to its golden image:

* ``files/``: a tree of directories and regular files, copied to the same
  paths below ``/`` of the build instance;
* ``install.sh``: run once in the build instance by ``/bin/sh`` as root,
  after the files are there. It has the golden image's shell and Python
  (``python3``, the standard library) and no network.

An overlay holds at least one regular file, below ``files/`` or as
``install.sh``, and nothing else: no other entry beside the two, and no
symbolic link or special file anywhere. A name below ``files/`` is
printable UTF-8 (no control or invisible character). Nothing here follows
a link.

The listing names the regular files. An empty directory is not part of an
overlay (a checkout keeps none), and which owner and mode a file gets in
the image is the builder's matter.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

FILES = "files"
INSTALL = "install.sh"


class OverlayError(ValueError):
    """A directory is not an overlay.

    Attributes:
        problems: Every reason, each with the path it is about (relative to
            the overlay, ``.`` for the overlay itself).
    """

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        """Keep the problems.

        Args:
            problems: Each path and what is wrong with it.
        """
        super().__init__("; ".join(f"{path}: {reason}" for path, reason in problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class Overlay:
    """An overlay that has the form.

    Attributes:
        root: The overlay directory.
        files: The regular files below ``files/``, each as its path relative
            to ``files/`` with forward slashes, sorted.
        install: Whether the overlay has an ``install.sh``.
    """

    root: Path
    files: tuple[str, ...]
    install: bool

    def paths(self) -> tuple[Path, ...]:
        """Every regular file of the overlay: those of ``files/``, then ``install.sh``."""
        found = [self.root / FILES / name for name in self.files]
        return (*found, self.root / INSTALL) if self.install else tuple(found)


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISREG(mode):
        return "a regular file"
    return "a special file"


def _shown(name: str) -> str:
    """NAME with every character that cannot be printed as ``?``.

    A byte that is not UTF-8 reaches Python as a lone surrogate, which is not printable.
    """
    return "".join(char if char.isprintable() else "?" for char in name)


def _tree(
    directory: Path, relative: str, files: list[str], problems: list[tuple[str, str]]
) -> None:
    try:
        with os.scandir(directory) as entries:
            listed = sorted(
                (entry.name, entry.stat(follow_symlinks=False).st_mode) for entry in entries
            )
    except OSError as exc:
        problems.append((relative, f"cannot be read ({type(exc).__name__})"))
        return
    for entry, mode in listed:
        name = f"{relative}/{entry}"
        if not entry.isprintable():
            problems.append((f"{relative}/{_shown(entry)}", "a name that is not printable UTF-8"))
        elif stat.S_ISDIR(mode):
            _tree(directory / entry, name, files, problems)
        elif stat.S_ISREG(mode):
            files.append(name.removeprefix(FILES + "/"))
        else:
            problems.append((name, f"{_kind(mode)}, not a directory or a regular file"))


def read_overlay(root: Path) -> Overlay:
    """Check that ROOT is an overlay, and list its files.

    Args:
        root: The overlay directory.

    Returns:
        The overlay.

    Raises:
        OverlayError: ROOT or something in it cannot be read, ROOT is not
            a directory, holds another entry than ``files`` and
            ``install.sh``, ``files`` is not a directory or ``install.sh``
            not a regular file, something below ``files/`` is a symbolic
            link, a special file, has a name that is not printable UTF-8 or
            lies too deep, or the overlay holds no regular file at all. It
            names every problem.
    """
    try:
        mode = os.lstat(root).st_mode
    except OSError as exc:
        raise OverlayError([(".", f"cannot be read ({type(exc).__name__})")]) from None
    if not stat.S_ISDIR(mode):
        raise OverlayError([(".", f"{_kind(mode)}, not a directory")])
    problems: list[tuple[str, str]] = []
    files: list[str] = []
    found: dict[str, int] = {}
    try:
        with os.scandir(root) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                found[entry.name] = entry.stat(follow_symlinks=False).st_mode
    except OSError as exc:
        raise OverlayError([(".", f"cannot be read ({type(exc).__name__})")]) from None
    for name, entry_mode in found.items():
        if name == FILES and stat.S_ISDIR(entry_mode):
            try:
                _tree(root / FILES, FILES, files, problems)
            except RecursionError:
                problems.append((FILES, "directories nested too deeply"))
        elif name == FILES:
            problems.append((name, f"{_kind(entry_mode)}, not a directory"))
        elif name == INSTALL and not stat.S_ISREG(entry_mode):
            problems.append((name, f"{_kind(entry_mode)}, not a regular file"))
        elif name != INSTALL:
            problems.append((_shown(name), f"an overlay holds only {FILES}/ and {INSTALL}"))
    install = stat.S_ISREG(found.get(INSTALL, 0))
    if not problems and not files and not install:
        problems.append((".", f"an overlay holds a file below {FILES}/, {INSTALL} or both"))
    if problems:
        raise OverlayError(problems)
    return Overlay(root=root, files=tuple(sorted(files)), install=install)
