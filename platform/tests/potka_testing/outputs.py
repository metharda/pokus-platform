"""What a program in a target prints and how it ends, kept as files for the tests.

The tests of the fault primitives and probes script a target's programs.
Every output they script is a file of ``tests/fixtures/target-outputs/``,
so that all of them can be seen in one place and each says where its text
comes from:

* ``source: manual``: written from the program's documentation;
* ``source: recorded``, with ``recorded: <date> <image>``: captured from a
  real target.

A file is a header of ``key: value`` lines, a line ``---``, and then the
bytes of one stream, exactly as the program wrote them. The header has
``source``, ``status`` (the exit status) and ``stream`` (``stdout`` or
``stderr``; the other stream is empty), and ``recorded`` for a recorded
one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from potka_incus.inside import Ended

OUTPUTS = Path(__file__).resolve().parents[1] / "fixtures" / "target-outputs"
SEPARATOR = b"---\n"
SOURCES = ("manual", "recorded")
STREAMS = ("stdout", "stderr")


@dataclass(frozen=True)
class Output:
    """One kept output.

    Attributes:
        name: The file's name without its suffix.
        source: ``manual`` or ``recorded``.
        recorded: For a recorded one, when and from which image.
        status: The program's exit status.
        stream: The stream the bytes were written to.
        content: The bytes.
    """

    name: str
    source: str
    recorded: str
    status: int
    stream: str
    content: bytes

    def ended(self) -> Ended:
        """How a scripted command ends with this output."""
        if self.stream == "stdout":
            return Ended(self.status, self.content, b"")
        return Ended(self.status, b"", self.content)

    def text(self) -> str:
        """The bytes as text."""
        return self.content.decode("utf-8")


def parse(name: str, raw: bytes) -> Output:
    """The output a file's bytes hold.

    Raises:
        ValueError: The file has not the form this module's first lines give.
    """
    head, separator, content = raw.partition(SEPARATOR)
    if not separator:
        raise ValueError(f"{name}: no line --- after the header")
    fields: dict[str, str] = {}
    for line in head.decode("utf-8").splitlines():
        key, colon, value = line.partition(": ")
        if not colon or key in fields:
            raise ValueError(f"{name}: a header line is 'key: value', each key once")
        fields[key] = value
    wanted = {"source", "status", "stream"} | (
        {"recorded"} if fields.get("source") == "recorded" else set()
    )
    if set(fields) != wanted:
        raise ValueError(f"{name}: the header has {sorted(fields)}, not {sorted(wanted)}")
    if fields["source"] not in SOURCES or fields["stream"] not in STREAMS:
        raise ValueError(f"{name}: not a source or not a stream")
    if not fields["status"].isdigit() or not 0 <= int(fields["status"]) <= 255:
        raise ValueError(f"{name}: not an exit status")
    return Output(
        name,
        fields["source"],
        fields.get("recorded", ""),
        int(fields["status"]),
        fields["stream"],
        content,
    )


def names(directory: Path = OUTPUTS) -> list[str]:
    """The name of every kept output, sorted."""
    return sorted(path.stem for path in directory.glob("*.txt"))


def output(name: str, directory: Path = OUTPUTS) -> Output:
    """The kept output of that name."""
    return parse(name, (directory / f"{name}.txt").read_bytes())


def manual(directory: Path = OUTPUTS) -> list[str]:
    """The names of the outputs that no real target has confirmed yet."""
    return [name for name in names(directory) if output(name, directory).source == "manual"]
