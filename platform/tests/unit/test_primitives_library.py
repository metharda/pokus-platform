"""Every fault and probe of the library, once each: what holds of all of them alike."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from potka_core.catalog import lint
from potka_core.images import load_table
from potka_incus import client as incus
from potka_incus.fake import FakeIncus
from potka_primitives import context, library
from potka_primitives.base import BASE_IMAGE, Primitive
from potka_primitives.context import Targets
from potka_primitives.errors import TargetFailure
from potka_testing import outputs
from potka_testing.target_disk import Disk
from potka_testing.target_units import Files, Units
from potka_testing.targets import INSTANCES, inside, run, state
from potka_testing.tool_views import LIBRARY

MARK = "zqmarked"
SITE = "server {\n    listen 80;\n    location / { proxy_pass http://127.0.0.1:8000; }\n}\n"
HBA = "/etc/postgresql/16/main/pg_hba.conf"
PG = "postgresql@16-main.service"
# The names the lint knows and the library has not: each needs what no image has yet.
NOT_BUILT = {
    "app.bad_config",
    "dns.break_resolver",
    "dns.wrong_record",
    "docker.crashloop",
    "net.block_port",
    "pg.exhaust_connections",
    "proc.cpu_hog",
    "proc.fd_exhaust",
    "proc.mem_hog",
    "redis.maxmemory_oom",
}
# For every fault: its parameters without the node, on a node prepared by `prepared`.
FAULTS: dict[str, dict[str, Any]] = {
    "disk.fill": {"path": f"/srv/{MARK}.part", "percent": 60},
    "disk.inode_exhaust": {"path": f"/srv/{MARK}", "prefix": "spool", "percent": 1},
    "pg.fill_disk": {"path": f"/var/lib/postgresql/{MARK}.part", "percent": 60},
    "file.write": {"path": f"/etc/{MARK}.conf", "content": "a = 1\n", "mode": "640", "uid": 33},
    "file.mutate": {
        "path": "/etc/app.conf",
        "edits": [{"edit": "set_key", "key": "workers", "value": "64"}],
    },
    "permissions.break": {"path": "/etc/app.conf", "mode": "600", "uid": 33},
    "systemd.action": {"unit": "nginx.service", "action": "stop"},
    "systemd.break_unit": {"unit": "nginx.service", "exec_start": f"/opt/{MARK}/served --x"},
    "nginx.corrupt_conf": {"path": "/etc/nginx/site.conf", "defect": "unclosed_block"},
    "nginx.set_upstream": {"path": "/etc/nginx/site.conf", "upstream": "127.0.0.1:8099"},
    "net.netem": {"delay_ms": 200, "loss_percent": 5},
    "docker.kill_container": {"container": "web"},
    "docker.stop_daemon": {},
    "pg.stop": {"unit": PG},
    "pg.break_hba": {"path": HBA, "unit": PG, "defect": "reject"},
    "redis.stop": {"unit": "redis-server.service"},
}
PROGRAMS = (
    "chmod",
    "chown",
    "docker",
    "fallocate",
    "findmnt",
    "mkdir",
    "stat",
    "systemctl",
    "tc",
    "touch",
)


def prepared() -> tuple[FakeIncus, Targets, Units]:
    """A node on which every fault of the library can be made once, and its units."""
    fake, targets = run(programs=PROGRAMS)
    within = inside(fake, "web")
    Files(within)
    Disk(within, inodes=1_000_000, taken=1000)
    table = Units(within)
    for unit in ("nginx.service", PG, "redis-server.service", "docker.socket", "docker.service"):
        table.add(unit)
    for directory in ("/etc/nginx", "/etc/systemd/system", "/etc/postgresql/16/main"):
        within.mkdir(directory)
    within.mkdir("/var/lib/postgresql")
    within.write("/etc/app.conf", b"workers = 2\n", mode=0o640)
    within.write("/etc/nginx/site.conf", SITE.encode())
    within.write(HBA, b"local all all peer\nhost all all 127.0.0.1/32 trust\n")
    within.on(
        ["tc", "qdisc", "show", "dev", "eth0"], **vars(outputs.output("tc-qdisc-noqueue").ended())
    )
    added = ["tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "200ms", "loss", "5%"]
    within.on(added, 0)
    inspect = ["docker", "inspect", "--type=container", "--format={{.State.Running}}", "--", "web"]
    within.on(inspect, **vars(outputs.output("docker-inspect-running").ended()))
    within.on(["docker", "kill", "--", "web"], 0, b"web\n")
    return fake, targets, table


def made(name: str) -> tuple[FakeIncus, Primitive[Any], Any, Callable[[], Any]]:
    fake, targets, _ = prepared()
    primitive = library.primitive(name)
    params = primitive.parameters({"node": "web", **FAULTS[name]})

    def applied() -> Any:
        primitive.check(targets, params)
        primitive.ready(targets, params)
        return primitive.apply(targets, params)

    return fake, primitive, params, applied


def test_the_library_and_the_lint_s_lists_agree() -> None:
    assert set(FAULTS) == set(library.PRIMITIVES) and len(FAULTS) == 16
    assert set(lint.PRIMITIVES) - set(library.PRIMITIVES) == NOT_BUILT
    assert set(library.PRIMITIVES) <= lint.PRIMITIVES
    assert NOT_BUILT == lint.LOAD_PRIMITIVES | set(lint.TOOLS) | lint.UNSUPPORTED
    assert set(library.PROBES) <= lint.PROBES


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_check_and_ready_write_nothing_and_apply_stays_within_what_it_declares(name: str) -> None:
    fake, targets, table = prepared()
    within = inside(fake, "web")
    primitive = library.primitive(name)
    params = primitive.parameters({"node": "web", **FAULTS[name]})
    held, units = state(fake, "web"), copy.deepcopy(table.units)
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held and (table.units, table.asked) == (units, [])
    facts = primitive.apply(targets, params)
    after, _ = state(fake, "web")
    declared = primitive.side_effects(params)
    # Every unit whose state it changed, and every unit it asked systemd about, is declared.
    acted = {unit for unit in units if table.units[unit] != units[unit]}
    acted |= {unit for asked in table.asked for unit in asked[1:]}
    assert acted <= set(declared.units), sorted(acted)
    assert set(declared.units) <= set(units)
    changed = {path for path in set(held[0]) | set(after) if held[0].get(path) != after.get(path)}
    assert all(
        any(path == file or path.startswith(file + "/") for file in declared.files)
        for path in changed
    ), sorted(changed)
    # Every program it ran is one it names, and each fact is a number or a word of its own.
    ran = {asked.argv[0] for asked in within.executions}
    assert ran <= {program.name for program in primitive.requires.programs}, sorted(ran)
    assert all(type(value) is int or value in primitive.words for value in facts.values())
    assert set(facts) <= primitive.reports


# What the client can raise, of each kind but the one that answers what a path is.
CLIENT_ERRORS: tuple[Exception, ...] = (
    incus.NotFound(f"{INSTANCES['web']}: {MARK}", 404),
    incus.Forbidden(f"{INSTANCES['web']}: {MARK}", 403),
    incus.Unreachable(f"{INSTANCES['web']}: {MARK}"),
    incus.OperationTimeout(f"{INSTANCES['web']}: {MARK}", {}),
    incus.OperationFailed(f"{INSTANCES['web']}: {MARK}", {}),
    incus.OutputLost(f"{INSTANCES['web']}: {MARK}", 500, 0),
    incus.IncusError(f"{INSTANCES['web']}: {MARK}", 500),
    ValueError(f"{INSTANCES['web']}: {MARK}"),
)
CLIENT_CALLS = (
    "instance",
    "state",
    "exec",
    "read_file",
    "write_file",
    "delete_file",
    "list_directory",
)


def refusing(error: Exception, asked: list[str]) -> Callable[..., Any]:
    """A call of the client that notes that it was made and ends in ERROR."""

    def refuse(*_: Any, **__: Any) -> Any:
        asked.append("asked")
        raise error

    return refuse


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_every_error_of_the_client_under_every_call_of_a_fault_is_the_library_s(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    primitive = library.primitive(name)
    params = primitive.parameters({"node": "web", **FAULTS[name]})
    failed = 0
    for error in CLIENT_ERRORS:
        for call in ("check", "ready", "apply"):
            fake, targets, table = prepared()
            asked: list[str] = []
            with monkeypatch.context() as patched:
                for method in CLIENT_CALLS:
                    patched.setattr(incus.Incus, method, refusing(error, asked))
                try:
                    getattr(primitive, call)(targets, params)
                except TargetFailure as failure:
                    failed += 1
                    assert asked and str(failure) == context._failure(error), (call, error)
                    assert failure.alias == "web"
                    assert failure.__cause__ is None and failure.__context__ is None
                    text = f"{failure!r} {vars(failure)}"
                    assert MARK not in text and INSTANCES["web"] not in text
                else:
                    # Nothing was asked of the client, so nothing of it could be lost.
                    assert not asked, (call, error)
            assert (table.asked, inside(fake, "web").executions) == ([], [])
    # Every fault asks the client for something when it is made ready and when it is applied.
    assert failed >= 2 * len(CLIENT_ERRORS)
    assert set(CLIENT_CALLS) == {
        name
        for name in vars(incus.Incus)
        if f"_client.{name}(" in Path(context.__file__).read_text(encoding="utf-8")
    }


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_the_same_parameters_give_the_same_commands_and_files(name: str) -> None:
    def once() -> tuple[list[Any], Any, Any]:
        fake, _, _, applied = made(name)
        facts = applied()
        return inside(fake, "web").executions, state(fake, "web"), facts

    assert once() == once()


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_nothing_a_fault_leaves_in_a_target_names_the_library_or_the_lab(name: str) -> None:
    fake, _, _, applied = made(name)
    before, _ = state(fake, "web")
    applied()
    after, _ = state(fake, "web")
    words = [b"pokus", b"pinka", b"potka", *(one.encode() for one in library.PRIMITIVES)]
    for path, node in after.items():
        if before.get(path) == node:
            continue
        content = node[1]
        assert isinstance(content, bytes)
        text = path.encode() + b"\n" + content
        assert not [word for word in words if word in text.lower()], path
    # The commands it ran name them no more than what it wrote.
    for asked in inside(fake, "web").executions:
        line = " ".join(asked.argv).encode().lower()
        assert not [word for word in words if word in line], asked.argv


def test_every_program_of_the_library_is_the_service_s_own_or_in_every_golden_image() -> None:
    table = load_table()
    packages: dict[str, str] = {}
    for step in (*library.PRIMITIVES.values(), *library.PROBES.values()):
        for program in step.requires.programs:
            assert packages.setdefault(program.name, program.package) == program.package
            assert (
                program.package == step.requires.service
                or program.package in BASE_IMAGE
                or table.everywhere(program.package)
            ), (step.name, program.name)
    assert packages == {
        "chmod": "coreutils",
        "chown": "coreutils",
        "docker": "docker.io",
        "fallocate": "util-linux",
        "findmnt": "util-linux",
        "mkdir": "coreutils",
        "stat": "coreutils",
        "systemctl": "systemd",
        "tc": "iproute2",
        "touch": "coreutils",
    }
    assert sorted(packages) == sorted(PROGRAMS)
    # A service's package is one that some golden image carries.
    services = {step.requires.service for step in library.PRIMITIVES.values()} - {None}
    assert services == {"docker.io", "nginx", "postgresql", "redis-server"}
    for package in services:
        assert any(blueprint.carries(str(package)) for blueprint in table.blueprints.values())


def test_every_kept_output_is_named_where_tests_read_it_and_all_are_still_written_by_hand() -> None:
    tests = Path(__file__).resolve().parents[1]
    readers = [
        path
        for directory in ("unit", "potka_testing")
        for path in sorted((tests / directory).glob("*.py"))
        if path.name not in (Path(__file__).name, "test_target_outputs.py")
    ]
    read = [path.read_text(encoding="utf-8") for path in readers]
    assert len(readers) > 20
    # A file that no test or stand-in names is read by none.
    assert [name for name in outputs.names() if not any(f'"{name}"' in text for text in read)] == []
    assert outputs.manual() == outputs.names()
    assert len(outputs.names()) == 23


WRONG: tuple[Any, ...] = (MARK, [MARK], {"key": MARK}, 918273645, f"/{MARK}/../x", None, True)


def test_the_views_of_the_release_s_steps_say_what_is_wrong_and_show_no_value() -> None:
    probes: dict[str, dict[str, Any]] = {
        "disk_usage_below": {"percent": 90},
        "inode_usage_below": {"path": "/srv", "percent": 90},
    }
    assert set(probes) == set(library.PROBES)
    views = {**LIBRARY.primitives, **LIBRARY.probes}
    for name, fitting in {**FAULTS, **probes}.items():
        given = {"node": "web", **fitting}
        assert views[name].problem(given) == (), name
        said: list[str] = []
        for key in given:
            for wrong in WRONG:
                said += views[name].problem({**given, key: wrong})
        said += views[name].problem({**given, "more": MARK})
        said += views[name].problem({})
        text = " ".join(said)
        assert said and MARK not in text and "918273645" not in text, name
        assert "more: is no parameter of it" in said and "node: is missing" in said, name
    # What a step acts on reaches its view as the step has it.
    for name, step in {**library.PRIMITIVES, **library.PROBES}.items():
        assert views[name].service == step.requires.service, name
    assert LIBRARY.primitives["nginx.corrupt_conf"].service == "nginx"
    assert LIBRARY.primitives["disk.fill"].service is None
