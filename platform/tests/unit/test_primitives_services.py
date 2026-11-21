"""The faults of the network, of Docker and of the two databases."""

from __future__ import annotations

from typing import Any

import pytest
from potka_incus.fake import FakeIncus
from potka_primitives import library, services, units
from potka_primitives.base import Effects, Requires
from potka_primitives.context import Targets
from potka_primitives.errors import FaultFailed, Refusal, SetupRefused
from potka_primitives.services import (
    DockerKillContainer,
    DockerStopDaemon,
    NetNetem,
    PgBreakHba,
    PgStop,
    RedisStop,
)
from potka_testing.outputs import output
from potka_testing.target_units import SHOW, Units
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

SHOWN = ("tc", "qdisc", "show", "dev", "eth0")
INSPECT = ("docker", "inspect", "--type=container", "--format={{.State.Running}}", "--")
MARK = "zqmarked"
HBA = "/etc/postgresql/16/main/pg_hba.conf"
PG = "postgresql@16-main.service"
RULES = """\
# TYPE  DATABASE  USER  ADDRESS  METHOD
local   all       postgres       peer

host    all       all   127.0.0.1/32  scram-sha-256
hostssl appdb     app   10.0.0.0/8    scram-sha-256  # the application
"""
INCLUDING = RULES + "include_if_exists extra.conf\n"


def lab(*programs: str) -> tuple[FakeIncus, Targets, Units]:
    fake, targets = run(programs=programs or ("tc", "docker", "systemctl"))
    return fake, targets, Units(inside(fake, "db"))


def refused(error: Any, reason: str) -> None:
    assert (str(error), error.alias) == (reason, "db")
    assert error.__context__ is None and error.__cause__ is None
    assert MARK not in f"{error!r} {vars(error)}" and INSTANCES["db"] not in repr(error)


def ran(fake: FakeIncus) -> list[tuple[str, ...]]:
    return [asked.argv for asked in inside(fake, "db").executions]


def test_the_library_has_all_sixteen_faults_and_no_other() -> None:
    assert sorted(library.PRIMITIVES) == [
        "disk.fill",
        "disk.inode_exhaust",
        "docker.kill_container",
        "docker.stop_daemon",
        "file.mutate",
        "file.write",
        "net.netem",
        "nginx.corrupt_conf",
        "nginx.set_upstream",
        "permissions.break",
        "pg.break_hba",
        "pg.fill_disk",
        "pg.stop",
        "redis.stop",
        "systemd.action",
        "systemd.break_unit",
    ]
    assert sorted(library.PROBES) == ["disk_usage_below", "inode_usage_below"]
    assert NetNetem.requires == Requires((services.TC,))
    # The daemon's unit is asked for before the daemon is: systemctl is one of its programs.
    assert DockerKillContainer.requires == Requires((units.SYSTEMCTL, services.DOCKER), "docker.io")
    assert DockerStopDaemon.requires == Requires((units.SYSTEMCTL,), "docker.io")
    assert PgStop.requires == PgBreakHba.requires == Requires((units.SYSTEMCTL,), "postgresql")
    assert RedisStop.requires == Requires((units.SYSTEMCTL,), "redis-server")
    assert (services.TC.package, services.DOCKER.package) == ("iproute2", "docker.io")
    # Every fault that acts on a service says which, and every one of them has a version.
    acting = {name for name, step in library.PRIMITIVES.items() if step.requires.service}
    assert acting == {
        "docker.kill_container",
        "docker.stop_daemon",
        "nginx.corrupt_conf",
        "nginx.set_upstream",
        "pg.break_hba",
        "pg.fill_disk",
        "pg.stop",
        "redis.stop",
    }
    assert {step.version for step in library.PRIMITIVES.values()} == {1}


def netem(**more: Any) -> Any:
    return NetNetem.parameters({"node": "db", **more})


@pytest.mark.parametrize(
    ("given", "argv"),
    [
        ({"delay_ms": 250}, ("delay", "250ms")),
        ({"loss_percent": 30}, ("loss", "30%")),
        ({"delay_ms": 1, "loss_percent": 100}, ("delay", "1ms", "loss", "100%")),
    ],
)
def test_a_node_s_interface_delays_or_drops_what_it_sends(
    given: dict[str, int], argv: tuple[str, ...]
) -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    within.on(list(SHOWN), **vars(output("tc-qdisc-noqueue").ended()))
    added = ("tc", "qdisc", "add", "dev", "eth0", "root", "netem", *argv)
    within.on(list(added), 0)
    primitive, params = NetNetem(), netem(**given)
    held = state(fake, "db")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert ran(fake) == [SHOWN]
    assert primitive.apply(targets, params) == {}
    assert ran(fake) == [SHOWN, added] and state(fake, "db") == held
    # Neither a file nor a unit, and declared all the same.
    assert primitive.side_effects(params) == Effects(other=("the queueing discipline of eth0",))
    assert "eth0" in primitive.describe(params)


def test_an_interface_that_delays_already_and_an_answer_in_another_form_are_refused() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    within.on(list(SHOWN), **vars(output("tc-qdisc-netem").ended()))
    with pytest.raises(SetupRefused) as there:
        NetNetem().ready(targets, netem(delay_ms=5))
    refused(there.value, "already_in_that_state")
    for said in (b"", b"qdisc\n", b"noqueue 0: root\n", b"qdisc fq_codel 0: root", b"\xff\n"):
        within.on(list(SHOWN), 0, said)
        with pytest.raises(SetupRefused) as unread:
            NetNetem().ready(targets, netem(delay_ms=5))
        refused(unread.value, "unexpected_output")
    # Of several lines the root's is the one that says so, wherever it stands; a discipline
    # of that kind below another root is not the fault in place.
    for said in (
        b"qdisc mq 0: root \nqdisc netem 8001: parent :1 limit 1000\n",
        b"qdisc netem 8001: parent :1 limit 1000\nqdisc mq 0: root \n",
        b"qdisc fq_codel 0: root refcnt 2 limit 10240p flows 1024\n",
    ):
        within.on(list(SHOWN), 0, said)
        NetNetem().ready(targets, netem(delay_ms=5))
    within.on(list(SHOWN), 0, b"qdisc mq 0: parent :1 \nqdisc netem 8001: root refcnt 2 limit 9\n")
    with pytest.raises(SetupRefused) as below:
        NetNetem().ready(targets, netem(delay_ms=5))
    refused(below.value, "already_in_that_state")
    # No root, two roots, or a line that is no discipline's: nothing is guessed.
    for said in (
        b"qdisc fq_codel 0: parent :1 limit 10240p\n",
        b"qdisc mq 0: root \nqdisc noqueue 0: root refcnt 2 \n",
        b"qdisc mq 0: root \nsomething else\n",
        b"qdisc mq 0: root \n\n",
        b"qdisc mq 0: rooted \n",
        b"qdisc mq 0: dev eth0 root \n",
    ):
        within.on(list(SHOWN), 0, said)
        with pytest.raises(SetupRefused) as unsure:
            NetNetem().ready(targets, netem(delay_ms=5))
        refused(unsure.value, "unexpected_output")
    within.on(list(SHOWN), 1, b"", b'Cannot find device "eth0"\n')
    with pytest.raises(SetupRefused) as failed:
        NetNetem().ready(targets, netem(delay_ms=5))
    refused(failed.value, "command_failed")
    added = ["tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "5ms"]
    within.on(added, 2, b"", b"Error: Specified qdisc kind is unknown.\n")
    with pytest.raises(FaultFailed) as unmade:
        NetNetem().apply(targets, netem(delay_ms=5))
    refused(unmade.value, "command_failed")
    assert (unmade.value.program, unmade.value.status) == ("tc", 2)


@pytest.mark.parametrize(
    "wrong",
    [
        {},
        {"delay_ms": 0},
        {"delay_ms": 60_001},
        {"delay_ms": "5"},
        {"loss_percent": 0},
        {"loss_percent": 101},
        {"loss_percent": 2.5},
        {"delay_ms": 5, "interface": "lo"},
        {"delay_ms": 5, "jitter_ms": 1},
    ],
)
def test_netem_s_parameters_are_a_delay_a_loss_or_both(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        netem(**wrong)


def container(name: str = MARK) -> Any:
    return DockerKillContainer.parameters({"node": "db", "container": name})


def test_a_running_container_is_killed() -> None:
    fake, targets, table = lab()
    table.add("docker.service")
    within = inside(fake, "db")
    within.on([*INSPECT, MARK], **vars(output("docker-inspect-running").ended()))
    within.on(["docker", "kill", "--", MARK], 0, f"{MARK}\n".encode())
    primitive, params = DockerKillContainer(), container()
    held = state(fake, "db")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert primitive.apply(targets, params) == {}
    assert ran(fake) == [
        (*SHOW, "docker.service"),
        (*INSPECT, MARK),
        ("docker", "kill", "--", MARK),
    ]
    assert state(fake, "db") == held and table.asked == []
    assert primitive.side_effects(params) == Effects(other=(f"the container {MARK}",))


@pytest.mark.parametrize("active", ["inactive", "failed", "deactivating", None])
def test_a_daemon_that_does_not_run_is_not_asked_and_so_not_started(active: str | None) -> None:
    fake, targets, table = lab()
    table.add("docker.socket")
    if active is not None:
        table.add("docker.service", active=active)
    # A docker command would reach the socket, and the socket would start the daemon.
    with pytest.raises(SetupRefused) as down:
        DockerKillContainer().ready(targets, container())
    refused(down.value, "unit_not_active")
    assert ran(fake) == [(*SHOW, "docker.service")] and table.asked == []


def test_a_container_that_does_not_run_or_is_not_there_is_refused() -> None:
    fake, targets, table = lab()
    table.add("docker.service")
    within = inside(fake, "db")
    primitive, params = DockerKillContainer(), container()
    cases = [
        (output("docker-inspect-stopped").ended(), "already_in_that_state"),
        (output("docker-inspect-missing").ended(), "container_not_found"),
    ]
    for ended, reason in cases:
        within.on([*INSPECT, MARK], **vars(ended))
        with pytest.raises(SetupRefused) as stopped:
            primitive.ready(targets, params)
        refused(stopped.value, reason)
    for said in (b"", b"true", b"True\n", b"true\ntrue\n", b"running\n", b"\xff\n"):
        within.on([*INSPECT, MARK], 0, said)
        with pytest.raises(SetupRefused) as unread:
            primitive.ready(targets, params)
        refused(unread.value, "unexpected_output")
    within.on([*INSPECT, MARK], 1, b"", b"Cannot connect to the Docker daemon\n")
    with pytest.raises(SetupRefused) as down:
        primitive.ready(targets, params)
    refused(down.value, "command_failed")
    within.on(["docker", "kill", "--", MARK], 1, b"", b"Error response from daemon\n")
    with pytest.raises(FaultFailed) as unkilled:
        primitive.apply(targets, params)
    refused(unkilled.value, "command_failed")
    for name in ("", "-rm", "a b", "a/b", "a" * 65, "{{.Id}}", "web;x"):
        with pytest.raises(ValidationError):
            container(name)


def test_the_daemon_and_the_socket_that_starts_it_are_stopped_together() -> None:
    fake, targets, table = lab()
    table.add("docker.socket")
    table.add("docker.service")
    primitive = DockerStopDaemon()
    params = DockerStopDaemon.parameters({"node": "db"})
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert table.asked == []
    facts = primitive.apply(targets, params)
    assert facts == {"socket_active_state": "inactive", "service_active_state": "inactive"}
    assert table.asked == [("stop", "docker.socket", "docker.service")]
    assert ran(fake)[2] == ("systemctl", "stop", "--", "docker.socket", "docker.service")
    assert primitive.side_effects(params) == Effects(units=("docker.socket", "docker.service"))
    # With both stopped the fault is in place; with one still running it is not.
    with pytest.raises(SetupRefused) as there:
        primitive.ready(targets, params)
    refused(there.value, "already_in_that_state")
    table.units["docker.socket"][1] = "active"
    primitive.ready(targets, params)
    with pytest.raises(ValidationError):
        DockerStopDaemon.parameters({"node": "db", "unit": "docker.service"})


def test_a_node_without_the_socket_s_unit_is_refused() -> None:
    _, targets, table = lab()
    table.add("docker.service")
    params = DockerStopDaemon.parameters({"node": "db"})
    with pytest.raises(SetupRefused) as unknown:
        DockerStopDaemon().ready(targets, params)
    refused(unknown.value, "unit_not_found")
    assert table.asked == []


@pytest.mark.parametrize(
    ("primitive", "unit"),
    [(PgStop, "postgresql.service"), (PgStop, PG), (RedisStop, "redis-server.service")],
)
def test_a_database_s_unit_is_stopped(primitive: Any, unit: str) -> None:
    fake, targets, table = lab()
    table.add(unit)
    fault, params = primitive(), primitive.parameters({"node": "db", "unit": unit})
    fault.check(targets, params)
    fault.ready(targets, params)
    assert fault.apply(targets, params) == {
        "active_state": "inactive",
        "unit_file_state": "enabled",
    }
    assert table.asked == [("stop", unit)]
    assert ran(fake) == [(*SHOW, unit), ("systemctl", "stop", "--", unit), (*SHOW, unit)]
    assert fault.side_effects(params) == Effects(units=(unit,))
    assert fault.describe(params) == f"Stops {unit} on the node db."
    with pytest.raises(SetupRefused) as there:
        fault.ready(targets, params)
    refused(there.value, "already_in_that_state")
    del table.units[unit]
    with pytest.raises(SetupRefused) as unknown:
        fault.ready(targets, params)
    refused(unknown.value, "unit_not_found")


def test_a_database_s_fault_takes_only_that_database_s_unit() -> None:
    for unit in ("nginx.service", "postgresql.socket", "postgresql@main.service", "pg.service"):
        with pytest.raises(ValidationError):
            PgStop.parameters({"node": "db", "unit": unit})
    for unit in ("redis.service", "postgresql.service", "redis-server.socket"):
        with pytest.raises(ValidationError):
            RedisStop.parameters({"node": "db", "unit": unit})
    assert RedisStop.parameters({"node": "db"}).unit == "redis-server.service"
    with pytest.raises(ValidationError):
        PgStop.parameters({"node": "db"})


def hba(defect: str, path: str = HBA, unit: str = PG) -> Any:
    return PgBreakHba.parameters({"node": "db", "path": path, "unit": unit, "defect": defect})


def test_a_rule_that_rejects_every_connection_is_put_before_the_first_rule() -> None:
    assert services.rejecting(RULES) == RULES.replace(
        "local   all       postgres       peer\n",
        "host all all all reject\nlocal   all       postgres       peer\n",
    )
    assert services.rejecting("# only\r\nhost all all all trust\r\n") == (
        "# only\r\nhost all all all reject\r\nhost all all all trust\r\n"
    )
    for none in ("", "# a comment\n\n", "include extra.conf\n"):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            services.rejecting(none)
    # A line that takes rules in from another file is read before none of its rules either.
    for taken in ("include_dir conf.d", "include extra.conf", "include_if_exists extra.conf"):
        text = f"# first\n{taken}\nhost all all 10.0.0.0/8 trust\n"
        assert services.rejecting(text) == text.replace(
            "# first\n", f"# first\n{services.REJECT}\n"
        )
    assert services.rejecting(INCLUDING) == services.rejecting(RULES) + (
        "include_if_exists extra.conf\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "host all all \\\n    10.0.0.0/8 scram-sha-256\n",
        "local all all peer\n# a comment that goes on \\\nhost all all all trust\n",
        "host all all all trust\nlocal all all peer \\\r\n",
        "host all all all trust \\",
    ],
)
def test_a_file_whose_lines_go_on_in_the_next_is_no_place_for_either_defect(text: str) -> None:
    for change in (services.rejecting, services.without_hosts):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            change(text)
    # A backslash elsewhere in a line is none of this.
    assert services.rejecting("host all a\\b all trust\n").startswith(services.REJECT)
    for first in (
        "host all all all reject\n",
        "  host   all all  all   reject  # x\nlocal all all peer\n",
    ):
        with pytest.raises(Refusal, match="already_in_that_state"):
            services.rejecting(first)


def test_the_rules_for_connections_over_the_network_are_taken_away() -> None:
    assert services.without_hosts(RULES) == (
        "# TYPE  DATABASE  USER  ADDRESS  METHOD\nlocal   all       postgres       peer\n\n"
    )
    for none in ("", "local all all peer\n# host all all all trust\n"):
        with pytest.raises(Refusal, match="already_in_that_state"):
            services.without_hosts(none)
    # What another file's rules are is not seen here: they could be for the network too.
    for taking in (INCLUDING, "include_dir conf.d\nlocal all all peer\n", "include a.conf\n"):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            services.without_hosts(taking)


@pytest.mark.parametrize("defect", ["reject", "no_entry"])
def test_the_file_is_changed_and_postgresql_is_asked_to_read_it_again(defect: str) -> None:
    fake, targets, table = lab()
    within = inside(fake, "db")
    within.mkdir("/etc/postgresql/16/main")
    within.write(HBA, RULES.encode(), uid=110, gid=115, mode=0o640)
    table.add(PG)
    primitive, params = PgBreakHba(), hba(defect)
    held = state(fake, "db")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "db") == held and table.asked == []
    changed = services.rejecting(RULES) if defect == "reject" else services.without_hosts(RULES)
    assert primitive.apply(targets, params) == {"bytes": len(changed)}
    written = within.nodes[HBA]
    assert (written.content, written.uid, written.gid, written.mode) == (
        changed.encode(),
        110,
        115,
        0o640,
    )
    assert table.asked == [("reload", PG)] and table.units[PG][1] == "active"
    assert primitive.side_effects(params) == Effects(files=(HBA,), units=(PG,))
    # No comment and no word of the library's is written.
    assert written.content.count(b"#") == changed.count("#") <= RULES.count("#")
    assert b"potka" not in written.content
    with pytest.raises(SetupRefused) as there:
        primitive.ready(targets, params)
    refused(there.value, "already_in_that_state")


def test_a_cluster_that_does_not_run_or_has_no_such_file_is_refused() -> None:
    fake, targets, table = lab()
    within = inside(fake, "db")
    primitive = PgBreakHba()
    with pytest.raises(SetupRefused) as unknown:
        primitive.ready(targets, hba("reject"))
    refused(unknown.value, "unit_not_found")
    table.add(PG, active="inactive")
    with pytest.raises(SetupRefused) as stopped:
        primitive.ready(targets, hba("reject"))
    refused(stopped.value, "unit_not_active")
    table.units[PG][1] = "active"
    with pytest.raises(SetupRefused) as absent:
        primitive.ready(targets, hba("reject"))
    refused(absent.value, "file_not_found")
    within.mkdir("/etc/postgresql/16/main")
    within.write(HBA, b"# nothing\n")
    with pytest.raises(SetupRefused) as placeless:
        primitive.ready(targets, hba("reject"))
    refused(placeless.value, "no_place_for_defect")
    within.write(HBA, RULES.encode())
    within.on(["systemctl", "reload", "--", PG], 1, b"", b"Job failed.\n")
    with pytest.raises(FaultFailed) as unread:
        primitive.apply(targets, hba("reject"))
    refused(unread.value, "command_failed")


@pytest.mark.parametrize(
    "wrong",
    [
        {"path": "/etc/postgresql/16/main/postgresql.conf"},
        {"path": "/etc/postgresql/pg_hba.conf"},
        {"path": "/etc/passwd"},
        {"path": "/etc/postgresql/16/main/../main/pg_hba.conf"},
        {"path": "/srv/pg_hba.conf"},
        {"unit": "nginx.service"},
        {"unit": "postgresql.socket"},
        # A cluster's file with another cluster's unit: the wrong one would read again.
        {"unit": "postgresql@15-main.service"},
        {"unit": "postgresql@16-other.service"},
        {"unit": "postgresql@16-main2.service"},
        {"defect": "trust"},
        {"defect": "reject", "database": "all"},
    ],
)
def test_the_fault_writes_only_a_cluster_s_own_file(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PgBreakHba.parameters({"node": "db", "path": HBA, "unit": PG, "defect": "reject", **wrong})
    assert hba("reject", "/etc/postgresql/9/a_b/pg_hba.conf", "postgresql.service").defect == (
        "reject"
    )
    assert hba("reject", unit="postgresql.service").unit == "postgresql.service"
    other = hba("no_entry", "/etc/postgresql/9/a_b/pg_hba.conf", "postgresql@9-a_b.service")
    assert other.unit == "postgresql@9-a_b.service"
    assert HBA in PgBreakHba().describe(hba("no_entry"))
