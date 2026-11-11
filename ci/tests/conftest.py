"""Shared fixtures: isolated git repositories with placeholder identities."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

MAINTAINER = ("Researcher One", "researcher1@example.com")
OTHER = ("Researcher Two", "researcher2@example.com")


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """No git configuration or identity of the machine, and no GitHub Actions variables.

    The checks read the branch and the range from the GITHUB_* variables
    when CI runs them; a test sets the ones it needs.
    """
    home = tmp_path_factory.mktemp("home")
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith(("GIT_", "GITHUB_")) or key == "POTKA_PRIVACY_DENYLIST":
            del os.environ[key]
    os.environ.update(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
        GIT_CONFIG_NOSYSTEM="1",
    )
    yield
    os.environ.clear()
    os.environ.update(saved)


class Repo:
    """A git repository for one test."""

    root: str = ""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict[str, str] | None = None, data: bytes | None = None) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.path), *args],
            env={**os.environ, **(env or {})},
            input=data,
            capture_output=True,
            check=True,
        )
        return done.stdout.decode().strip()

    def commit(
        self,
        message: str = "A change",
        author: tuple[str, str] = MAINTAINER,
        committer: tuple[str, str] | None = None,
    ) -> str:
        committer = committer or author
        self.git(
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            message,
            env={
                "GIT_AUTHOR_NAME": author[0],
                "GIT_AUTHOR_EMAIL": author[1],
                "GIT_COMMITTER_NAME": committer[0],
                "GIT_COMMITTER_EMAIL": committer[1],
            },
        )
        return self.git("rev-parse", "HEAD")

    def raw_commit(self, header: str, message: bytes, parents: list[str] | None = None) -> str:
        """Write a commit object by hand and move HEAD to it."""
        tree = self.git("rev-parse", "HEAD^{tree}")
        lines = [f"tree {tree}"]
        lines += [f"parent {p}" for p in (parents if parents is not None else [self.head()])]
        # The header, a blank line, then the message (as git writes a commit).
        data = ("\n".join(lines) + "\n" + header + "\n\n").encode() + message
        sha = self.git("hash-object", "-t", "commit", "-w", "--literally", "--stdin", data=data)
        self.git("update-ref", "HEAD", sha)
        return sha

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def set_origin_main(self, sha: str) -> None:
        """Make refs/remotes/origin/main point at SHA, as a fetch of main would."""
        self.git("update-ref", "refs/remotes/origin/main", sha)

    @classmethod
    def at(cls, path: Path) -> Repo:
        """An existing repository."""
        repo = cls.__new__(cls)
        repo.path = path
        return repo

    @classmethod
    def head_of(cls, path: Path) -> str:
        return cls.at(path).head()

    def write(self, name: str, text: str) -> Path:
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def person(who: tuple[str, str]) -> str:
    """An author or committer header value."""
    return f"{who[0]} <{who[1]}> 1700000000 +0000"


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """A repository with the maintainer's root commit."""
    r = Repo(tmp_path / "repo")
    r.root = r.commit("Initial commit")
    return r
