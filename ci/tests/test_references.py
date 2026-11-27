from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Repo

from potka_ci import references

RULES = references.load()

# Assembled at run time, so this file holds none of the references it tests.
SECTION = "see " + chr(0xA7) + "3.2"
STEP = "done in " + "P" + "0"
STEP_C = "the " + "P" + "3c skeleton"
STEP_S = "the " + "S" + "0 seed"
RECORDS = "per " + "PROGRESS" + ".md"
NOTES = "IMPLEMENTATION" + "_NOTES"
RULES_FILE = "CLAUDE" + ".md says"
ASSISTANT = "the " + "." + "claude/agents directory"
PROTOCOL = "the session " + "protocol"
SPEC = "potka-platform-" + "spec"
DECISION = "the owner's " + "decision"
DECISION_TYPOGRAPHIC = "per the owner" + chr(0x2019) + "s decision"
DECIDED = "the owner " + "decided"
SPEC_SECTION = "see section 3.5 of the " + "specification"


def ids(text: str) -> list[str]:
    return [finding.rsplit(": ", 1)[1] for finding in references.scan_text(text, RULES)]


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (SECTION, "section-sign"),
        (STEP, "plan-step"),
        (STEP_C, "plan-step"),
        (STEP_S, "plan-step"),
        (RECORDS, "records"),
        (NOTES, "records"),
        (RULES_FILE, "rules-file"),
        (ASSISTANT, "assistant-directory"),
        (PROTOCOL, "protocol"),
        (SPEC, "specification-files"),
        (DECISION, "owner-decision"),
        (DECISION_TYPOGRAPHIC, "owner-decision"),
        (DECIDED, "owner-decided"),
        (SPEC_SECTION, "specification-section"),
    ],
)
def test_each_pattern_reports(text: str, rule: str) -> None:
    assert ids(text) == [rule]


@pytest.mark.parametrize(
    "text",
    [
        "the p95 latency and the P95 latency",
        "version 1.0, contract v2, resolutions V1 to V7",
        "the file's owner and the owner group",
        "a .claude-like name and claude/x branches",
        "the session ends; the protocol of the API",
        "the specification of CloudEvents",
        "TODO" + "(verify) a digest",
    ],
)
def test_product_terms_pass(text: str) -> None:
    assert ids(text) == []


def test_paths_of_assistant_material_are_refused(repo: Repo) -> None:
    repo.write("." + "claude/settings.json", "{}\n")
    repo.write("docs/" + "CLAUDE" + ".md", "x\n")
    repo.write("notes/a.md", "x\n")
    outcome = references.run(repo.path)
    assert outcome.status == "failed"
    assert sorted(outcome.lines) == [
        "." + "claude/settings.json: path: assistant-settings",
        "docs/" + "CLAUDE" + ".md: path: rules-file",
        "notes/a.md: path: working-records",
    ]


def test_files_and_messages_are_checked(repo: Repo) -> None:
    repo.write("platform/a.py", f"# {STEP}\n")
    sha = repo.commit(f"A change\n\n{SECTION}")
    outcome = references.run(repo.path)
    assert outcome.status == "failed"
    assert outcome.lines == [
        "platform/a.py:1:11: plan-step",
        f"commit {sha[:12]}: message line 3:5: section-sign",
    ]


def test_the_patterns_file_does_not_match_itself() -> None:
    text = references.PATTERNS_FILE.read_text(encoding="utf-8")
    assert references.scan_text(text, RULES) == []


def test_unreadable_files_are_named_and_links_are_not_followed(repo: Repo) -> None:
    (repo.path / "blob.bin").write_bytes(b"\xff\xfe" + STEP.encode())
    repo.write("target.txt", "clean\n")
    (repo.path / "link.txt").symlink_to("target.txt")
    outcome = references.run(repo.path)
    assert outcome.status == "warning"
    assert "blob.bin: not checked (not UTF-8 text)" in outcome.lines


def test_a_shallow_checkout_fails(repo: Repo, tmp_path: Path) -> None:
    # The message with the reference is the one a shallow checkout lacks.
    sha = repo.commit(f"A change\n\n{SECTION}")
    repo.commit("Second")
    finding = f"commit {sha[:12]}: message line 3:5: section-sign"
    whole, shallow = tmp_path / "whole", tmp_path / "shallow"
    repo.git("clone", "-q", f"file://{repo.path}", str(whole))
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.path}", str(shallow))
    assert references.run(whole).lines == [finding]
    outcome = references.run(shallow)
    assert outcome.status == "failed"
    assert outcome.lines == ["a shallow checkout: fetch the whole history"]


def test_a_checkout_that_is_not_shallow_is_read(repo: Repo, tmp_path: Path) -> None:
    # A repository without a commit, and a second working tree of a whole one.
    empty, linked = tmp_path / "empty", tmp_path / "linked"
    repo.git("init", "-q", str(empty))
    (empty / "a.txt").write_text("clean\n", encoding="utf-8")
    repo.git("worktree", "add", "-q", "--detach", str(linked))
    assert references.run(empty).lines == ["references: 1 file(s) and 0 commit message(s) checked"]
    assert references.run(linked).status == "passed"
