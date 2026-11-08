from __future__ import annotations

from pathlib import Path

import pytest
from conftest import MAINTAINER, OTHER, Repo, person

from potka_ci import commits
from potka_ci.outcome import Outcome

GITHUB = ("GitHub", "noreply@github.com")
# Assembled at run time, so this file holds none of the lines it tests.
TRAILER = "Co-" + "Authored-By: " + OTHER[0] + " <" + OTHER[1] + ">"
GENERATED = "Generated" + " with a tool"
SIGNATURE = "gpgsig -----BEGIN SSH SIGNATURE-----\n U1NIU0lH\n -----END SSH SIGNATURE-----"


def check(repo: Repo, before: str | None = None, branch: str = "main") -> list[str]:
    outcome = commits.run(repo.path, branch, before, root_commit=repo.root)
    return outcome.lines if outcome.status == "failed" else []


def run(repo: Repo, before: str | None = None, branch: str = "main") -> Outcome:
    return commits.run(repo.path, branch, before, root_commit=repo.root)


def test_the_maintainer_s_commits_pass(repo: Repo) -> None:
    repo.commit("Add the manifest\n\nThe services and their sockets.")
    outcome = run(repo)
    assert outcome.status == "passed"
    assert outcome.lines == [
        "commits: 2 commit(s) checked (the whole history of HEAD (branch main))"
    ]


def test_the_expected_identity_is_the_root_commit_s_author_not_its_committer(
    tmp_path: Path,
) -> None:
    other = Repo(tmp_path / "other")
    root = other.commit("Initial commit", author=OTHER, committer=MAINTAINER)
    other.commit("A change", author=OTHER)
    assert commits.expected_identity(other.path, "HEAD", root) == OTHER
    # The root itself was committed by another identity than its author.
    assert commits.run(other.path, "main", None, root_commit=root).lines == [
        f"commit {root[:12]}: the committer is not the expected identity"
    ]


def test_a_history_with_another_root_fails(repo: Repo, tmp_path: Path) -> None:
    # An orphan branch brings its own root and its own author.
    orphan = Repo(tmp_path / "orphan")
    orphan.commit("Another root", author=OTHER)
    orphan.commit("A change", author=OTHER)
    outcome = commits.run(orphan.path, "main", None, root_commit=repo.root)
    assert outcome.status == "failed"
    other_root = orphan.git("rev-list", "--max-parents=0", "HEAD")
    assert outcome.lines == [
        f"the history has the root commit(s) {other_root[:12]},"
        f" not the repository's root commit {repo.root[:12]}"
    ]


@pytest.mark.parametrize(
    ("author", "committer", "reason"),
    [
        (OTHER, MAINTAINER, "the author is not the expected identity"),
        (MAINTAINER, OTHER, "the committer is not the expected identity"),
        ((MAINTAINER[0], OTHER[1]), MAINTAINER, "the author is not the expected identity"),
        ((OTHER[0], MAINTAINER[1]), MAINTAINER, "the author is not the expected identity"),
        (MAINTAINER, GITHUB, "the committer is not the expected identity"),
    ],
)
def test_another_identity_fails(
    repo: Repo, author: tuple[str, str], committer: tuple[str, str], reason: str
) -> None:
    sha = repo.commit("A change", author=author, committer=committer)
    assert check(repo) == [f"commit {sha[:12]}: {reason}"]


@pytest.mark.parametrize("line", [TRAILER, TRAILER.lower(), GENERATED, "Session" + ": x"])
def test_an_attribution_line_fails(repo: Repo, line: str) -> None:
    sha = repo.commit(f"A change\n\nIts body.\n{line}")
    (finding,) = check(repo)
    assert finding.startswith(f"commit {sha[:12]}: a message line matches the pattern ")


@pytest.mark.parametrize("header", ["gpgsig", "gpgsig-sha256"])
def test_a_signed_commit_fails(repo: Repo, header: str) -> None:
    signature = SIGNATURE.replace("gpgsig", header, 1)
    sha = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(MAINTAINER)}\n{signature}", b"Signed\n"
    )
    assert check(repo) == [f"commit {sha[:12]}: a signed commit (commits are unsigned)"]


def test_an_encoding_or_a_nul_byte_fails(repo: Repo) -> None:
    people = f"author {person(MAINTAINER)}\ncommitter {person(MAINTAINER)}"
    encoded = repo.raw_commit(people + "\nencoding ISO-8859-1", b"A change\n")
    nul = repo.raw_commit(people, b"A change\0\n\n" + TRAILER.encode() + b"\n")
    assert check(repo) == [
        f"commit {nul[:12]}: the commit object is not UTF-8 text or holds a NUL byte",
        f"commit {encoded[:12]}: the commit declares an encoding",
    ]


def test_a_merge_made_on_github_passes(repo: Repo) -> None:
    base = repo.head()
    side = repo.commit("A change on the branch")
    repo.git("update-ref", "HEAD", base)
    main = repo.commit("A change on main")
    repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}\n{SIGNATURE}",
        b"Merge pull request #1 from example-org/branch\n",
        parents=[main, side],
    )
    assert run(repo).status == "passed"


def test_a_github_merge_message_is_still_checked(repo: Repo) -> None:
    base = repo.head()
    side = repo.commit("A change on the branch")
    repo.git("update-ref", "HEAD", base)
    main = repo.commit("A change on main")
    sha = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}\n{SIGNATURE}",
        f"Merge pull request #1\n\n{TRAILER}\n".encode(),
        parents=[main, side],
    )
    (finding,) = check(repo)
    assert finding.startswith(f"commit {sha[:12]}: a message line matches the pattern ")


def test_a_github_merge_by_another_author_fails(repo: Repo) -> None:
    base = repo.head()
    side = repo.commit("A change on the branch")
    repo.git("update-ref", "HEAD", base)
    main = repo.commit("A change on main")
    sha = repo.raw_commit(
        f"author {person(OTHER)}\ncommitter {person(GITHUB)}\n{SIGNATURE}",
        b"Merge\n",
        parents=[main, side],
    )
    assert check(repo) == [
        f"commit {sha[:12]}: the author is not the expected identity",
        f"commit {sha[:12]}: the committer is not the expected identity",
        f"commit {sha[:12]}: a signed commit (commits are unsigned)",
    ]


def test_a_push_to_main_checks_before_to_after(repo: Repo) -> None:
    foreign = repo.commit("Older, foreign", author=OTHER, committer=MAINTAINER)
    repo.commit("New")
    assert run(repo, foreign).lines == [
        f"commits: 1 commit(s) checked ({foreign[:12]}..HEAD (branch main))"
    ]
    refused = [f"commit {foreign[:12]}: the author is not the expected identity"]
    assert check(repo, "0" * 40) == refused  # a new branch: the whole history
    assert check(repo, None) == refused
    assert check(repo, "f" * 40) == refused  # unknown to git: the whole history


# The event kinds of a push: the branch's range is every commit not on main.
def branch_with(repo: Repo, *commits_: tuple[str, tuple[str, str]]) -> list[str]:
    repo.set_origin_main(repo.root)
    repo.git("checkout", "-q", "-b", "moving/on")
    return [repo.commit(message, author=author) for message, author in commits_]


def test_the_first_push_of_a_branch_checks_every_commit_not_on_main(repo: Repo) -> None:
    foreign, _ = branch_with(repo, ("Foreign", OTHER), ("Good", MAINTAINER))
    outcome = commits.run(repo.path, "moving/on", "0" * 40, root_commit=repo.root)
    assert outcome.lines == [
        f"commit {foreign[:12]}: the author is not the expected identity",
        f"commit {foreign[:12]}: the committer is not the expected identity",
    ]


def test_a_later_push_still_checks_an_earlier_refused_commit(repo: Repo) -> None:
    foreign, earlier = branch_with(repo, ("Foreign", OTHER), ("Good", MAINTAINER))
    repo.commit("Later")
    outcome = commits.run(repo.path, "moving/on", earlier, root_commit=repo.root)
    assert outcome.status == "failed"
    assert outcome.lines[0] == f"commit {foreign[:12]}: the author is not the expected identity"


def test_a_push_after_a_skipped_run_covers_the_skipped_commits(repo: Repo) -> None:
    # The push of "Skipped" ran no CI; the next run checks it.
    (skipped,) = branch_with(repo, ("Skipped [skip ci]", OTHER))
    pushed = repo.commit("The next push")
    outcome = commits.run(repo.path, "moving/on", skipped, root_commit=repo.root)
    assert outcome.lines == [
        f"commit {skipped[:12]}: the author is not the expected identity",
        f"commit {skipped[:12]}: the committer is not the expected identity",
    ]
    assert pushed


def test_the_branch_range_needs_origin_main(repo: Repo) -> None:
    repo.git("checkout", "-q", "-b", "moving/on")
    outcome = commits.run(repo.path, "moving/on", None, root_commit=repo.root)
    assert outcome.lines == ["origin/main is not fetched: fetch it (the range is origin/main..)"]


def test_a_push_to_main_with_a_github_merge_checks_the_merged_commits(repo: Repo) -> None:
    (good,) = branch_with(repo, ("Good", MAINTAINER))
    foreign = repo.commit("Foreign", author=OTHER, committer=MAINTAINER)
    repo.git("checkout", "-q", "main")
    merge = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}\n{SIGNATURE}",
        b"Merge pull request #1 from example-org/moving/on\n",
        parents=[repo.root, foreign],
    )
    outcome = commits.run(repo.path, "main", repo.root, merge, root_commit=repo.root)
    assert outcome.lines == [f"commit {foreign[:12]}: the author is not the expected identity"]
    assert good


def test_a_clean_github_merge_to_main_passes(repo: Repo) -> None:
    (good,) = branch_with(repo, ("Good", MAINTAINER))
    repo.git("checkout", "-q", "main")
    merge = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}\n{SIGNATURE}",
        b"Merge pull request #1 from example-org/moving/on\n",
        parents=[repo.root, good],
    )
    outcome = commits.run(repo.path, "main", repo.root, merge, root_commit=repo.root)
    assert outcome.status == "passed"
    assert outcome.lines == [
        f"commits: 2 commit(s) checked ({repo.root[:12]}..{merge} (branch main))"
    ]


def test_a_shallow_checkout_fails(repo: Repo, tmp_path: Path) -> None:
    repo.commit("Second")
    shallow = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.path}", str(shallow))
    outcome = commits.run(shallow, "main", None, root_commit=repo.root)
    assert outcome.status == "failed"
    assert outcome.lines == ["a shallow checkout: fetch the whole history"]


def test_two_root_commits_fail(repo: Repo) -> None:
    first = repo.head()
    people = f"author {person(MAINTAINER)}\ncommitter {person(MAINTAINER)}"
    second = repo.raw_commit(people, b"Another root\n", [])
    repo.raw_commit(people, b"Join\n", [second, first])
    roots = [r[:12] for r in repo.git("rev-list", "--max-parents=0", "HEAD").split()]
    assert check(repo) == [
        f"the history has the root commit(s) {', '.join(roots)},"
        f" not the repository's root commit {repo.root[:12]}"
    ]


def test_the_patterns_file_must_be_usable(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.write_text("# a comment\n\nco-authored-by\n", encoding="utf-8")
    assert commits.load_patterns(good) == ["co-authored-by"]
    for text, reason in (
        ("co-authored-by\r\n", "a carriage return"),
        ("(co-authored\n", "grep cannot use the pattern"),
        ("# only a comment\n", "no pattern"),
    ):
        bad = tmp_path / "bad"
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(commits.PatternError, match=reason):
            commits.load_patterns(bad)


def test_the_patterns_are_the_ones_the_commit_check_reads() -> None:
    patterns = commits.load_patterns()
    assert (
        commits.matching_pattern("Add the gateway\n\nThe socket and its clients.", patterns) is None
    )
    assert commits.matching_pattern(f"A change\n\n{TRAILER}", patterns) is not None


# Lines of the kinds the patterns name, one or more for each pattern.
SIMILAR = (
    "Co" + "authored-by: researcher2",
    "Co" + chr(0xE9) + "Authored-By: x",
    "- Signed-" + "off-by: researcher2",
    "> Session" + ": https://example.com/s",
    "Generated" + " w/ a tool",
    "This change was " + "generated by a script",
    "AI-" + "generated",
    "Text written with the help of " + "Copilot",
    "aider" + ": add the parser",
    "The text came from " + "Claude.",
    "noreply@" + "anthropic.com",
    "https://" + "claude.ai/code/session_example",
)


def test_every_pattern_refuses_a_line_of_its_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check matches in the C locale whatever the environment's locale is.
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    patterns = commits.load_patterns()
    matched = set()
    for line in SIMILAR:
        pattern = commits.matching_pattern(f"A change\n\n{line}", patterns)
        assert pattern is not None, line
        for p in patterns:
            if commits.matching_pattern(line, [p]) is not None:
                matched.add(p)
    assert matched == set(patterns)


def test_the_patterns_match_in_the_c_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    # In the C locale a non-ASCII letter is a separator, as for the hook;
    # in C.UTF-8 it is a letter, and this line (no colon) would pass.
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    line = "Co" + chr(0xE9) + "authored" + chr(0xE9) + "by researcher2"
    assert commits.matching_pattern(line, commits.load_patterns()) is not None


def test_a_two_parent_merge_by_another_committer_fails(repo: Repo) -> None:
    base = repo.head()
    side = repo.commit("A change on the branch")
    repo.git("update-ref", "HEAD", base)
    main = repo.commit("A change on main")
    sha = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(OTHER)}\n{SIGNATURE}",
        b"Merge\n",
        parents=[main, side],
    )
    assert check(repo) == [
        f"commit {sha[:12]}: the committer is not the expected identity",
        f"commit {sha[:12]}: a signed commit (commits are unsigned)",
    ]


@pytest.mark.parametrize(
    ("header", "message", "reason"),
    [
        (
            f"author {person(MAINTAINER)}\nauthor {person(MAINTAINER)}"
            f"\ncommitter {person(MAINTAINER)}",
            b"Two authors\n",
            "the author is not the expected identity",
        ),
        (
            f"author {person(MAINTAINER)}",
            b"No committer\n",
            "the committer is not the expected identity",
        ),
        (
            f"author not an ident\ncommitter {person(MAINTAINER)}",
            b"Bad ident\n",
            "the author is not the expected identity",
        ),
        (
            f"author {person(MAINTAINER)}\ncommitter {person(MAINTAINER)}",
            b"caf\xe9\n",
            "the commit object is not UTF-8 text or holds a NUL byte",
        ),
    ],
)
def test_a_header_or_body_git_would_not_write_fails(
    repo: Repo, header: str, message: bytes, reason: str
) -> None:
    sha = repo.raw_commit(header, message)
    assert check(repo) == [f"commit {sha[:12]}: {reason}"]


def test_parents_are_counted_where_git_reads_them(repo: Repo) -> None:
    # A "parent" line after the committer does not make a merge.
    other = repo.commit("Another")
    sha = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}\nparent {other}\n{SIGNATURE}",
        b"Not a merge\n",
    )
    assert check(repo) == [
        f"commit {sha[:12]}: the committer is not the expected identity",
        f"commit {sha[:12]}: a signed commit (commits are unsigned)",
    ]
