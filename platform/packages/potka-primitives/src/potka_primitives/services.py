"""The faults of a node's network, of Docker and of the two databases.

Each acts on one service a blueprint runs, and only with that service's
own tool or with what every image has: ``tc`` for the network,
``docker`` for a container, ``systemctl`` for a unit.
"""

from __future__ import annotations

import re
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, model_validator

from potka_primitives.base import Effects, Facts, Params, Primitive, Program, Requires
from potka_primitives.context import Node
from potka_primitives.errors import Refusal
from potka_primitives.files import lines_of, read_text
from potka_primitives.units import RUNNING, SYSTEMCTL, WORDS, after, loaded, unit_state
from potka_primitives.values import AbsolutePath, Unit

TC: Final = Program("tc", "iproute2")
DOCKER: Final = Program("docker", "docker.io")
# The one network interface a target is given when it is made.
INTERFACE: Final = "eth0"
DOCKER_UNITS: Final = ("docker.socket", "docker.service")
# A line of ``tc qdisc show``: a discipline's kind and handle, and what follows them.
_QDISC: Final = re.compile(r"qdisc ([a-z_]+) [0-9a-f]+: (root|parent|dev)\b[^\n]*")
_POSTGRESQL: Final = re.compile(r"postgresql(@[0-9]{1,3}-[a-z0-9_]{1,63})?\.service")
_HBA: Final = re.compile(r"/etc/postgresql/[0-9]{1,3}/[a-z0-9_]{1,63}/pg_hba\.conf")
REJECT: Final = "host all all all reject"


class NetemParams(Params):
    """What a node's network interface does to what it sends."""

    delay_ms: Annotated[int, Field(ge=1, le=60_000)] | None = None
    """The milliseconds every packet is held back."""
    loss_percent: Annotated[int, Field(ge=1, le=100)] | None = None
    """The share of the packets that is dropped."""

    @model_validator(mode="after")
    def _something(self) -> NetemParams:
        if self.delay_ms is None and self.loss_percent is None:
            raise ValueError("one of delay_ms and loss_percent is given")
        return self


class NetNetem(Primitive[NetemParams]):
    """``net.netem``: delay or drop what a node sends on its network interface."""

    name = "net.netem"
    version = 1
    Params = NetemParams
    requires = Requires((TC,))

    def describe(self, params: NetemParams) -> str:
        """One sentence that says what the step does with these parameters."""
        what = [
            *([f"holds every packet back {params.delay_ms} ms"] if params.delay_ms else []),
            *([f"drops {params.loss_percent} % of the packets"] if params.loss_percent else []),
        ]
        return f"On the node {params.node}, {INTERFACE} {' and '.join(what)}."

    def side_effects(self, params: NetemParams) -> Effects:
        """Nothing in a file or of a unit: the interface's queueing discipline."""
        return Effects(other=(f"the queueing discipline of {INTERFACE}",))

    def _ready(self, node: Node, params: NetemParams) -> None:
        ran = node.run(["tc", "qdisc", "show", "dev", INTERFACE]).expect(0)
        text = ran.text()
        said = [_QDISC.fullmatch(line) for line in text.split("\n")[:-1]]
        roots = [line[1] for line in said if line is not None and line[2] == "root"]
        # Every line is a discipline's, the text ends with a line's end, and one is the root's.
        if not text.endswith("\n") or None in said or len(roots) != 1:
            raise Refusal("unexpected_output", program="tc", status=0)
        if roots[0] == "netem":
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: NetemParams) -> Facts:
        argv = ["tc", "qdisc", "add", "dev", INTERFACE, "root", "netem"]
        if params.delay_ms is not None:
            argv += ["delay", f"{params.delay_ms}ms"]
        if params.loss_percent is not None:
            argv += ["loss", f"{params.loss_percent}%"]
        node.run(argv).expect(0)
        return {}


class ContainerParams(Params):
    """A container of the node's Docker."""

    container: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
    """The container's name."""


class DockerKillContainer(Primitive[ContainerParams]):
    """``docker.kill_container``: kill a running container."""

    name = "docker.kill_container"
    version = 1
    Params = ContainerParams
    requires = Requires((SYSTEMCTL, DOCKER), service="docker.io")

    def describe(self, params: ContainerParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Kills the container {params.container} on the node {params.node}."

    def side_effects(self, params: ContainerParams) -> Effects:
        """Nothing in a file or of a unit: a container of the node's Docker."""
        return Effects(other=(f"the container {params.container}",))

    def _ready(self, node: Node, params: ContainerParams) -> None:
        # A docker command to a daemon that is stopped starts it through its socket:
        # the daemon is asked only when it runs, so that this reads and changes nothing.
        if unit_state(node, DOCKER_UNITS[1]).active not in RUNNING:
            raise Refusal("unit_not_active")
        ran = node.run(
            [
                "docker",
                "inspect",
                "--type=container",
                "--format={{.State.Running}}",
                "--",
                params.container,
            ]
        )
        if ran.exit_code != 0:
            if ran.said("No such container"):
                raise Refusal("container_not_found")
            ran.expect(0)
        said = ran.text()
        if said == "false\n":
            raise Refusal("already_in_that_state")
        if said != "true\n":
            raise Refusal("unexpected_output", program="docker", status=0)

    def _apply(self, node: Node, params: ContainerParams) -> Facts:
        node.run(["docker", "kill", "--", params.container]).expect(0)
        return {}


class DaemonParams(Params):
    """The node whose Docker daemon is stopped."""


class DockerStopDaemon(Primitive[DaemonParams]):
    """``docker.stop_daemon``: stop Docker's daemon and the socket that would start it again.

    A unit that requires the daemon and is started later starts it again;
    that no such unit runs on the node is the scenario's to hold.
    """

    name = "docker.stop_daemon"
    version = 1
    Params = DaemonParams
    requires = Requires((SYSTEMCTL,), service="docker.io")
    reports = frozenset({"socket_active_state", "service_active_state"})
    words = WORDS

    def describe(self, params: DaemonParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Stops docker.socket and docker.service on the node {params.node}."

    def side_effects(self, params: DaemonParams) -> Effects:
        """The two units."""
        return Effects(units=DOCKER_UNITS)

    def _ready(self, node: Node, params: DaemonParams) -> None:
        states = [loaded(node, unit) for unit in DOCKER_UNITS]
        if not any(state.active in RUNNING for state in states):
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: DaemonParams) -> Facts:
        node.run(["systemctl", "stop", "--", *DOCKER_UNITS], wait=180).expect(0)
        socket, service = (unit_state(node, unit).active for unit in DOCKER_UNITS)
        return {"socket_active_state": socket, "service_active_state": service}


def _postgresql(unit: str) -> str:
    if _POSTGRESQL.fullmatch(unit) is None:
        raise ValueError("the unit is PostgreSQL's: postgresql.service or one cluster's")
    return unit


def _redis(unit: str) -> str:
    if unit != "redis-server.service":
        raise ValueError("the unit is redis-server.service")
    return unit


class _Stop[P: Params](Primitive[P]):
    """Stop a service's unit."""

    requires = Requires((SYSTEMCTL,))
    reports = frozenset({"active_state", "unit_file_state"})
    words = WORDS

    def describe(self, params: P) -> str:
        """One sentence that says what the step does with these parameters."""
        return f"Stops {params.unit} on the node {params.node}."  # type: ignore[attr-defined]

    def side_effects(self, params: P) -> Effects:
        """The unit."""
        return Effects(units=(params.unit,))  # type: ignore[attr-defined]

    def _ready(self, node: Node, params: P) -> None:
        if loaded(node, params.unit).active not in RUNNING:  # type: ignore[attr-defined]
            raise Refusal("already_in_that_state")

    def _apply(self, node: Node, params: P) -> Facts:
        unit: str = params.unit  # type: ignore[attr-defined]
        node.run(["systemctl", "stop", "--", unit], wait=180).expect(0)
        return after(node, unit)


class PgUnitParams(Params):
    """A unit of PostgreSQL."""

    unit: Annotated[Unit, AfterValidator(_postgresql)]
    """``postgresql.service`` or a cluster's ``postgresql@<version>-<cluster>.service``."""


class PgStop(_Stop[PgUnitParams]):
    """``pg.stop``: stop PostgreSQL."""

    name = "pg.stop"
    version = 1
    Params = PgUnitParams
    requires = Requires((SYSTEMCTL,), service="postgresql")


class RedisUnitParams(Params):
    """Redis's unit."""

    unit: Annotated[Unit, AfterValidator(_redis)] = "redis-server.service"
    """``redis-server.service``."""


class RedisStop(_Stop[RedisUnitParams]):
    """``redis.stop``: stop Redis."""

    name = "redis.stop"
    version = 1
    Params = RedisUnitParams
    requires = Requires((SYSTEMCTL,), service="redis-server")


def _hba(path: str) -> str:
    if _HBA.fullmatch(path) is None:
        raise ValueError("the file is a cluster's /etc/postgresql/<version>/<cluster>/pg_hba.conf")
    return path


def _words(line: str) -> list[str]:
    """The words of a line of ``pg_hba.conf`` before its comment."""
    return line.split("#", 1)[0].split()


def _included(line: str) -> bool:
    """Whether the line takes rules in from another file."""
    return bool(_words(line)) and _words(line)[0].startswith("include")


def _rule(line: str) -> str | None:
    """The connection type of a line of ``pg_hba.conf`` that is a rule, else None."""
    words = _words(line)
    return words[0] if words and not words[0].startswith(("include", "@")) else None


def _rules(text: str) -> list[str]:
    """The lines of a ``pg_hba.conf`` whose every rule is one line.

    Raises:
        Refusal: ``no_place_for_defect``: a line goes on in the next one (a
            backslash at its end), so a line here is no rule of its own.
    """
    lines = lines_of(text)
    if any(line.rstrip("\r\n").endswith("\\") for line in lines):
        raise Refusal("no_place_for_defect")
    return lines


def rejecting(text: str) -> str:
    """TEXT with a rule that rejects every connection over the network before every other.

    The rule is put before the first line that is a rule or that takes
    rules in from another file, so that none is read before it.
    """
    lines = _rules(text)
    first = next(
        (index for index, line in enumerate(lines) if _rule(line) or _included(line)), None
    )
    if first is None or not any(_rule(line) for line in lines):
        raise Refusal("no_place_for_defect")
    if _words(lines[first]) == REJECT.split():
        raise Refusal("already_in_that_state")
    end = "\r\n" if lines[first].endswith("\r\n") else "\n"
    return "".join([*lines[:first], REJECT + end, *lines[first:]])


def without_hosts(text: str) -> str:
    """TEXT without its rules for connections over the network.

    Raises:
        Refusal: ``no_place_for_defect`` for a file that takes rules in
            from another: what those hold is not seen here.
    """
    lines = _rules(text)
    if any(_included(line) for line in lines):
        raise Refusal("no_place_for_defect")
    kept = [line for line in lines if not (_rule(line) or "").startswith("host")]
    if len(kept) == len(lines):
        raise Refusal("already_in_that_state")
    return "".join(kept)


class HbaParams(Params):
    """A cluster's client authentication file, its unit, and what becomes of the file."""

    path: Annotated[AbsolutePath, AfterValidator(_hba)]
    """The cluster's ``pg_hba.conf``."""
    unit: Annotated[Unit, AfterValidator(_postgresql)]
    """The unit that is asked to read the file again."""
    defect: Literal["reject", "no_entry"]
    """``reject``: every connection over the network is rejected; ``no_entry``: none has a rule."""

    @model_validator(mode="after")
    def _one_cluster(self) -> HbaParams:
        # A cluster's own unit is named after it: postgresql@<version>-<cluster>.service.
        of_unit = self.unit.removeprefix("postgresql").removesuffix(".service")
        _, _, _, version, cluster, _ = self.path.split("/")
        if of_unit and of_unit != f"@{version}-{cluster}":
            raise ValueError("the unit is the cluster's whose file it is, or postgresql.service")
        return self


class PgBreakHba(Primitive[HbaParams]):
    """``pg.break_hba``: make PostgreSQL refuse the connections that come over the network."""

    name = "pg.break_hba"
    version = 1
    Params = HbaParams
    requires = Requires((SYSTEMCTL,), service="postgresql")
    reports = frozenset({"bytes"})

    def describe(self, params: HbaParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Changes {params.path} on the node {params.node} ({params.defect}) and asks"
            f" {params.unit} to read it again."
        )

    def side_effects(self, params: HbaParams) -> Effects:
        """The file and the unit."""
        return Effects(files=(params.path,), units=(params.unit,))

    def _result(self, node: Node, params: HbaParams) -> str:
        text = read_text(node, params.path)
        return rejecting(text) if params.defect == "reject" else without_hosts(text)

    def _ready(self, node: Node, params: HbaParams) -> None:
        # A unit that does not run reads nothing again: the fault could not be made.
        if loaded(node, params.unit).active not in RUNNING:
            raise Refusal("unit_not_active")
        self._result(node, params)

    def _apply(self, node: Node, params: HbaParams) -> Facts:
        content = self._result(node, params).encode()
        node.write(params.path, content)
        node.run(["systemctl", "reload", "--", params.unit], wait=180).expect(0)
        return {"bytes": len(content)}


PRIMITIVES: Final = (
    NetNetem,
    DockerKillContainer,
    DockerStopDaemon,
    PgStop,
    PgBreakHba,
    RedisStop,
)
