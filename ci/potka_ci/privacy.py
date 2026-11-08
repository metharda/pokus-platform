"""Privacy scan: no personal or installation-specific data in the repository.

It reports, in the repository's files (tracked, or untracked and not
ignored: their paths, their contents, and the targets of symbolic links),
and, for every commit of the run's range (:func:`potka_ci.repo.push_range`:
on a branch other than main, every commit not on main yet; on main, the
push's commits), in its message, the paths it added and the lines it
added:

* e-mail addresses outside the RFC 2606 domains (example.com, example.org,
  example.net and their subdomains; the .test, .example, .invalid and
  .localhost top-level domains), except the SSH remotes of public forges,
  GitHub's own address and SSH algorithm names (at openssh.com and
  libssh.org);
* public IPv4 and IPv6 addresses and networks (the documentation and
  private ranges and multicast are allowed; a four-part version after a
  version comparison, as a TOML version value or in a distribution's file
  name is not an address; an address written with a prefix length but
  host bits set, as ``ip addr`` prints it, is an address), Tailscale
  addresses (single CGNAT addresses in ``100.64.0.0/10``, and addresses in
  ``fd7a:115c:a1e0::/48``; a network, as an address pool, and Tailscale's
  fixed resolver are allowed);
* Tailscale MagicDNS names (``*.ts.net``);
* MAC addresses outside the documentation range ``00:00:5e:00:53:00`` to
  ``00:00:5e:00:53:ff`` (the all-zero and broadcast addresses are allowed);
* UUIDs other than the nil UUID;
* SSH, PGP and PEM key material outside ``tests/fixtures/test-keys/``;
* access tokens (GitHub, Hugging Face, AWS, Tailscale);
* the terms of the maintainer's denylist, from the environment variable
  ``POTKA_PRIVACY_DENYLIST`` (newline- or comma-separated, matched as whole
  words in any letter case), applied to file contents, paths, link
  targets, commit messages and added lines, never to author or committer
  names (the commit check owns the identity). Without it, or with no term
  in it (CI passes a secret that is not set as an empty variable), the scan
  passes with a warning.

A finding names the place and the rule, never the matched text, so the
output does not spread what it found: where the place is a path, what a
rule matched in it is masked, and bytes that are not UTF-8 are escaped.
Text and paths are read as UTF-8, each byte that is not part of UTF-8 as
Latin-1. A file in the tree with a NUL byte, and an added file in history
whose added lines hold one, is named as not scanned (binary); history is
otherwise diffed as text, whatever the file's attributes.
A shallow checkout fails the scan: its history cannot be read.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

from potka_ci.outcome import Outcome, from_findings
from potka_ci.repo import (
    EMPTY_TREE,
    GitError,
    display_path,
    git,
    git_bytes,
    push_range,
    repository_files,
)

DENYLIST_ENV = "POTKA_PRIVACY_DENYLIST"
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = ("test", "example", "invalid", "localhost")
# Addresses of a public service, not of a person: GitHub's own (the
# committer of the merges made on github.com) and the SSH remotes of public
# forges.
SERVICE_ADDRESSES = frozenset(
    {
        "noreply@github.com",
        "git@github.com",
        "git@gitlab.com",
        "git@codeberg.org",
        "git@bitbucket.org",
    }
)
DOC_MAC_PREFIX = "00:00:5e:00:53:"
ALLOWED_MACS = frozenset({"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"})
NIL_UUID = "00000000-0000-0000-0000-000000000000"
IPV6_FULL_COLONS = 7
IPV6_MIN_GROUPS = 2
CGNAT = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
# Tailscale's resolver, the same in every tailnet.
TAILSCALE_RESOLVERS = frozenset(
    {ipaddress.ip_address("100.100.100.100"), ipaddress.ip_address("fd7a:115c:a1e0::53")}
)
TEST_KEYS = "/tests/fixtures/test-keys/"

EMAIL_RE = re.compile(
    r"(?<![\w.+%-])[\w.+%-]+@(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,})(?![\w-])"
)
# SSH algorithm names look like addresses at openssh.com and libssh.org:
# hyphenated words with a digit (ssh-ed25519, sk-ssh-ed25519,
# chacha20-poly1305, curve25519-sha256), never a person's name.
SSH_ALGORITHM_RE = re.compile(
    r"^(?=[a-z0-9-]*\d)[a-z0-9]+(?:-[a-z0-9]+)+@(?:openssh\.com|libssh\.org)$"
)
# An address may end a sentence: a "." after it ends the match, unless a
# digit follows (a five-part version or an OID is not an address). A "-"
# then a letter ends it too (a file name); a "-" then a digit is a range.
IPV4_RE = re.compile(
    r"(?<![\w.])(?P<addr>\d{1,3}(?:\.\d{1,3}){3})(?:/(?P<len>\d{1,2}))?"
    r"(?![\w]|-[A-Za-z_]|\.\d)"
)
IPV6_RE = re.compile(
    r"(?<![\w:.])(?P<addr>(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})"
    r"(?:/(?P<len>\d{1,3}))?(?![\w:]|\.\d)"
)
# A Python slice (xs[1::2]) has at most two colons; an address in brackets
# (a URL with a port, a YAML list) has more.
SLICE_COLONS = 2
# A four-part version is not an IPv4 address: a dotted quad right after a
# version comparison (==, >=, ~=, ...) or as a TOML version value, as in
# uv.lock and pyproject.toml, or in a distribution's file name
# (name-1.2.3.4.tar.gz, name-1.2.3.4-py3-none-any.whl).
VERSION_BEFORE_RE = re.compile(r'(?:[=<>~!]=|===|^\s*version\s*=\s*")$')
DIST_VERSION_RE = re.compile(
    r"[A-Za-z_]-(?P<version>\d+(?:\.\d+){3})(?=\.tar\.|\.zip|\.whl|-py\d|-cp\d|\.dist-info)"
)
TS_NET_RE = re.compile(r"(?<![\w-])(?:[A-Za-z0-9-]+\.)+ts\.net(?![\w-])", re.IGNORECASE)
MAC_RE = re.compile(r"(?<![\w:-])(?P<mac>(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![\w:-])")
UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])"
)
KEY_RES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE|PUBLIC) KEY-----"),
    re.compile(r"-----BEGIN PGP (?:PUBLIC|PRIVATE) KEY BLOCK-----"),
    re.compile(r"---- BEGIN SSH2 " r"PUBLIC KEY ----"),  # two literals: no match on this line
    re.compile(
        r"(?<![\w-])(?:sk-)?(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+)"
        r"(?:-cert-v01)?(?:@openssh\.com)?\s+AAAA[0-9A-Za-z+/]{16,}"
    ),
)
TOKEN_RES = (
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Tailscale key", re.compile(r"\btskey-[A-Za-z0-9-]{10,}")),
)


def is_example_domain(domain: str) -> bool:
    """Whether DOMAIN is reserved by RFC 2606.

    Args:
        domain: A domain name.

    Returns:
        True for example.com, example.org, example.net and their subdomains,
        and for names under .test, .example, .invalid and .localhost.
    """
    domain = domain.lower().rstrip(".")
    if domain.rsplit(".", 1)[-1] in RESERVED_TLDS:
        return True
    return any(domain == d or domain.endswith("." + d) for d in EXAMPLE_DOMAINS)


def is_test_key_path(path: str) -> bool:
    """Whether PATH lies below a ``tests/fixtures/test-keys/`` directory.

    Args:
        path: A path relative to the repository root.

    Returns:
        True for a test-key fixture.
    """
    return TEST_KEYS in "/" + PurePosixPath(path).as_posix()


def load_denylist(environ: dict[str, str] | None = None) -> list[str] | None:
    """The denylist terms, or None when the variable is not set or holds none.

    Args:
        environ: The environment (the process's own when None).

    Returns:
        The terms, or None.
    """
    value = (os.environ if environ is None else environ).get(DENYLIST_ENV, "")
    terms = [t.strip() for t in re.split(r"[\n,]", value) if t.strip()]
    return terms or None


def _ip_rule(text: str, length: str | None) -> str | None:
    """The rule an address or network breaks: "public", "tailscale" or None.

    A network (no host bits set) is judged as a network; an address written
    with a prefix length and host bits set (as ``ip addr`` prints an
    interface's address) is judged as the address it is.
    """
    try:
        if length is not None:
            interface = ipaddress.ip_interface(f"{text}/{length}")
            if interface.ip == interface.network.network_address and length not in ("32", "128"):
                net = interface.network
                return "public" if net.is_global and not net.is_multicast else None
            addr: ipaddress.IPv4Address | ipaddress.IPv6Address = interface.ip
        else:
            addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if addr in TAILSCALE_RESOLVERS:
        return None
    if (isinstance(addr, ipaddress.IPv4Address) and addr in CGNAT) or (
        isinstance(addr, ipaddress.IPv6Address) and addr in TAILSCALE_V6
    ):
        return "tailscale"
    return "public" if addr.is_global and not addr.is_multicast else None


def scan_spans(line: str, path: str, denylist: Sequence[str]) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, rule)`` for every finding in one line.

    Args:
        line: The line.
        path: The file it comes from (for the test-key rule), or "" for a message.
        denylist: The denylist terms.

    Yields:
        The 0-based span of what matched and a description of the rule.
    """
    for m in EMAIL_RE.finditer(line):
        address = m.group(0).lower()
        if address in SERVICE_ADDRESSES or SSH_ALGORITHM_RE.match(address):
            continue
        if not is_example_domain(m.group("domain")):
            yield m.start(), m.end(), "e-mail address outside the example domains"
    versions = {m.start("version") for m in DIST_VERSION_RE.finditer(line)}
    for m in IPV4_RE.finditer(line):
        if m.start() in versions or VERSION_BEFORE_RE.search(line[: m.start()]):
            continue
        rule = _ip_rule(m.group("addr"), m.group("len"))
        if rule == "public":
            yield m.start(), m.end(), "public IPv4 address (use 192.0.2.0/24 and the like)"
        elif rule == "tailscale":
            yield m.start(), m.end(), "Tailscale address (use 192.0.2.0/24)"
    for m in IPV6_RE.finditer(line):
        addr = m.group("addr")
        if "::" not in addr and addr.count(":") != IPV6_FULL_COLONS:
            continue
        if sum(1 for group in addr.split(":") if group) < IPV6_MIN_GROUPS:
            continue
        if line[m.start() - 1 : m.start()] == "[" and addr.count(":") <= SLICE_COLONS:
            continue
        rule = _ip_rule(addr, m.group("len"))
        if rule == "public":
            yield m.start(), m.end(), "public IPv6 address (use 2001:db8::/32)"
        elif rule == "tailscale":
            yield m.start(), m.end(), "Tailscale address (use 2001:db8::/32)"
    for m in TS_NET_RE.finditer(line):
        yield m.start(), m.end(), "Tailscale name (*.ts.net)"
    for m in MAC_RE.finditer(line):
        mac = m.group("mac").lower().replace("-", ":")
        if not mac.startswith(DOC_MAC_PREFIX) and mac not in ALLOWED_MACS:
            yield (
                m.start(),
                m.end(),
                "MAC address outside 00:00:5e:00:53:00 to 00:00:5e:00:53:ff",
            )
    for m in UUID_RE.finditer(line):
        if m.group(0) != NIL_UUID:
            yield m.start(), m.end(), "UUID other than the nil UUID"
    if not is_test_key_path(path):
        for key_re in KEY_RES:
            for m in key_re.finditer(line):
                yield m.start(), m.end(), "key material outside a test-key fixture"
    for name, token_re in TOKEN_RES:
        for m in token_re.finditer(line):
            yield m.start(), m.end(), name
    for term in denylist:
        for m in re.finditer(rf"(?<!\w){re.escape(term)}(?!\w)", line, re.IGNORECASE):
            yield m.start(), m.end(), "a term of the denylist"


def scan_line(line: str, path: str, denylist: Sequence[str]) -> Iterator[tuple[int, str]]:
    """Yield ``(column, rule)`` for every finding in one line.

    Args:
        line: The line.
        path: The file it comes from (for the test-key rule), or "" for a message.
        denylist: The denylist terms.

    Yields:
        The 1-based column and a description of the rule.
    """
    for start, _, rule in scan_spans(line, path, denylist):
        yield start + 1, rule


def masked(path: str, denylist: Sequence[str]) -> str:
    """PATH for printing: what a rule matched in it masked, non-UTF-8 bytes escaped.

    The rules read the path as :func:`path_text` does, one character per
    character of PATH, so a match masks the same characters here.

    Args:
        path: A path as ``os.fsdecode`` gives it.
        denylist: The denylist terms.

    Returns:
        The path to print.
    """
    text = path_text(path)
    chars = list(path)
    for start, end, _ in scan_spans(text, text, denylist):
        chars[start:end] = ["*"] * (end - start)
    return display_path("".join(chars))


def scan_text(text: str, path: str, denylist: Sequence[str]) -> list[str]:
    """Every finding in a text, as ``<line>:<column>: <rule>``.

    Args:
        text: The text.
        path: Its path (for the test-key rule), or "" for a message.
        denylist: The denylist terms.

    Returns:
        The findings.
    """
    return [
        f"{number}:{column}: {rule}"
        for number, line in enumerate(text.split("\n"), 1)
        for column, rule in scan_line(line, path, denylist)
    ]


def decode_bytes(data: bytes) -> str:
    """Bytes as text: UTF-8, and each byte that is not part of UTF-8 as Latin-1.

    A stray byte in a UTF-8 line changes only that character, so a term in
    the line's UTF-8 part is still found.

    Args:
        data: The bytes.

    Returns:
        The text.
    """
    text = data.decode("utf-8", "surrogateescape")
    return "".join(chr(ord(c) - 0xDC00) if "\udc80" <= c <= "\udcff" else c for c in text)


def path_text(name: str) -> str:
    """A path as the rules read it: bytes that are not UTF-8 as Latin-1.

    Args:
        name: A path as ``os.fsdecode`` gives it.

    Returns:
        The path's text.
    """
    return decode_bytes(name.encode("utf-8", "surrogateescape"))


def read_text(path: Path) -> str | None:
    """A file's text, as :func:`decode_bytes` reads it; None for a binary file.

    Args:
        path: The file.

    Returns:
        The text, or None when it holds a NUL byte.
    """
    data = path.read_bytes()
    if b"\0" in data:
        return None
    return decode_bytes(data)


def unquote_path(raw: bytes) -> str:
    """A path as git prints it in a diff header, unquoted.

    Args:
        raw: The path, possibly in git's C-style quotes.

    Returns:
        The path (bytes that are not UTF-8 kept as by ``os.fsdecode``).
    """
    raw = raw.rstrip(b"\t")  # git appends a TAB to a path with a space
    if raw.startswith(b'"') and raw.endswith(b'"'):
        raw = raw[1:-1].decode("ascii", "backslashreplace").encode("latin-1", "backslashreplace")
        raw = raw.decode("unicode_escape").encode("latin-1")
    return os.fsdecode(raw)


HUNK_RE = re.compile(rb"^@@ -\d+(?:,(?P<old>\d+))? \+\d+(?:,(?P<new>\d+))? @@")


def added_lines(patch: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(path, line)`` for every line a patch adds.

    The patch is ``git diff-tree -p -U0`` output. Lines are counted from the
    hunk headers, so an added line that looks like a header is still an
    added line. (The paths a commit adds are read separately: an empty new
    file has no hunk.)

    Args:
        patch: The patch.

    Yields:
        The new path of the file and the added line's bytes.
    """
    path = ""
    old = new = 0
    for raw in patch.split(b"\n"):
        if old > 0 or new > 0:
            if raw.startswith(b"+"):
                new -= 1
                yield path, raw[1:]
            elif raw.startswith(b"-"):
                old -= 1
            elif raw.startswith(b"\\"):
                continue  # "\ No newline at end of file"
            else:
                old -= 1
                new -= 1
            continue
        m = HUNK_RE.match(raw)
        if m:
            old = int(m.group("old") or 1)
            new = int(m.group("new") or 1)
        elif raw.startswith(b"+++ "):
            target = raw[4:]
            path = "" if target == b"/dev/null" else unquote_path(target).removeprefix("b/")


DIFF_TREE = (
    "-c",
    "core.quotePath=false",
    "diff-tree",
    "-r",
    "--no-renames",
    "--text",
    "--no-textconv",
    "--no-ext-diff",
    "--no-color",
)


def scan_history(
    root: Path, denylist: Sequence[str], commits: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Scan commits: each one's message, the paths it added and the lines it added.

    Each commit is diffed against its first parent (the empty tree for a
    root commit) with the plumbing ``git diff-tree``, as text, with fixed
    prefixes, so no diff setting of the machine changes what is scanned.
    The paths a commit adds come from ``--name-only -z`` (an empty new file
    has no hunk); an added file whose added lines hold a NUL byte is named
    as not scanned, as in the tree.

    Args:
        root: The working tree.
        denylist: The denylist terms.
        commits: The commits to scan.

    Returns:
        The findings and the warnings.
    """
    findings: list[str] = []
    warnings: list[str] = []
    for commit in commits:
        sha, *parents = git(root, ["rev-list", "--parents", "-n", "1", commit]).split()
        short = sha[:12]
        base = parents[0] if parents else EMPTY_TREE
        message = decode_bytes(git_bytes(root, ["log", "-1", "--format=%B", sha]))
        findings += [f"commit {short}: message line {f}" for f in scan_text(message, "", denylist)]
        names = git_bytes(root, [*DIFF_TREE, "-z", "--name-only", "--diff-filter=A", base, sha])
        for raw_name in names.split(b"\0"):
            if not raw_name:
                continue
            name = os.fsdecode(raw_name)
            text = path_text(name)
            findings += [
                f"commit {short}: path {masked(name, denylist)}: {rule}"
                for _, rule in scan_line(text, text, denylist)
            ]
        patch = git_bytes(
            root, [*DIFF_TREE, "-p", "-U0", "--src-prefix=a/", "--dst-prefix=b/", base, sha]
        )
        binary: set[str] = set()
        for path, raw_line in added_lines(patch):
            if b"\0" in raw_line:
                binary.add(path)
                continue
            findings += [
                f"commit {short}: {masked(path, denylist)}: added line: {rule}"
                for _, rule in scan_line(decode_bytes(raw_line), path_text(path), denylist)
            ]
        warnings += [
            f"commit {short}: {masked(path, denylist)}: not scanned (binary)"
            for path in sorted(binary)
        ]
    return findings, warnings


def run(
    root: Path,
    branch: str,
    before: str | None,
    after: str = "HEAD",
    environ: dict[str, str] | None = None,
) -> Outcome:
    """Scan the repository's files and the history of the run's range.

    Args:
        root: The working tree.
        branch: The branch pushed to.
        before: The branch's tip before the push, or None.
        after: The pushed commit.
        environ: The environment (the process's own when None).

    Returns:
        The outcome.
    """
    if git(root, ["rev-parse", "--is-shallow-repository"]).strip() != "false":
        return Outcome("failed", ["a shallow checkout: fetch the whole history"])
    try:
        commits, scope = push_range(root, branch, before, after)
    except GitError as exc:
        return Outcome("failed", [str(exc)])
    denylist = load_denylist(environ)
    terms = denylist or []
    findings: list[str] = []
    warnings: list[str] = []
    if denylist is None:
        warnings.append(f"{DENYLIST_ENV} is not set or empty: the denylist was not checked")
    files = repository_files(root)
    for name in files:
        path = root / name
        text_name = path_text(name)
        shown = masked(name, terms)
        findings += [f"{shown}: path: {rule}" for _, rule in scan_line(text_name, text_name, terms)]
        if path.is_symlink():
            target = os.readlink(path)
            findings += [
                f"{shown}: link target: {rule}"
                for _, rule in scan_line(path_text(target), text_name, terms)
            ]
            continue
        text = read_text(path)
        if text is None:
            warnings.append(f"{shown}: not scanned (binary)")
            continue
        findings += [f"{shown}:{f}" for f in scan_text(text, text_name, terms)]
    history, history_warnings = scan_history(root, terms, commits)
    findings += history
    warnings += history_warnings
    summary = f"privacy: {len(files)} file(s) and {len(commits)} commit(s) of {scope} scanned"
    return from_findings(findings, warnings, summary)
