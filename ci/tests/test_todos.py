from __future__ import annotations

from conftest import Repo

from potka_ci import todos


def test_no_marker_passes(repo: Repo) -> None:
    repo.write("a.py", "x = 1\n")
    assert todos.run(repo.path).status == "passed"


def test_a_marker_is_a_warning_naming_its_place(repo: Repo) -> None:
    repo.write("docs/a.md", "line\nversion 1.0 " + todos.MARKER + "\n")
    outcome = todos.run(repo.path)
    assert outcome.status == "warning"
    assert outcome.lines[0] == f"docs/a.md:2: {todos.MARKER} open"


def test_an_unreadable_file_is_named(repo: Repo) -> None:
    (repo.path / "latin1.txt").write_bytes("caf\xe9 ".encode("latin-1") + todos.MARKER.encode())
    outcome = todos.run(repo.path)
    assert outcome.status == "warning"
    assert "latin1.txt: not read (not UTF-8 text, or unreadable)" in outcome.lines
