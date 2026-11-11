"""Every open verification marker in the repository, listed.

A version, digest, checksum, URL or key fingerprint that is not verified yet
carries the marker. While any remains, or a file cannot be read, the check
reports a warning: the gate shows it, and ``make verify-todos`` fails.
"""

from __future__ import annotations

from pathlib import Path

from potka_ci.outcome import Outcome
from potka_ci.repo import display_path, repository_files

MARKER = "TODO(" + "verify)"


def run(root: Path) -> Outcome:
    """List every line that carries the marker.

    Args:
        root: The working tree.

    Returns:
        A warning naming each place, or passed.
    """
    found = []
    unread = []
    for name in repository_files(root):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            unread.append(f"{display_path(name)}: not read (not UTF-8 text, or unreadable)")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if MARKER in line:
                found.append(f"{display_path(name)}:{number}: {MARKER} open")
    if found or unread:
        return Outcome("warning", [*found, *unread, f"{len(found)} open {MARKER} item(s)"])
    return Outcome("passed", [f"no open {MARKER} item"])
