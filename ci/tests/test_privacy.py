from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import MAINTAINER, Repo, person

from potka_ci import privacy

# Assembled at run time, so this file holds no address the scan would report.
PUBLIC_IPV4 = "8.8" + ".8.8"
PUBLIC_IPV6 = "2606" + ":4700::1111"
REAL_EMAIL = "someone" + "@" + "nonexistent-lab-0123.org"
MAC = "3c:22:fb" + ":12:34:56"
UUID = "123e4567-e89b" + "-12d3-a456-426614174000"
PRIVATE_KEY = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
SSH_KEY = "ssh-ed25519 " + "AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExample"
GITHUB_TOKEN = "ghp_" + "A" * 36
FOUR_PART_VERSION = "1.7" + ".12.25"
GITHUB = ("GitHub", "noreply@github.com")


def rules(line: str, path: str = "", denylist: tuple[str, ...] = ()) -> list[str]:
    return [rule for _, rule in privacy.scan_line(line, path, denylist)]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (f"contact {REAL_EMAIL}", "e-mail address outside the example domains"),
        (f"server {PUBLIC_IPV4}", "public IPv4 address (use 192.0.2.0/24 and the like)"),
        (f"net {PUBLIC_IPV4}/24", "public IPv4 address (use 192.0.2.0/24 and the like)"),
        (f"server {PUBLIC_IPV6}", "public IPv6 address (use 2001:db8::/32)"),
        (f"nic {MAC}", "MAC address outside 00:00:5e:00:53:00 to 00:00:5e:00:53:ff"),
        (f"id {UUID}", "UUID other than the nil UUID"),
        (PRIVATE_KEY, "key material outside a test-key fixture"),
        (SSH_KEY, "key material outside a test-key fixture"),
        (f"token {GITHUB_TOKEN}", "GitHub token"),
        ("hf_" + "a" * 34, "Hugging Face token"),
        ("AKIA" + "ABCDEFGHIJKLMNOP", "AWS access key"),
        ("tskey-" + "auth-k123456789", "Tailscale key"),
        ("github_pat_" + "A" * 30, "GitHub token"),
        ("-----BEGIN PGP " + "PUBLIC KEY BLOCK-----", "key material outside a test-key fixture"),
        ("---- BEGIN SSH2 " + "PUBLIC KEY ----", "key material outside a test-key fixture"),
        ("-----BEGIN " + "PUBLIC KEY-----", "key material outside a test-key fixture"),
        (
            "sk-ssh-ed25519@openssh.com " + "AAAAGnNrLXNzaC1lZDI1NTE5QG9wZW5zc2guY29t",
            "key material outside a test-key fixture",
        ),
        (
            "rsync -a alice@" + "gpu01.nonexistent-lab-0123.org:/srv .",
            "e-mail address outside the example domains",
        ),
        (
            "The gateway is " + PUBLIC_IPV4 + ".",
            "public IPv4 address (use 192.0.2.0/24 and the like)",
        ),
        (PUBLIC_IPV4 + ".nip.io", "public IPv4 address (use 192.0.2.0/24 and the like)"),
        ("resolver " + PUBLIC_IPV6 + ".", "public IPv6 address (use 2001:db8::/32)"),
        (
            "tailscale ssh researcher1@host " + "100.101" + ".102.103",
            "Tailscale address (use 192.0.2.0/24)",
        ),
        ("gpu01.tail-scale" + ".ts.net", "Tailscale name (*.ts.net)"),
        (
            "inet " + "100.101" + ".102.103/32 scope global tailscale0",
            "Tailscale address (use 192.0.2.0/24)",
        ),
        ("inet " + "100.101" + ".102.103/10", "Tailscale address (use 192.0.2.0/24)"),
        (
            "tailscale ip -6: " + "fd7a:115c:a1e0" + ":ab12::1",
            "Tailscale address (use 2001:db8::/32)",
        ),
        ("curl http://[" + PUBLIC_IPV6 + "]:8080/", "public IPv6 address (use 2001:db8::/32)"),
        ("dns: [" + PUBLIC_IPV6 + "]", "public IPv6 address (use 2001:db8::/32)"),
        ("host-" + PUBLIC_IPV4, "public IPv4 address (use 192.0.2.0/24 and the like)"),
        ("Reported by someone@" + "openssh.com", "e-mail address outside the example domains"),
        (
            "ssh-ed25519-cert-v01@openssh.com " + "AAAAIHNzaC1lZDI1NTE5LWNlcnQtdjAx",
            "key material outside a test-key fixture",
        ),
    ],
)
def test_each_rule_reports(line: str, rule: str) -> None:
    assert rule in rules(line)


@pytest.mark.parametrize(
    "line",
    [
        "researcher1@example.com and ops@lab.example.org",
        "git@github.com:example-org/repo.git",
        "addresses 192.0.2.10, 198.51.100.0/24, 203.0.113.5, 10.0.0.1, 127.0.0.1",
        "2001:db8::1 and fe80::1 and ::1",
        "nic 00:00:5e:00:53:01 and 00:00:00:00:00:00",
        "id 00000000-0000-0000-0000-000000000000",
        f'version = "{FOUR_PART_VERSION}"',
        f'  "a-tool=={FOUR_PART_VERSION}",',
        f'{{ name = "a-tool", specifier = ">={FOUR_PART_VERSION}" }}',
        "commits made on github.com have noreply@github.com as their committer",
        "sha256:9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6",
        "the time is 12:34:56 and the ratio 1:2:3",
        "admin@lab.test, ops@host.example, x@y.invalid, root@localhost.localhost",
        "ssh -T git@github.com and git@github.com:example-org/repo.git",
        "sk-ssh-ed25519@openssh.com is an algorithm name",
        "evens = values[::2]; odd = xs[1::2]",
        "a label ::2 and a scope ::1",
        "broadcast ff:ff:ff:ff:ff:ff and multicast 224.0.0.251 and ff02::1",
        "an address pool 100.64.0.0/10 and the resolver 100.100.100.100",
        "git@gitlab.com:example-org/a.git, git@codeberg.org:example-org/b.git",
        "kex curve25519-sha256@libssh.org, cipher chacha20-poly1305@openssh.com",
        f"actionlint_py-{FOUR_PART_VERSION}.tar.gz and a_tool-{FOUR_PART_VERSION}-py3",
        "OID 1.3.6.1.4.1 and version 1.2.3.4.5",
    ],
)
def test_allowed_values_pass(line: str) -> None:
    assert rules(line) == []


def test_a_four_part_version_elsewhere_is_an_address() -> None:
    assert rules(f"host {FOUR_PART_VERSION}") == [
        "public IPv4 address (use 192.0.2.0/24 and the like)"
    ]


def test_key_material_is_allowed_in_a_test_key_fixture_only() -> None:
    assert rules(PRIVATE_KEY, "platform/tests/fixtures/test-keys/key") == []
    for path in (
        "platform/tests/keys/key",
        "platform/tests/fixtures/key",
        "platform/test-keys/fixtures/key",
        "platform/packages/a/src/fixtures/test-keys/key",
    ):
        assert rules(PRIVATE_KEY, path) == ["key material outside a test-key fixture"], path


def test_the_denylist_matches_whole_words_in_any_case() -> None:
    assert rules("Lab Alpha-Host here", denylist=("alpha-host",)) == ["a term of the denylist"]
    assert rules("alpha-hosts", denylist=("alpha-host",)) == []


def test_load_denylist() -> None:
    assert privacy.load_denylist({}) is None
    assert privacy.load_denylist({privacy.DENYLIST_ENV: ""}) is None
    assert privacy.load_denylist({privacy.DENYLIST_ENV: " ,\n"}) is None
    assert privacy.load_denylist({privacy.DENYLIST_ENV: "a, b\nc\n"}) == ["a", "b", "c"]


def test_run_reports_place_and_rule_never_the_value(repo: Repo) -> None:
    repo.write("docs/page.md", f"first\nwrite to {REAL_EMAIL}\n")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "failed"
    assert outcome.lines == ["docs/page.md:2:10: e-mail address outside the example domains"]
    assert all(REAL_EMAIL not in line for line in outcome.lines)


def test_run_scans_commit_messages(repo: Repo) -> None:
    sha = repo.commit(f"A change\n\nAsked by {REAL_EMAIL}")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "Alpha"})
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"commit {sha[:12]}: message line 3:10: e-mail address outside the example domains"
    ]


def test_run_without_the_denylist_warns(repo: Repo) -> None:
    repo.write("a.txt", "clean\n")
    for environ in ({}, {privacy.DENYLIST_ENV: ""}):
        outcome = privacy.run(repo.path, "main", None, environ=environ)
        assert outcome.status == "warning"
        assert outcome.lines[0] == (
            "POTKA_PRIVACY_DENYLIST is not set or empty: the denylist was not checked"
        )


def test_run_with_the_denylist_passes_a_clean_repository(repo: Repo) -> None:
    repo.write("a.txt", "clean\n")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "passed"
    assert outcome.lines == [
        "privacy: 1 file(s) and 1 commit(s) of the whole history of HEAD (branch main) scanned"
    ]


def test_run_names_a_file_it_cannot_read(repo: Repo) -> None:
    (repo.path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "warning"
    assert "blob.bin: not scanned (binary)" in outcome.lines


def test_ignored_files_are_not_scanned(repo: Repo) -> None:
    repo.write(".gitignore", ".venv/\n")
    repo.write(".venv/lib/site.py", f"x = '{REAL_EMAIL}'\n")
    assert (
        privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"}).status
        == "passed"
    )


def test_paths_link_targets_and_latin1_files_are_scanned(repo: Repo) -> None:
    repo.write("docs/" + "alpha-host" + "-notes.md", "clean\n")
    (repo.path / "data").symlink_to("/srv/" + "alpha-host" + "/data")
    (repo.path / "latin1.txt").write_bytes(("caf" + chr(0xE9) + " " + REAL_EMAIL).encode("latin-1"))
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "failed"
    assert sorted(outcome.lines) == [
        "data: link target: a term of the denylist",
        "docs/**********-notes.md: path: a term of the denylist",
        "latin1.txt:1:6: e-mail address outside the example domains",
    ]


def test_lines_added_in_history_are_scanned(repo: Repo) -> None:
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    repo.git("add", "-A")
    added = repo.commit("Add a page")
    repo.write("docs/a.md", "clean\n")
    repo.git("add", "-A")
    repo.commit("Clean the page")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        f"commit {added[:12]}: docs/a.md: added line: e-mail address outside the example domains"
    ]


def test_the_denylist_applies_to_commit_messages(repo: Repo) -> None:
    sha = repo.commit("A change\n\nTested on " + "Alpha-Host")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [f"commit {sha[:12]}: message line 3:11: a term of the denylist"]


def test_a_shallow_checkout_fails(repo: Repo, tmp_path: Path) -> None:
    repo.commit("Second")
    shallow = tmp_path / "shallow"
    repo.git("clone", "-q", "--depth", "1", f"file://{repo.path}", str(shallow))
    outcome = privacy.run(shallow, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "failed"
    assert outcome.lines == ["a shallow checkout: fetch the whole history"]


def test_an_address_range_is_two_addresses() -> None:
    first, second = "8.8.4" + ".10", "8.8.4" + ".20"
    assert rules(f"ipv4.ovn.ranges: {first}-{second}") == [
        "public IPv4 address (use 192.0.2.0/24 and the like)",
        "public IPv4 address (use 192.0.2.0/24 and the like)",
    ]


def test_a_link_target_is_scanned_with_every_rule(repo: Repo) -> None:
    (repo.path / "share").symlink_to("/net/" + PUBLIC_IPV4 + "/share")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        "share: link target: public IPv4 address (use 192.0.2.0/24 and the like)"
    ]


def test_a_file_name_that_is_not_utf8_is_scanned_and_printed_escaped(repo: Repo) -> None:
    (repo.path / "caf\udce9-alpha-host.txt").write_text("clean\n", encoding="utf-8")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == ["caf\\xe9-**********.txt: path: a term of the denylist"]


def commit_all(repo: Repo, message: str) -> str:
    repo.git("add", "-A")
    return repo.commit(message)


@pytest.mark.parametrize(
    ("name", "content", "shown"),
    [
        (
            "docs/caf" + chr(0xE9) + ".md",
            f"write to {REAL_EMAIL}\n",
            "docs/caf" + chr(0xE9) + ".md",
        ),
        ('docs/a "b".md', f"write to {REAL_EMAIL}\n", 'docs/a "b".md'),
        ("docs/a b.md", f"write to {REAL_EMAIL}\n", "docs/a b.md"),
        (
            "docs/page.md",
            f"intro\n++ {REAL_EMAIL} on a line that looks like a header\n",
            "docs/page.md",
        ),
        ("docs/page.md", f"intro\x0cwrite to {REAL_EMAIL}\n", "docs/page.md"),
    ],
)
def test_history_finds_lines_added_and_removed_later(
    repo: Repo, name: str, content: str, shown: str
) -> None:
    repo.write(name, content)
    added = commit_all(repo, "Add a page")
    (repo.path / name).unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"commit {added[:12]}: {shown}: added line: e-mail address outside the example domains"
    ]


def test_history_reads_binary_attributes_and_renames_as_text(repo: Repo) -> None:
    repo.write(".gitattributes", "*.json -diff\n")
    repo.write("data.json", f'{{"to": "{REAL_EMAIL}"}}\n')
    added = commit_all(repo, "Add data")
    repo.git("mv", "data.json", "moved.json")
    moved = commit_all(repo, "Move it")
    (repo.path / "moved.json").unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        f"commit {moved[:12]}: moved.json: added line: e-mail address outside the example domains",
        f"commit {added[:12]}: data.json: added line: e-mail address outside the example domains",
    ]


def test_history_scans_paths_and_merges(repo: Repo) -> None:
    repo.write("notes/alpha-host-setup.md", "clean\n")
    named = commit_all(repo, "Add notes")
    repo.git("mv", "notes/alpha-host-setup.md", "notes/setup.md")
    commit_all(repo, "Rename")
    base = repo.head()
    repo.git("checkout", "-q", "-b", "side")
    repo.write("side.md", f"write to {REAL_EMAIL}\n")
    side = commit_all(repo, "On the side")
    repo.git("checkout", "-q", "main")
    repo.git(
        "-c",
        "commit.gpgsign=false",
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "Merge",
        "side",
        env={
            "GIT_AUTHOR_NAME": "Researcher One",
            "GIT_AUTHOR_EMAIL": "researcher1@example.com",
            "GIT_COMMITTER_NAME": "Researcher One",
            "GIT_COMMITTER_EMAIL": "researcher1@example.com",
        },
    )
    merge = repo.head()
    assert base
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert sorted(outcome.lines) == sorted(
        [
            f"commit {named[:12]}: path notes/**********-setup.md: a term of the denylist",
            f"commit {side[:12]}: side.md: added line: e-mail address outside the example domains",
            f"commit {merge[:12]}: side.md: added line: e-mail address outside the example domains",
            "side.md:1:10: e-mail address outside the example domains",
        ]
    )


# No diff, color or quoting setting of the machine changes what the history
# scan reads. Plumbing git diff-tree ignores these porcelain settings itself
# (the fixed options keep it so), and a quoted path is unquoted; the one
# setting it does read, a -diff attribute, is the binary-attribute test above.
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("diff.noprefix", "true"),
        ("diff.mnemonicPrefix", "true"),
        ("diff.relative", "true"),
        ("diff.external", "false"),
        ("color.ui", "always"),
        ("color.diff", "always"),
        ("core.quotePath", "true"),
    ],
)
def test_a_diff_setting_of_the_machine_changes_nothing(repo: Repo, key: str, value: str) -> None:
    repo.git("config", key, value)
    name = "docs/caf" + chr(0xE9) + ".md"
    repo.write(name, f"write to {REAL_EMAIL}\n")
    added = commit_all(repo, "Add a page")
    (repo.path / name).unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        f"commit {added[:12]}: {name}: added line: e-mail address outside the example domains"
    ]


def test_lines_are_counted_at_newlines_only(repo: Repo) -> None:
    # A form feed or a line separator inside a line does not start a new line.
    repo.write("docs/a.md", f"intro\x0cmore\u2028still\nwrite to {REAL_EMAIL}\n")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == ["docs/a.md:2:10: e-mail address outside the example domains"]


# The history part follows the range; the work tree is scanned in full.
def test_a_branch_run_scans_the_history_not_on_main_and_the_whole_tree(repo: Repo) -> None:
    repo.write("docs/old.md", f"write to {REAL_EMAIL}\n")
    on_main = commit_all(repo, "On main already")
    (repo.path / "docs/old.md").unlink()
    commit_all(repo, "Removed on main")
    repo.set_origin_main(repo.head())
    repo.git("checkout", "-q", "-b", "moving/on")
    repo.write("docs/new.md", f"mail {REAL_EMAIL}\n")
    added = commit_all(repo, "On the branch")
    outcome = privacy.run(repo.path, "moving/on", "0" * 40, environ={privacy.DENYLIST_ENV: "x-y"})
    assert outcome.lines == [
        "docs/new.md:1:6: e-mail address outside the example domains",
        f"commit {added[:12]}: docs/new.md: added line: e-mail address outside the example domains",
    ]
    assert on_main


def test_a_run_without_a_push_event_on_main_scans_no_history_and_the_whole_tree(
    repo: Repo,
) -> None:
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    added = commit_all(repo, "Pushed already")
    repo.set_origin_main(added)
    environ = {privacy.DENYLIST_ENV: "x-y"}
    found = "docs/a.md:1:10: e-mail address outside the example domains"
    outcome = privacy.run(repo.path, "main", None, environ=environ, pushed=False)
    assert outcome.lines == [found]
    # A push to main without before: the whole history.
    assert privacy.run(repo.path, "main", None, environ=environ).lines == [
        found,
        f"commit {added[:12]}: docs/a.md: added line: e-mail address outside the example domains",
    ]


def test_a_push_to_main_scans_its_range(repo: Repo) -> None:
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    added = commit_all(repo, "Add")
    (repo.path / "docs/a.md").unlink()
    removed = commit_all(repo, "Remove")
    environ = {privacy.DENYLIST_ENV: "x-y"}
    assert privacy.run(repo.path, "main", added, removed, environ=environ).status == "passed"
    assert privacy.run(repo.path, "main", "0" * 40, removed, environ=environ).lines == [
        f"commit {added[:12]}: docs/a.md: added line: e-mail address outside the example domains"
    ]


def test_the_denylist_is_not_applied_to_identities(repo: Repo) -> None:
    # The commit check owns the author and committer; the scan reads neither.
    repo.commit("A change", author=("Alpha-Host", "researcher2@example.com"))
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "passed"


@pytest.mark.parametrize("content", ["", "\n\nclean\n"], ids=["empty", "blank-lines"])
def test_history_scans_the_path_of_an_empty_file_once(repo: Repo, content: str) -> None:
    # An empty new file has no hunk; a blank added line is not a path.
    repo.write("notes/alpha-host.md", content)
    added = commit_all(repo, "Add notes")
    (repo.path / "notes/alpha-host.md").unlink()
    commit_all(repo, "Remove them")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        f"commit {added[:12]}: path notes/**********.md: a term of the denylist"
    ]


def test_history_names_an_added_file_it_cannot_read_as_text(repo: Repo) -> None:
    (repo.path / "docs").mkdir()
    (repo.path / "docs/u.txt").write_bytes(f"write to {REAL_EMAIL}\n".encode("utf-16"))
    added = commit_all(repo, "Add a page")
    (repo.path / "docs/u.txt").unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.status == "warning"
    assert outcome.lines == [
        f"commit {added[:12]}: docs/u.txt: not scanned (binary)",
        "privacy: 0 file(s) and 3 commit(s) of the whole history of HEAD (branch main) scanned",
    ]


def test_history_reads_a_change_at_a_last_line_without_a_newline(repo: Repo) -> None:
    (repo.path / "a.md").write_bytes(b"one\ntwo")
    commit_all(repo, "Add a page")
    (repo.path / "a.md").write_bytes(f"one\ntwo {REAL_EMAIL}".encode())
    changed = commit_all(repo, "Change its last line")
    (repo.path / "a.md").unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: "alpha-host"})
    assert outcome.lines == [
        f"commit {changed[:12]}: a.md: added line: e-mail address outside the example domains"
    ]


# A byte that is not UTF-8 changes only its own character: a term spelled in
# Latin-1, or in UTF-8 next to a stray byte, is still found.
LATIN1_NAME = b"caf\xe9-host.txt"
MIXED_LINE = b"z\xc3\xbcrich-host \xff\n"
NON_ASCII_TERMS = "caf" + chr(0xE9) + "-host, z" + chr(0xFC) + "rich-host"


def test_non_ascii_terms_are_found_next_to_bytes_that_are_not_utf8(repo: Repo) -> None:
    (repo.path / os.fsdecode(LATIN1_NAME)).write_bytes(MIXED_LINE)
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: NON_ASCII_TERMS})
    assert outcome.lines == [
        "*********.txt: path: a term of the denylist",
        "*********.txt:1:1: a term of the denylist",
    ]


def test_history_finds_non_ascii_terms_next_to_bytes_that_are_not_utf8(repo: Repo) -> None:
    (repo.path / os.fsdecode(LATIN1_NAME)).write_bytes(MIXED_LINE)
    added = commit_all(repo, "Add a file")
    (repo.path / os.fsdecode(LATIN1_NAME)).unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "main", None, environ={privacy.DENYLIST_ENV: NON_ASCII_TERMS})
    assert outcome.lines == [
        f"commit {added[:12]}: path *********.txt: a term of the denylist",
        f"commit {added[:12]}: *********.txt: added line: a term of the denylist",
    ]


# The privacy scan follows the commit check's range for every kind of push.
def test_a_later_push_of_a_branch_still_scans_an_earlier_commit(repo: Repo) -> None:
    repo.set_origin_main(repo.root)
    repo.git("checkout", "-q", "-b", "moving/on")
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    earlier = commit_all(repo, "Add a page")
    (repo.path / "docs/a.md").unlink()
    commit_all(repo, "Remove it")
    outcome = privacy.run(repo.path, "moving/on", earlier, environ={privacy.DENYLIST_ENV: "x-y"})
    assert outcome.lines == [
        f"commit {earlier[:12]}: docs/a.md: added line: e-mail address outside the example domains"
    ]


def test_a_push_to_main_after_a_skipped_run_scans_the_skipped_commits(repo: Repo) -> None:
    # The push of "Skipped" ran no CI; the next push's range starts before it.
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    skipped = commit_all(repo, "Skipped [skip ci]")
    (repo.path / "docs/a.md").unlink()
    pushed = commit_all(repo, "The next push")
    environ = {privacy.DENYLIST_ENV: "x-y"}
    outcome = privacy.run(repo.path, "main", repo.root, pushed, environ=environ)
    assert outcome.lines == [
        f"commit {skipped[:12]}: docs/a.md: added line: e-mail address outside the example domains"
    ]


def test_a_github_merge_to_main_scans_the_merged_commits(repo: Repo) -> None:
    repo.git("checkout", "-q", "-b", "moving/on")
    repo.write("docs/a.md", f"write to {REAL_EMAIL}\n")
    added = commit_all(repo, "Add a page")
    (repo.path / "docs/a.md").unlink()
    repo.write("docs/b.md", "clean\n")
    tip = commit_all(repo, "Replace it")
    merge = repo.raw_commit(
        f"author {person(MAINTAINER)}\ncommitter {person(GITHUB)}",
        b"Merge pull request #1 from example-org/moving/on\n",
        parents=[repo.root, tip],
    )
    environ = {privacy.DENYLIST_ENV: "x-y"}
    outcome = privacy.run(repo.path, "main", repo.root, merge, environ=environ)
    assert outcome.lines == [
        f"commit {added[:12]}: docs/a.md: added line: e-mail address outside the example domains"
    ]
