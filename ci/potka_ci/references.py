"""The references check: the repository speaks only in the product's terms.

No file, path or commit message refers to a document, plan, record or
decision of the people building the platform, and no assistant's settings
or rules file is in the repository. The patterns are in
``ci/references.json``; each name there is split by an empty group, so the
file does not match its own patterns and is checked like any other. Every
commit message reachable from ``HEAD`` is checked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from potka_ci.outcome import Outcome, from_findings
from potka_ci.repo import display_path, git, git_ok, repository_files

PATTERNS_FILE = Path(__file__).resolve().parents[1] / "references.json"


@dataclass(frozen=True)
class Rule:
    """One pattern of the check.

    Attributes:
        id: Its name, printed with each finding.
        regex: The compiled expression.
    """

    id: str
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Rules:
    """Every pattern of the check.

    Attributes:
        text: Patterns for file contents and commit messages.
        paths: Patterns for paths relative to the repository root.
    """

    text: tuple[Rule, ...]
    paths: tuple[Rule, ...]


def load(path: Path = PATTERNS_FILE) -> Rules:
    """Read the patterns file.

    Args:
        path: The file.

    Returns:
        The rules.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return Rules(
        text=tuple(Rule(p["id"], re.compile(p["regex"])) for p in data["patterns"]),
        paths=tuple(Rule(p["id"], re.compile(p["regex"])) for p in data["paths"]),
    )


def scan_text(text: str, rules: Rules) -> list[str]:
    """Every finding in a text, as ``<line>:<column>: <rule id>``.

    Args:
        text: The text.
        rules: The rules.

    Returns:
        The findings.
    """
    return [
        f"{number}:{m.start() + 1}: {rule.id}"
        for number, line in enumerate(text.splitlines(), 1)
        for rule in rules.text
        for m in rule.regex.finditer(line)
    ]


def run(root: Path, rules: Rules | None = None) -> Outcome:
    """Check every path, file and commit message.

    Args:
        root: The working tree.
        rules: The rules (the patterns file's when None).

    Returns:
        The outcome.
    """
    rules = rules or load()
    findings: list[str] = []
    warnings: list[str] = []
    files = repository_files(root)
    for name in files:
        shown = display_path(name)
        findings += [f"{shown}: path: {rule.id}" for rule in rules.paths if rule.regex.search(name)]
        if (root / name).is_symlink():
            continue
        try:
            text = (root / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.append(f"{shown}: not checked (not UTF-8 text)")
            continue
        findings += [f"{shown}:{f}" for f in scan_text(text, rules)]
    commits = 0
    if git_ok(root, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]):
        for entry in git(root, ["log", "-z", "--format=%H%n%B", "HEAD"]).split("\0"):
            if not entry.strip():
                continue
            sha, _, message = entry.partition("\n")
            commits += 1
            findings += [f"commit {sha[:12]}: message line {f}" for f in scan_text(message, rules)]
    summary = f"references: {len(files)} file(s) and {commits} commit message(s) checked"
    return from_findings(findings, warnings, summary)
