"""Calls of the Incus client that are measured on the in-memory Incus and on a real one.

The same calls are made in both places and must end the same way. A unit
test holds the list of their ends for the in-memory Incus; the checks a
person runs against a real Incus compare it with the same list.
"""

from __future__ import annotations

from collections.abc import Callable

from potka_incus import client as incus


def measured(what: str, call: Callable[[], object]) -> str:
    """Print what a call gave or how it was refused; the same in short, without the message.

    A refusal is one of the client's exceptions: Incus's own answer, or
    what the client makes of an answer (a link read as a file has no
    status: Incus answered, and the client refused what it was given).
    """
    try:
        value = call()
    except incus.IncusError as exc:
        print(f"{what} was refused: {type(exc).__name__}, status {exc.status}: {exc}")
        return f"refused: {type(exc).__name__}, status {exc.status}"
    print(f"{what} gave {value!r}")
    return f"gave {value!r}"


def links_tree(base: str) -> list[list[str]]:
    """The commands that make the tree :func:`links_and_refusals` works in, below BASE."""
    return [
        ["mkdir", "-p", f"{base}/directory/full"],
        ["ln", "-s", "directory", f"{base}/through"],
        ["ln", "-s", "directory/file", f"{base}/link"],
        ["ln", "-s", "directory/later", f"{base}/dangling"],
        ["touch", f"{base}/directory/full/file"],
    ]


def links_and_refusals(client: incus.Incus, name: str, base: str) -> list[str]:
    """Read, write and delete through links and where Incus refuses, in the tree below BASE."""
    calls: list[tuple[str, Callable[[], object]]] = [
        (
            "writing through a link in the middle of a path",
            lambda: client.write_file(name, f"{base}/through/file", b"x"),
        ),
        (
            "reading through a link in the middle of a path",
            lambda: client.read_file(name, f"{base}/through/file").content,
        ),
        ("writing through a link", lambda: client.write_file(name, f"{base}/link", b"y")),
        (
            "the file the link names, read after that",
            lambda: client.read_file(name, f"{base}/directory/file").content,
        ),
        (
            "writing through a link to a file that does not exist",
            lambda: client.write_file(name, f"{base}/dangling", b"z"),
        ),
        (
            "the file that link names, read after that",
            lambda: client.read_file(name, f"{base}/directory/later").content,
        ),
        ("reading a link", lambda: client.read_file(name, f"{base}/link")),
        ("listing a link to a directory", lambda: client.list_directory(name, f"{base}/through")),
        ("deleting a link", lambda: client.delete_file(name, f"{base}/link")),
        (
            "the file the deleted link named, read after that",
            lambda: client.read_file(name, f"{base}/directory/file").content,
        ),
        (
            "appending to a file that does not exist",
            lambda: client.write_file(name, f"{base}/absent", b"x", append=True),
        ),
        (
            "writing below a directory that does not exist",
            lambda: client.write_file(name, f"{base}/absent/file", b"x"),
        ),
        ("writing to a directory", lambda: client.write_file(name, f"{base}/directory", b"x")),
        ("reading a file that does not exist", lambda: client.read_file(name, f"{base}/absent")),
        (
            "deleting a file that does not exist",
            lambda: client.delete_file(name, f"{base}/absent"),
        ),
        (
            "deleting a directory that is not empty",
            lambda: client.delete_file(name, f"{base}/directory/full"),
        ),
    ]
    return [measured(what, call) for what, call in calls]


# How the calls of links_and_refusals end on the in-memory Incus, in their order.
LINKS_AND_REFUSALS = [
    "gave None",
    "gave b'x'",
    "gave None",
    "gave b'y'",
    "gave None",
    "gave b'z'",
    "refused: WrongKind, status None",
    "refused: WrongKind, status None",
    "gave None",
    "gave b'y'",
    "refused: NotFound, status 404",
    "refused: NotFound, status 404",
    "refused: IncusError, status 500",
    "refused: NotFound, status 404",
    "refused: NotFound, status 404",
    "refused: IncusError, status 500",
]
