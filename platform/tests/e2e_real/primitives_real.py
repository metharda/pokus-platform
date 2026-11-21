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
  project.

The checks here change nothing in the instance: they list directories and
run commands that only read. What they print is for the person who runs
them and for ``tests/fixtures/target-outputs/``: each output is printed in
the form of a file of that directory (``potka_testing.outputs``), with
``source: recorded``, so that it can take the place of the one that was
written from a program's documentation. What is printed can hold names of
the instance's files, units and devices.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from potka_incus import client as incus
from potka_primitives import context
from potka_primitives.base import BASE_IMAGE
from potka_primitives.context import Node, Ran, Targets

VARIABLES = ("URL", "CERTS", "PROJECT", "INSTANCE")
ALIAS = "node"
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


def kept(name: str, ran: Ran, image: str) -> None:
    """Print a command's end as a file of the kept outputs would hold it."""
    today = datetime.datetime.now(tz=datetime.UTC).date().isoformat()
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
