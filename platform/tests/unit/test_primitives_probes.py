"""The probes that ask inside a node: a unit, a container, PostgreSQL, Redis."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from potka_incus.fake import FakeIncus
from potka_primitives import library, services, units
from potka_primitives.base import Requires, Verdict
from potka_primitives.context import Targets
from potka_primitives.errors import TargetFailure
from potka_primitives.services import ContainerState, PgQuery, PgReady, RedisPing
from potka_primitives.units import UnitInState
from potka_testing.outputs import output
from potka_testing.target_units import SHOW, Units
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

MARK = "zqmarked"
UNIT = f"{MARK}.service"
STATUS = ("docker", "inspect", "--type=container", "--format={{.State.Status}}", "--")
READY = ("pg_isready",)
PING = ("redis-cli", "ping")
ACCOUNT = (("id", "--user", "--", "postgres"), ("id", "--group", "--", "postgres"))
QUERY = "SELECT count(*) FROM zqmarked"
PSQL = (
    "psql",
    "--no-psqlrc",
    "--quiet",
    "--set=VERBOSITY=sqlstate",
    "--output=/dev/null",
    # The session: it reads only, the server ends a statement after five seconds, and
    # neither the client nor the server's log is told more than an error's code.
    "--dbname=dbname=appdb connect_timeout=5 options='-c default_transaction_read_only=on"
    " -c statement_timeout=5000 -c client_min_messages=error -c log_min_messages=panic'",
    f"--command={QUERY}",
)


def lab() -> tuple[FakeIncus, Targets, Units]:
    fake, targets = run(programs=("systemctl", "docker", "pg_isready", "psql", "id", "redis-cli"))
    return fake, targets, Units(inside(fake, "db"))


def ran(fake: FakeIncus) -> list[tuple[str, ...]]:
    return [asked.argv for asked in inside(fake, "db").executions]


def kept(fake: FakeIncus, argv: tuple[str, ...], name: str) -> None:
    """Let the command ARGV end as the kept output NAME has it."""
    inside(fake, "db").on(list(argv), **vars(output(name).ended()))


def clean(verdict: Verdict) -> Verdict:
    """VERDICT, which holds nothing of a parameter and nothing of the instance."""
    assert MARK in QUERY and MARK in UNIT
    assert MARK not in repr(verdict) and INSTANCES["db"] not in repr(verdict)
    return verdict


def test_the_library_has_the_five_probes_of_a_node_s_services() -> None:
    assert sorted(library.PROBES) == [
        "container_state",
        "disk_usage_below",
        "inode_usage_below",
        "pg_query",
        "pg_ready",
        "redis_ping",
        "unit_state",
    ]
    assert UnitInState.requires == Requires((units.SYSTEMCTL,))
    assert ContainerState.requires == Requires((units.SYSTEMCTL, services.DOCKER), "docker.io")
    assert PgReady.requires == Requires((services.ID, services.PG_ISREADY), "postgresql")
    assert PgQuery.requires == Requires((services.ID, services.PSQL), "postgresql")
    assert RedisPing.requires == Requires((services.REDIS_CLI,), "redis-server")
    assert {step.version for step in library.PROBES.values()} == {1}
    # A service's client programs are its own package's; id is the operating system image's.
    packages = {one.name: one.package for one in (services.PG_ISREADY, services.PSQL)}
    assert packages == {"pg_isready": "postgresql", "psql": "postgresql"}
    assert (services.REDIS_CLI.package, services.ID.package) == ("redis-server", "coreutils")


def unit(state: str, name: str = UNIT) -> Any:
    return UnitInState.parameters({"node": "db", "unit": name, "state": state})


@pytest.mark.parametrize(
    ("said", "asked", "outcome"),
    [
        (["loaded", "active", "enabled"], "active", "passed"),
        (["loaded", "active", "enabled"], "inactive", "failed"),
        (["loaded", "inactive", "disabled"], "inactive", "passed"),
        (["loaded", "failed", "enabled"], "failed", "passed"),
        (["loaded", "failed", "enabled"], "inactive", "failed"),
        (["loaded", "activating", "enabled"], "active", "failed"),
        # A unit systemd has not loaded is in no state that can be asked for.
        (["masked", "inactive", "masked"], "inactive", "failed"),
        (["masked", "active", "masked"], "active", "failed"),
        (["not-found", "inactive", ""], "inactive", "failed"),
    ],
)
def test_a_unit_is_in_a_state_when_systemd_has_loaded_it_and_says_so(
    said: list[str], asked: str, outcome: str
) -> None:
    fake, targets, table = lab()
    table.units[UNIT] = list(said)
    held = state(fake, "db")
    verdict = clean(UnitInState().run(targets, unit(asked)))
    assert verdict == Verdict(outcome, None, {"load_state": said[0], "active_state": said[1]})  # type: ignore[arg-type]
    assert ran(fake) == [(*SHOW, UNIT)] and table.asked == [] and state(fake, "db") == held


def test_a_unit_s_probe_reads_systemd_s_own_units_too_and_guesses_nothing() -> None:
    fake, targets, table = lab()
    table.add("systemd-journald.service", file="static")
    assert (
        UnitInState().run(targets, unit("active", "systemd-journald.service")).outcome == "passed"
    )
    # The seven states are systemd's own words for ActiveState, and no other is asked for.
    assert set(get_args(units.StateParams.model_fields["state"].annotation)) == units.ACTIVE_STATES
    for wrong in (
        {"state": "running"},
        {"state": "enabled"},
        {"unit": "nginx"},
        {"unit": "a@.service"},
    ):
        with pytest.raises(ValidationError):
            UnitInState.parameters({"node": "db", "unit": UNIT, "state": "active", **wrong})
    inside(fake, "db").on([*SHOW, UNIT], 0, b"LoadState=loaded\nActiveState=up\nUnitFileState=\n")
    assert clean(UnitInState().run(targets, unit("active"))) == Verdict(
        "error", "unexpected_output"
    )
    inside(fake, "db").on([*SHOW, UNIT], 1, b"", b"Failed to connect to bus\n")
    assert clean(UnitInState().run(targets, unit("active"))) == Verdict("error", "command_failed")


def container(state_asked: str, name: str = MARK) -> Any:
    return ContainerState.parameters({"node": "db", "container": name, "state": state_asked})


def test_a_container_s_state_is_docker_s_word_or_absent() -> None:
    fake, targets, table = lab()
    table.add("docker.service")
    probe = ContainerState()
    held = state(fake, "db")
    kept(fake, (*STATUS, MARK), "docker-inspect-status-running")
    facts = {"daemon_active_state": "active", "state": "running"}
    assert clean(probe.run(targets, container("running"))) == Verdict("passed", None, facts)
    assert clean(probe.run(targets, container("exited"))) == Verdict("failed", None, facts)
    assert clean(probe.run(targets, container("absent"))).outcome == "failed"
    kept(fake, (*STATUS, MARK), "docker-inspect-status-exited")
    assert probe.run(targets, container("exited")).outcome == "passed"
    assert probe.run(targets, container("running")).outcome == "failed"
    kept(fake, (*STATUS, MARK), "docker-inspect-missing")
    gone = {"daemon_active_state": "active", "state": "absent"}
    assert clean(probe.run(targets, container("absent"))) == Verdict("passed", None, gone)
    assert probe.run(targets, container("running")) == Verdict("failed", None, gone)
    # The daemon's unit is asked first, then Docker; nothing changes, systemd is told nothing.
    assert ran(fake)[:2] == [(*SHOW, "docker.service"), (*STATUS, MARK)]
    assert state(fake, "db") == held and table.asked == []
    assert set(get_args(services.ContainerStateParams.model_fields["state"].annotation)) == {
        *services.CONTAINER_STATES,
        services.ABSENT,
    }


def test_a_container_s_state_in_another_form_is_an_error() -> None:
    fake, targets, table = lab()
    table.add("docker.service")
    probe = ContainerState()
    for said in (b"", b"running", b"Running\n", b"running\nexited\n", b"up\n", b"\xff\n"):
        inside(fake, "db").on([*STATUS, MARK], 0, said)
        assert clean(probe.run(targets, container("running"))) == Verdict(
            "error", "unexpected_output"
        )
    inside(fake, "db").on([*STATUS, MARK], 1, b"", b"Cannot connect to the Docker daemon\n")
    assert probe.run(targets, container("running")) == Verdict("error", "command_failed")
    inside(fake, "db").on([*STATUS, MARK], 127, b"", b"not found\n")
    assert probe.run(targets, container("running")) == Verdict("error", "tool_missing")
    for name in ("", "-rm", "a b", "{{.Id}}"):
        with pytest.raises(ValidationError):
            container("running", name)
    with pytest.raises(ValidationError):
        container("stopped")


@pytest.mark.parametrize("daemon", ["inactive", "failed", "deactivating", None])
def test_a_stopped_daemon_is_not_asked_and_so_not_started(daemon: str | None) -> None:
    fake, targets, table = lab()
    table.add("docker.socket")
    if daemon is not None:
        table.add("docker.service", active=daemon)
    probe = ContainerState()
    # No container runs then; of any other state nothing can be said without asking.
    stopped = {"daemon_active_state": daemon or "inactive"}
    assert clean(probe.run(targets, container("running"))) == Verdict("failed", None, stopped)
    for other in ("exited", "absent", "dead"):
        assert probe.run(targets, container(other)) == Verdict("error", "unit_not_active")
    assert set(ran(fake)) == {(*SHOW, "docker.service")} and table.asked == []


def test_postgresql_is_ready_when_it_accepts_connections() -> None:
    fake, targets, _ = lab()
    accounted(fake)
    probe = PgReady()
    ready = PgReady.parameters({"node": "db"})
    not_ready = PgReady.parameters({"node": "db", "ready": False})
    held = state(fake, "db")
    assert ready.ready is True
    for name, status, accepts in (
        ("pg-isready-accepting", 0, True),
        ("pg-isready-rejecting", 1, False),
        ("pg-isready-no-response", 2, False),
    ):
        kept(fake, READY, name)
        said = {"status": status}
        assert probe.run(targets, ready) == Verdict("passed" if accepts else "failed", None, said)
        assert probe.run(targets, not_ready) == Verdict(
            "failed" if accepts else "passed", None, said
        )
    assert set(ran(fake)) == {*ACCOUNT, READY} and state(fake, "db") == held
    # No attempt was made: nothing is known of the server.
    inside(fake, "db").on(list(READY), 3, b"/var/run/postgresql:5432 - no attempt\n")
    for asked in (ready, not_ready):
        assert probe.run(targets, asked) == Verdict("error", "command_failed")
    inside(fake, "db").on(list(READY), 127)
    assert probe.run(targets, ready) == Verdict("error", "tool_missing")
    # The server is asked as its own user, never as root, whatever it answers: a role it
    # does not know would have it write the attempt in its log at every poll.
    calls = [one for one in inside(fake, "db").executions if one.argv == READY]
    assert len(calls) == 9
    assert {(one.user, one.group, one.cwd) for one in calls} == {(110, 115, "/")}
    for wrong in ({"ready": "yes"}, {"ready": 1}, {"port": 5433}):
        with pytest.raises(ValidationError):
            PgReady.parameters({"node": "db", **wrong})


def test_without_postgresql_s_user_the_server_is_not_asked_whether_it_is_ready() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    kept(fake, READY, "pg-isready-accepting")
    ready = PgReady.parameters({"node": "db"})
    within.on(list(ACCOUNT[0]), 1, b"", b"id: 'postgres': no such user\n")
    assert PgReady().run(targets, ready) == Verdict("error", "command_failed")
    kept(fake, ACCOUNT[0], "id-number")
    within.on(list(ACCOUNT[1]), 0, b"postgres\n")
    assert PgReady().run(targets, ready) == Verdict("error", "unexpected_output")
    # The user's number alone is not enough: the group's is asked for and must be given.
    within.on(list(ACCOUNT[1]), 1, b"", b"id: 'postgres': no such user\n")
    assert PgReady().run(targets, ready) == Verdict("error", "command_failed")
    assert READY not in ran(fake)


def query(text: str = QUERY, database: str = "appdb") -> Any:
    return PgQuery.parameters({"node": "db", "database": database, "query": text})


def accounted(fake: FakeIncus) -> None:
    """Let the node have PostgreSQL's user, whose number is not its group's."""
    kept(fake, ACCOUNT[0], "id-number")
    kept(fake, ACCOUNT[1], "id-number-group")


def test_a_reading_statement_runs_as_postgresql_s_own_user_in_a_read_only_session() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    accounted(fake)
    within.on(list(PSQL), 0)
    held = state(fake, "db")
    assert clean(PgQuery().run(targets, query())) == Verdict("passed", None, {"status": 0})
    assert ran(fake) == [*ACCOUNT, PSQL] and state(fake, "db") == held
    asked = within.executions[-1]
    # The numbers id gave, each in its place, and a directory that user may stand in.
    assert (asked.user, asked.group, asked.cwd) == (110, 115, "/")
    assert [(one.user, one.group) for one in within.executions[:2]] == [(0, 0), (0, 0)]


def test_a_statement_that_fails_or_finds_no_server_is_a_failed_probe() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    accounted(fake)
    for name, status in (("psql-no-connection", 2), ("psql-statement-failed", 1)):
        kept(fake, PSQL, name)
        assert clean(PgQuery().run(targets, query())) == Verdict("failed", None, {"status": status})
    # A statement the server ended for its time is refused like any other: not so now.
    refused = output("psql-statement-failed").content
    assert refused == b"ERROR:  42P01\n"
    within.on(list(PSQL), 1, b"", refused.replace(b"42P01", b"57014"))
    assert PgQuery().run(targets, query()) == Verdict("failed", None, {"status": 1})


def test_a_statement_that_is_wrong_in_every_target_is_no_answer() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    accounted(fake)
    wrong = Verdict("error", "command_failed")
    # Its syntax, and a write in a session that reads only: the statement's own defects.
    kept(fake, PSQL, "psql-statement-syntax")
    assert clean(PgQuery().run(targets, query())) == wrong
    syntax = output("psql-statement-syntax").content
    assert syntax == b"ERROR:  42601\n"
    within.on(list(PSQL), 1, b"", syntax.replace(b"42601", b"25006"))
    assert PgQuery().run(targets, query()) == wrong
    # psql's own failure is none of the server's, and only an error's code alone is read:
    # a message, a second line or another severity is no answer of the server's.
    for said in (
        b"psql: error: out of memory\n",
        b"",
        b'ERROR:  relation "zqmarked" does not exist\n',
        b"ERROR:  42P01",
        b"ERROR: 42P01\n",
        b"NOTICE:  00000\nERROR:  42P01\n",
        b"FATAL:  57P01\n",
    ):
        within.on(list(PSQL), 1, b"", said)
        assert clean(PgQuery().run(targets, query())) == wrong
    # With one command psql has no status of a script's failed statement.
    within.on(list(PSQL), 3, b"", refused := b"ERROR:  42P01\n")
    assert PgQuery().run(targets, query()) == Verdict("error", "command_failed")
    assert refused == output("psql-statement-failed").content
    within.on(list(PSQL), 126)
    assert PgQuery().run(targets, query()) == Verdict("error", "tool_missing")


def test_without_postgresql_s_user_no_statement_is_sent() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    within.on(list(ACCOUNT[0]), 1, b"", b"id: 'postgres': no such user\n")
    assert PgQuery().run(targets, query()) == Verdict("error", "command_failed")
    for said in (b"", b"110", b"postgres\n", b"110\n33\n", b"-1\n", b"\xff\n"):
        within.on(list(ACCOUNT[0]), 0, said)
        assert PgQuery().run(targets, query()) == Verdict("error", "unexpected_output")
    kept(fake, ACCOUNT[0], "id-number")
    within.on(list(ACCOUNT[1]), 0, b"none\n")
    assert PgQuery().run(targets, query()) == Verdict("error", "unexpected_output")
    assert PSQL not in ran(fake)


@pytest.mark.parametrize(
    "wrong",
    [
        {"query": "SELECT 1; DROP TABLE ledger"},
        {"query": "SELECT 1;"},
        {"query": "DELETE FROM ledger"},
        {"query": "WITH x AS (DELETE FROM ledger RETURNING 1) SELECT 1"},
        {"query": "\\! id"},
        {"query": "SELECT\n1"},
        {"query": "SELECT"},
        {"query": "SELECTED 1"},
        {"query": " SELECT 1"},
        {"query": "SELECT 'é'"},
        {"query": "SELECT " + "1" * 1000},
        {"query": 1},
        {"database": "appdb options=x"},
        {"database": "AppDb"},
        {"database": ""},
        {"database": "a" * 64},
        {"expect": "1"},
    ],
)
def test_a_query_is_one_reading_statement_on_a_named_database(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refused:
        PgQuery.parameters({"node": "db", "database": "appdb", "query": "SELECT 1", **wrong})
    assert "ledger" not in str(refused.value)
    for fits in ("SELECT 1", "select count(*) from ledger where id > 0", "SELECT\tnow()"):
        assert query(fits).query == fits
    assert query(database="_a1").database == "_a1"


def test_redis_answers_a_ping_or_does_not() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    probe, params = RedisPing(), RedisPing.parameters({"node": "db"})
    held = state(fake, "db")
    kept(fake, PING, "redis-cli-pong")
    assert probe.run(targets, params) == Verdict("passed", None, {"status": 0})
    kept(fake, PING, "redis-cli-refused")
    assert probe.run(targets, params) == Verdict("failed", None, {"status": 1})
    assert set(ran(fake)) == {PING} and state(fake, "db") == held
    # An answer that is neither says nothing of whether Redis serves.
    for said in (b"", b"PONG", b"pong\n", b"NOAUTH Authentication required.\n", b"PONG\nPONG\n"):
        within.on(list(PING), 0, said)
        assert probe.run(targets, params) == Verdict("error", "unexpected_output")
    within.on(list(PING), 1, b"", b"Unrecognized option or bad number of args\n")
    assert probe.run(targets, params) == Verdict("error", "command_failed")
    with pytest.raises(ValidationError):
        RedisPing.parameters({"node": "db", "unit": "redis-server.service"})


def test_a_failure_of_incus_under_a_probe_is_no_verdict() -> None:
    fake, targets, table = lab()
    table.add(UNIT)
    exec_path = f"/1.0/instances/{INSTANCES['db']}/exec"
    probes: list[tuple[Any, Any]] = [
        (UnitInState(), unit("active")),
        (ContainerState(), container("running")),
        (PgReady(), PgReady.parameters({"node": "db"})),
        (PgQuery(), query()),
        (RedisPing(), RedisPing.parameters({"node": "db"})),
    ]
    for probe, params in probes:
        with fake.refusing("POST", exec_path, 500, MARK), pytest.raises(TargetFailure) as failed:
            probe.run(targets, params)
        assert (str(failed.value), failed.value.alias) == ("incus_error", "db")
        assert MARK not in f"{failed.value!r} {vars(failed.value)}"


def test_what_a_probe_says_of_itself_is_one_sentence() -> None:
    said = [
        UnitInState().describe(unit("active")),
        ContainerState().describe(container("running")),
        PgReady().describe(PgReady.parameters({"node": "db"})),
        PgReady().describe(PgReady.parameters({"node": "db", "ready": False})),
        PgQuery().describe(query()),
        RedisPing().describe(RedisPing.parameters({"node": "db"})),
    ]
    for sentence in said:
        assert sentence.endswith(".") and sentence.count(". ") == 0 and " db " in sentence
    assert "does not accept" in said[3] and "accepts" in said[2]
    # The statement itself is the scenario's and is in no description.
    assert MARK not in said[4] and "appdb" in said[4]
