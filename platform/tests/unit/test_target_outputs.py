"""The kept outputs of a target's programs: their files' form."""

from __future__ import annotations

from pathlib import Path

import pytest
from potka_incus.inside import Ended
from potka_testing import outputs
from potka_testing.outputs import Output


def test_a_kept_output_says_where_its_text_comes_from(tmp_path: Path) -> None:
    (tmp_path / "stat-ext4.txt").write_bytes(
        b"source: manual\nstatus: 0\nstream: stdout\n---\n524288:402113:ext2/ext3\n"
    )
    (tmp_path / "fallocate-full.txt").write_bytes(
        b"source: recorded\nrecorded: 2026-01-01 base/1.0.1\nstatus: 1\nstream: stderr\n---\n"
        b"fallocate: fallocate failed: No space left on device\n---\nmore\n"
    )
    (tmp_path / "notes.md").write_bytes(b"not an output")
    assert outputs.names(tmp_path) == ["fallocate-full", "stat-ext4"]
    assert outputs.manual(tmp_path) == ["stat-ext4"]
    plain = outputs.output("stat-ext4", tmp_path)
    assert plain == Output("stat-ext4", "manual", "", 0, "stdout", b"524288:402113:ext2/ext3\n")
    assert plain.ended() == Ended(0, b"524288:402113:ext2/ext3\n", b"")
    assert plain.text() == "524288:402113:ext2/ext3\n"
    full = outputs.output("fallocate-full", tmp_path)
    # Everything after the first separator is the program's, a line of dashes too.
    assert full.content == b"fallocate: fallocate failed: No space left on device\n---\nmore\n"
    assert (full.source, full.recorded, full.status) == ("recorded", "2026-01-01 base/1.0.1", 1)
    assert full.ended() == Ended(1, b"", full.content)


@pytest.mark.parametrize(
    "raw",
    [
        b"source: manual\nstatus: 0\nstream: stdout\n",
        b"source: manual\nstatus: 0\n---\nx",
        b"source: manual\nstatus: 0\nstream: stdout\nmore: x\n---\nx",
        b"source: manual\nstatus: 0\nstream: stdout\nrecorded: x\n---\nx",
        b"source: recorded\nstatus: 0\nstream: stdout\n---\nx",
        b"source: guessed\nstatus: 0\nstream: stdout\n---\nx",
        b"source: manual\nstatus: 0\nstream: both\n---\nx",
        b"source: manual\nstatus: -1\nstream: stdout\n---\nx",
        b"source: manual\nstatus: 256\nstream: stdout\n---\nx",
        b"source: manual\nstatus: 0\nstatus: 0\nstream: stdout\n---\nx",
        b"source manual\nstatus: 0\nstream: stdout\n---\nx",
    ],
)
def test_a_file_of_another_form_is_refused(raw: bytes) -> None:
    with pytest.raises(ValueError, match=r"^case: "):
        outputs.parse("case", raw)


def test_every_kept_output_of_the_repository_has_the_form() -> None:
    for name in outputs.names():
        kept = outputs.output(name)
        assert kept.name == name and kept.content, name
