"""A run's nodes on the in-memory lab, for the tests of the fault primitives and probes.

Each node is an instance of the lab's golden image whose inside has the
directories of a command's path, laid out as on the lab's images (``/bin``
and ``/sbin`` are links), and an empty file for every program a test says
is installed. An instance's name is nothing like its alias, so that a test
can look for the name where only the alias may be.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from potka_incus.fake import FakeIncus
from potka_incus.inside import Inside
from potka_primitives.context import Targets

from potka_testing.fake_incus import FINGERPRINT, TARGET, client, lab

PROJECT = "bench"
# Each alias's instance; the names are what no error and no fact may hold.
INSTANCES: Mapping[str, str] = {"web": "t-7f3a91c2-n1", "db": "t-7f3a91c2-n2"}
DIRECTORIES = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/etc", "/srv")
LINKS = {"/sbin": "usr/sbin", "/bin": "usr/bin"}
# Where the lab's images have the programs the tests install.
SBIN = frozenset({"tc", "nginx"})


def run(
    driver: str = "lvm", *, programs: Iterable[str] = (), aliases: Iterable[str] = ("web", "db")
) -> tuple[FakeIncus, Targets]:
    """A lab with one running instance for each alias, and the run's targets."""
    fake = lab(driver)
    incus = client(fake, PROJECT)
    nodes = {alias: INSTANCES[alias] for alias in aliases}
    for name in nodes.values():
        incus.create(name, fingerprint=FINGERPRINT, profiles=(TARGET,))
        incus.start(name)
        within = fake.instance(PROJECT, name).inside
        for directory in DIRECTORIES:
            within.mkdir(directory)
        for link, target in LINKS.items():
            within.link(link, target)
        for program in programs:
            install(within, program)
    return fake, Targets(incus, nodes)


def install(within: Inside, program: str) -> None:
    """Put an empty executable of that name where the lab's images have it."""
    directory = "/usr/sbin" if program in SBIN else "/usr/bin"
    within.write(f"{directory}/{program}", b"", mode=0o755)


def inside(fake: FakeIncus, alias: str) -> Inside:
    """The inside of the node of that alias."""
    return fake.instance(PROJECT, INSTANCES[alias]).inside


def state(fake: FakeIncus, alias: str) -> tuple[dict[str, tuple[object, ...]], int]:
    """What a node holds: each file, directory and link with content, owner and mode; its use."""
    within = inside(fake, alias)
    nodes: dict[str, tuple[object, ...]] = {
        path: (node.kind, node.content, node.uid, node.gid, node.mode)
        for path, node in within.nodes.items()
    }
    return nodes, within.usage
