"""The fault primitives' reading commands in a real target, on a lab host.

Not run by the gate or by CI. The file's name keeps it out of every
collected run: pytest reads it only when it is named, as
``make e2e-real E2E=tests/e2e_real/primitives_real.py`` does.

A person runs it, as a user who can read a service's certificate
directory, with the variables of ``incus_real.py``:

* ``POTKA_E2E_INCUS_URL``, ``POTKA_E2E_INCUS_CERTS``,
  ``POTKA_E2E_INCUS_PROJECT``: the API, a service's certificates, the
  project;
* ``POTKA_E2E_INCUS_INSTANCE``: the name of a running instance of that
  project;
* ``POTKA_E2E_IMAGE``, if wanted: what to call the instance's image in what
  is printed (``base/1.0.1``);
* ``POTKA_E2E_UNIT``, if wanted: a unit of the instance whose state is read
  (``systemd-journald.service`` when not given).

The checks here change nothing in the instance: they list directories and
run commands that only read. What they print is for the person who runs
them and for ``tests/fixtures/target-outputs/``: each output is printed in
the form of a file of that directory (``potka_testing.outputs``), with
``source: recorded``, under the name of the file it can take the place of,
which was written from a program's documentation. What is printed can
hold names of the instance's files, units and devices.

Only what a reading command prints can be recorded here: a mount, an inode
count, a unit's state as it is, the three kinds of ``stat``'s answer, an
interface's queueing discipline as it is. What a program prints once a
fault is made (a full disk, a start that fails, a container, a delaying
discipline) stays as it was written, and how the state's use follows an
allocation is the other file's to measure (``incus_real.py``).
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from potka_incus import client as incus
from potka_primitives import context, disk, services, units
from potka_primitives.base import BASE_IMAGE
from potka_primitives.context import Node, Ran, Targets
from potka_primitives.errors import Refusal

VARIABLES = ("URL", "CERTS", "PROJECT", "INSTANCE")
ALIAS = "node"
SPOOL = "/var/tmp"  # noqa: S108 (a directory inside the instance)
# The kept output that a unit's state can take the place of, by what systemd says of it.
SHOWN = {
    ("loaded", "active", "enabled"): "systemctl-show-active",
    ("loaded", "inactive", "disabled"): "systemctl-show-inactive",
    ("loaded", "failed", "enabled"): "systemctl-show-failed",
    ("masked", "inactive", "masked"): "systemctl-show-masked",
    ("loaded", "active", "static"): "systemctl-show-static",
}
# The same for a file system, by the type ``stat`` names at the end of its answer.
COUNTED = {b":ext2/ext3\n": "stat-fs-ext4", b":btrfs\n": "stat-fs-btrfs"}
# The programs of the operating system image's own packages that the library runs:
# no table of what a blueprint installs can say that they are there.
OF_THE_IMAGE = {
    "coreutils": ("chmod", "chown", "mkdir", "stat", "touch"),
    "systemd": ("systemctl",),
    "util-linux": ("fallocate", "findmnt"),
}


def variable(name: str) -> str:
    return os.environ.get(f"POTKA_E2E_INCUS_{name}", "")


@pytest.fixture(scope="module")
def node() -> Iterator[Node]:
    """The instance of the environment as a run's node."""
    missing = [f"POTKA_E2E_INCUS_{name}" for name in VARIABLES if not variable(name)]
    if missing:
        pytest.fail(f"set {', '.join(missing)} (see this file's first lines)")
    certificate = incus.Certificate.of(Path(variable("CERTS")))
    with incus.Incus(variable("URL"), variable("PROJECT"), certificate=certificate) as client:
        yield Targets(client, {ALIAS: variable("INSTANCE")}).node(ALIAS)


def kept(name: str, ran: Ran) -> None:
    """Print a command's end as a file of the kept outputs would hold it."""
    today = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
    image = os.environ.get("POTKA_E2E_IMAGE", "") or "an-image-not-named"
    stream, content = ("stderr", ran.stderr) if ran.exit_code else ("stdout", ran.stdout)
    print(f"\n# {name}.txt")
    print(f"source: recorded\nrecorded: {today} {image}\nstatus: {ran.exit_code}")
    print(f"stream: {stream}\n---\n{content.decode('utf-8', 'replace')}", end="")


def test_the_command_path_s_directories_hold_the_image_s_own_programs(node: Node) -> None:
    for directory in context.PATH:
        print(f"\n{directory}: {node.kind(directory)}")
    assert set(OF_THE_IMAGE) == BASE_IMAGE
    absent = [
        f"{program} ({package})"
        for package, programs in sorted(OF_THE_IMAGE.items())
        for program in programs
        if not node.has(program)
    ]
    assert not absent, f"not in the command path's directories: {', '.join(absent)}"


def test_a_command_gets_the_library_s_environment(node: Node) -> None:
    ran = node.run(["env"]).expect(0)
    given = dict(line.partition("=")[::2] for line in ran.text().splitlines())
    print(f"\nPATH={given.get('PATH')}\nLC_ALL={given.get('LC_ALL')}")
    assert given.get("PATH") == context.ENVIRONMENT["PATH"]
    assert given.get("LC_ALL") == "C"


def test_the_disk_programs_print_what_the_library_reads(node: Node) -> None:
    mount = node.run(["findmnt", "--noheadings", "--output", "TARGET", f"--target={SPOOL}"])
    kept("findmnt-root", mount)
    counted = node.run(["stat", "--file-system", "--format=%c:%d:%T", "--", "/"])
    of_type = [name for end, name in COUNTED.items() if counted.stdout.endswith(end)]
    kept(of_type[0] if of_type else "stat-fs-of-another-type", counted)
    print(f"\nroot disk: configured size {node.root_size()}, use {node.root_usage()}")
    # The library's own readers take these outputs, or say why not.
    disk.on_root_disk(node, SPOOL)
    size, used = disk.measured(node)
    assert 0 < used <= size
    try:
        total, in_use = disk.inodes(node, "/")
    except Refusal as refusal:
        print(f"inodes: {refusal.reason}")
        assert refusal.reason == "inode_count_not_fixed"
    else:
        print(f"inodes: {in_use} of {total} in use")
        assert 0 < in_use <= total


def test_systemd_and_stat_print_what_the_library_reads(node: Node) -> None:
    unit = os.environ.get("POTKA_E2E_UNIT", "") or "systemd-journald.service"
    state = units.unit_state(node, unit)
    assert state.load != "not-found", "the instance has no unit of that name"
    of_state = SHOWN.get((state.load, state.active, state.file), "systemctl-show-of-another-state")
    for name, asked in ((of_state, unit), ("systemctl-show-not-found", "no-such.service")):
        shown = node.run(
            ["systemctl", "show", f"--property={','.join(units.PROPERTIES)}", "--", asked]
        )
        kept(name, shown)
    assert units.unit_state(node, "no-such.service").load == "not-found"
    # A file, a directory and a link that the operating system image has: /bin is a link.
    for name, path in (
        ("stat-file-regular", "/etc/hostname"),
        ("stat-file-directory", "/etc"),
        ("stat-file-link", "/bin"),
    ):
        kept(name, node.run(["stat", "--format=%a:%u:%g:%F", "--", path]))


def test_tc_prints_what_the_library_reads(node: Node) -> None:
    shown = node.run(["tc", "qdisc", "show", "dev", services.INTERFACE])
    plain = shown.stdout.startswith(b"qdisc noqueue ")
    kept("tc-qdisc-noqueue" if plain else "tc-qdisc-of-another-kind", shown)
    assert shown.exit_code == 0, "the instance has no interface of that name"
    # Whether a delaying discipline can be added is the host's: its kernel must have it,
    # and an instance cannot load it. Nothing is added here, so this does not say. An
    # entry of the module shows that it is loaded; none shows nothing, for the kernel
    # may have the discipline built in, or load it when it is first asked for.
    entry = node.kind("/sys/module/sch_netem")
    print(f"\n/sys/module/sch_netem in the instance: {entry} (none decides nothing)")
