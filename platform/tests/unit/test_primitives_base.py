"""What a fault primitive and a probe are: the calls of the base classes, the forms, the index."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from potka_core.catalog import lint
from potka_primitives import base, library, values
from potka_primitives.base import (
    BASE_IMAGE,
    Effects,
    Facts,
    Params,
    Primitive,
    Probe,
    Program,
    Requires,
    Step,
    Verdict,
)
from potka_primitives.context import Node, Targets
from potka_primitives.errors import (
    FaultFailed,
    Refusal,
    SetupRefused,
    TargetFailure,
    UnknownName,
    UnknownNode,
)
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

MARK = "zq-marked-value"
# The names the lint knows and no library may have yet.
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


class NoteParams(Params):
    path: values.AbsolutePath
    text: str
    percent: values.Percent = 50


class Note(Primitive[NoteParams]):
    """A primitive for these tests: it writes one file, after one reading command."""

    name = "sample.note"
    version = 3
    Params = NoteParams
    requires = Requires((Program("stat", "coreutils"), Program("findmnt", "util-linux")))
    reports = frozenset({"bytes", "state"})
    words = frozenset({"written"})
    calls: ClassVar[list[str]] = []
    refuse: ClassVar[dict[str, str]] = {}
    facts: ClassVar[dict[str, Any]] = {"bytes": 4, "state": "written"}

    def describe(self, params: NoteParams) -> str:
        return "Writes a note."

    def side_effects(self, params: NoteParams) -> Effects:
        return Effects(files=(params.path,))

    def _hook(self, name: str, node: Node) -> None:
        self.calls.append(name)
        if name in self.refuse:
            raise Refusal(self.refuse[name], program="stat", status=5)

    def _suited(self, node: Node, params: NoteParams) -> None:
        node.run(["stat", "--", "/etc"]).expect(0)
        self._hook("suited", node)

    def _ready(self, node: Node, params: NoteParams) -> None:
        self._hook("ready", node)
        if node.exists(params.path):
            raise Refusal("file_exists")

    def _apply(self, node: Node, params: NoteParams) -> Facts:
        self._hook("apply", node)
        node.write(params.path, params.text.encode())
        return dict(self.facts)


@pytest.fixture(autouse=True)
def fresh() -> None:
    Note.calls.clear()
    Note.refuse.clear()
    Note.facts.clear()
    Note.facts.update({"bytes": 4, "state": "written"})


def given(**more: Any) -> NoteParams:
    return Note.parameters({"node": "web", "path": f"/etc/{MARK}", "text": MARK, **more})


def lab(*programs: str) -> tuple[Any, Targets]:
    fake, targets = run(programs=programs)
    inside(fake, "web").on(["stat", "--", "/etc"], 0)
    return fake, targets


def test_check_finds_every_program_before_it_asks_the_primitive() -> None:
    fake, targets = lab("stat")
    before = state(fake, "web")
    with pytest.raises(SetupRefused) as missing:
        Note().check(targets, given())
    assert (str(missing.value), missing.value.alias, missing.value.program) == (
        "tool_missing",
        "web",
        "findmnt",
    )
    assert missing.value.__context__ is None and missing.value.__cause__ is None
    # Nothing ran and nothing was asked of the primitive: the tools come first.
    assert Note.calls == [] and inside(fake, "web").executions == []
    assert state(fake, "web") == before


def test_the_three_calls_reach_the_primitive_in_the_caller_s_order_and_only_apply_writes() -> None:
    fake, targets = lab("stat", "findmnt")
    note, params = Note(), given()
    before = state(fake, "web")
    note.check(targets, params)
    note.ready(targets, params)
    assert state(fake, "web") == before
    assert note.apply(targets, params) == {"bytes": 4, "state": "written"}
    assert Note.calls == ["suited", "ready", "apply"]
    after, _ = state(fake, "web")
    assert set(after) - set(before[0]) == set(note.side_effects(params).files)
    assert inside(fake, "db").executions == [] and len(inside(fake, "web").executions) == 1


@pytest.mark.parametrize(
    ("call", "raised"),
    [("suited", SetupRefused), ("ready", SetupRefused), ("apply", FaultFailed)],
)
def test_a_refusal_below_a_call_becomes_that_call_s_error(
    call: str, raised: type[Exception]
) -> None:
    fake, targets = lab("stat", "findmnt")
    Note.refuse[call] = "already_in_that_state"
    note, params = Note(), given()
    method = {"suited": note.check, "ready": note.ready, "apply": note.apply}[call]
    with pytest.raises(raised) as refused:
        method(targets, params)
    error = refused.value
    assert type(error) is raised
    assert (str(error), error.args) == ("already_in_that_state", ("already_in_that_state",))
    assert (error.alias, error.program, error.status) == ("web", "stat", 5)  # type: ignore[attr-defined]
    assert error.__context__ is None and error.__cause__ is None
    # Nothing of the parameters or of the instance is in it.
    text = f"{error} {error!r} {vars(error)}"
    assert MARK not in text and INSTANCES["web"] not in text
    # A refused apply wrote nothing here: the refusal came before the write.
    assert f"/etc/{MARK}" not in inside(fake, "web").nodes


def test_a_failure_of_incus_passes_through_every_call_as_it_is() -> None:
    fake, targets = lab("stat", "findmnt")
    note, params = Note(), given()
    exec_path = f"/1.0/instances/{INSTANCES['web']}/exec"
    files = f"/1.0/instances/{INSTANCES['web']}/files"
    with fake.refusing("POST", exec_path, 500, MARK), pytest.raises(TargetFailure) as checked:
        note.check(targets, params)
    with fake.refusing("GET", files, 403, MARK), pytest.raises(TargetFailure) as readied:
        note.ready(targets, params)
    with fake.refusing("POST", files, 500, MARK), pytest.raises(TargetFailure) as applied:
        note.apply(targets, params)
    for failed, kind in (
        (checked, "incus_error"),
        (readied, "forbidden"),
        (applied, "incus_error"),
    ):
        error = failed.value
        assert (str(error), error.alias) == (kind, "web")
        assert error.__context__ is None and error.__cause__ is None
        # Incus's own words, which the test marked, are in nothing the error holds.
        assert MARK not in f"{error!r} {vars(error)}" and INSTANCES["web"] not in repr(error)
    with pytest.raises(UnknownNode):
        note.check(targets, Note.parameters({"node": "zq", "path": "/etc/x", "text": "x"}))


@pytest.mark.parametrize("fact", [MARK, True, 1.5, None, b"x", ["written"]])
def test_a_fact_is_a_number_or_one_of_the_primitive_s_words(fact: Any) -> None:
    _, targets = lab("stat", "findmnt")
    Note.facts["state"] = fact
    with pytest.raises(TypeError, match="a fact that the step does not report") as wrong:
        Note().apply(targets, given())
    # The error says nothing of the fact, and not whose it is.
    assert MARK not in str(wrong.value) and "Note" not in str(wrong.value)


@pytest.mark.parametrize("name", [MARK, f"/etc/{MARK}", "docker.socket", "other", 5, None])
def test_a_fact_has_a_name_the_primitive_reports(name: Any) -> None:
    _, targets = lab("stat", "findmnt")
    Note.facts[name] = 4
    with pytest.raises(TypeError, match="a fact that the step does not report") as wrong:
        Note().apply(targets, given())
    assert MARK not in str(wrong.value)


def test_parameters_are_strict_and_an_error_shows_no_value() -> None:
    assert given().model_dump() == {
        "node": "web",
        "path": f"/etc/{MARK}",
        "text": MARK,
        "percent": 50,
    }
    wrong: list[dict[str, Any]] = [
        {"node": "web", "path": f"/etc/{MARK}", "text": MARK, "more": MARK},
        {"node": "web", "path": f"{MARK}/relative", "text": "x"},
        {"node": "web", "path": f"/etc/{MARK}", "text": 5},
        {"node": "web", "path": f"/etc/{MARK}", "text": "x", "percent": "50"},
        {"node": "web", "path": f"/etc/{MARK}", "text": "x", "percent": 101},
        {"node": f"Web-{MARK}", "path": "/etc/x", "text": "x"},
        {"path": f"/etc/{MARK}", "text": "x"},
    ]
    for one in wrong:
        with pytest.raises(ValidationError) as refused:
            Note.parameters(one)
        assert MARK not in str(refused.value) and MARK not in repr(refused.value)
    frozen = given()
    with pytest.raises(ValidationError):
        frozen.text = "other"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "relative",
        "//etc/x",
        "/etc//x",
        "/etc/./x",
        "/etc/../x",
        "/etc/x/",
        "/etc/a b",
        "/etc/a\tb",
        "/etc/a\nb",
        "/etc/\x7f",
        "/etc/é",
        "/dev",
        "/dev/null",
        "/proc/1/mem",
        "/sys/kernel",
        "",
        "/" + "a" * 4096,
    ],
)
def test_a_path_is_absolute_normal_printable_and_of_no_refused_root(path: str) -> None:
    with pytest.raises(ValidationError):
        Note.parameters({"node": "web", "path": path, "text": "x"})


@pytest.mark.parametrize("path", ["/etc/x", "/devices/x", "/process", "/systems/a.b-c_d", "/-x"])
def test_a_path_of_the_form_is_taken_as_it_is(path: str) -> None:
    assert Note.parameters({"node": "web", "path": path, "text": "x"}).path == path
    assert values.below("/etc/nginx/x", "/etc/nginx") and values.below("/etc/nginx", "/etc/nginx")
    assert not values.below("/etc/nginx2/x", "/etc/nginx") and values.below("/x", "/")


class UnitParams(Params):
    unit: values.Unit


@pytest.mark.parametrize(
    "unit",
    [
        "nginx",
        "nginx.conf",
        "nginx.service.d",
        "a/b.service",
        "../x.service",
        "-x.service",
        "a b.service",
        "x.service\n",
        ".service",
        "a" * 241 + ".service",
        "systemd-journald.service",
        "systemd-networkd.socket",
        "dbus.service",
        "dbus-broker.service",
        "incus-agent.service",
        "nginx.mount",
        "",
    ],
)
def test_a_unit_is_a_service_a_socket_or_a_timer_and_none_of_the_lab_s_own(unit: str) -> None:
    with pytest.raises(ValidationError) as refused:
        UnitParams.model_validate({"node": "web", "unit": unit})
    # No value is shown (the rule's own words name the suffixes).
    assert unit in ("", ".service") or unit not in str(refused.value)


@pytest.mark.parametrize(
    "unit", ["nginx.service", "postgresql@16-main.service", "docker.socket", "apt-daily.timer"]
)
def test_a_unit_of_the_form_is_taken(unit: str) -> None:
    assert UnitParams.model_validate({"node": "web", "unit": unit}).unit == unit


def test_a_class_that_is_no_step_does_not_load() -> None:
    def body(**more: Any) -> dict[str, Any]:
        return {"name": "sample.other", "version": 1, "Params": NoteParams, **more}

    for wrong in (
        body(name="Sample.Other"),
        body(name="sample.other.more"),
        body(name="sample-other"),
        body(version=0),
        body(version=True),
        body(version="1"),
        body(Params=dict),
        body(Params=NoteParams(node="web", path="/x", text="x")),
        body(requires=Requires((Program("/usr/bin/stat", "coreutils"),))),
        body(requires=Requires((Program("-stat", "coreutils"),))),
        {"name": "sample.other", "Params": NoteParams},
        {"name": "sample.other", "version": 1},
    ):
        with pytest.raises(TypeError):
            type("Other", (Primitive,), wrong)
    for call in ("check", "ready", "apply"):
        with pytest.raises(TypeError, match=f"replaces {call}"):
            type("Other", (Primitive,), body(**{call: lambda self: None}))
        with pytest.raises(TypeError, match=f"replaces {call}"):
            type("Deeper", (Note,), {call: lambda self: None})
    with pytest.raises(TypeError, match="replaces run"):
        type("Other", (Probe,), body(run=lambda self: None))
    # Whoever in a class's bases defines the call, and whatever else is the base classes' own.
    mixin = type("Mixin", (), {"apply": lambda self, targets, params: {}})
    with pytest.raises(TypeError, match="replaces apply"):
        type("Mixed", (mixin, Note), {})
    for own in ("_facts", "parameters"):
        with pytest.raises(TypeError, match=f"replaces {own}"):
            type("Deeper", (Note,), {own: lambda self, given: given})
        with pytest.raises(TypeError, match=f"replaces {own}"):
            type("Other", (Probe,), body(**{own: lambda self, given: given}))
    plain: Any = type("Plain", (Note,), {"words": frozenset({"other"})})
    assert plain.name == "sample.note"
    # A class without a name is a base for others and is held to nothing yet.
    family = type("Family", (Primitive,), {})
    assert vars(type("Other", (family,), body()))["name"] == "sample.other"


class Level(Probe[NoteParams]):
    """A probe for these tests."""

    name = "sample_level"
    version = 1
    Params = NoteParams
    reports = frozenset({"percent", "type"})
    words = frozenset({"ext4"})
    verdict: ClassVar[list[Any]] = []

    def describe(self, params: NoteParams) -> str:
        return "Measures a level."

    def _measure(self, targets: Targets, params: NoteParams) -> Verdict:
        found = self.verdict[0]
        if isinstance(found, Exception):
            raise found
        return found  # type: ignore[no-any-return]


def test_a_probe_passes_fails_or_could_not_measure() -> None:
    fake, targets = lab()
    before = state(fake, "web")
    probe, params = Level(), given()
    for verdict in (
        Verdict("passed", None, {"percent": 41, "type": "ext4"}),
        Verdict("failed", None, {"percent": 97}),
        Verdict("error", "root_disk_size_missing"),
    ):
        Level.verdict[:] = [verdict]
        assert probe.run(targets, params) == verdict
    Level.verdict[:] = [Refusal("unexpected_output", program="stat", status=0)]
    assert probe.run(targets, params) == Verdict("error", "unexpected_output")
    Level.verdict[:] = [TargetFailure("unreachable", alias="web")]
    with pytest.raises(TargetFailure):
        probe.run(targets, params)
    Level.verdict[:] = [Verdict("passed", None, {"type": MARK})]
    with pytest.raises(TypeError, match="a fact that the step does not report"):
        probe.run(targets, params)
    assert state(fake, "web") == before


@pytest.mark.parametrize(
    "verdict",
    [
        Verdict("error", None),
        Verdict("error", "made_up"),
        Verdict("error", MARK),
        Verdict("passed", "tool_missing"),
        Verdict("failed", "unexpected_output"),
        Verdict("pass"),  # type: ignore[arg-type]
        Verdict("", None),  # type: ignore[arg-type]
    ],
)
def test_a_verdict_that_is_none_is_not_passed_on(verdict: Verdict) -> None:
    _, targets = lab()
    Level.verdict[:] = [verdict]
    with pytest.raises(TypeError, match="a verdict that is none") as wrong:
        Level().run(targets, given())
    assert MARK not in str(wrong.value)


def test_parameters_are_what_json_has() -> None:
    fits = {"node": "web", "path": "/etc/x", "text": "x"}
    with pytest.raises(TypeError, match="a parameter's name is a string"):
        Note.parameters({**fits, 1: "x"})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        Note.parameters({**fits, "text": b"x"})  # type: ignore[dict-item]
    for number in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="JSON"):
            Note.parameters({**fits, "percent": number})
    # A list is a sequence and a whole number an integer, as JSON has them.
    assert Note.parameters({**fits, "percent": 7}).percent == 7


def test_the_index_names_each_step_once_and_cannot_be_changed() -> None:
    made = library.index((Note,))
    assert dict(made) == {"sample.note": Note}
    with pytest.raises(TypeError):
        made["other"] = Note  # type: ignore[index]
    with pytest.raises(ValueError, match=r"two steps are named sample\.note"):
        library.index((Note, Note))
    for lookup in (library.primitive, library.probe):
        with pytest.raises(UnknownName) as unknown:
            lookup(MARK)
        assert (str(unknown.value), unknown.value.name) == ("unknown_name", MARK)


def test_the_library_has_only_names_the_lint_knows_and_none_that_is_not_built() -> None:
    assert set(library.PRIMITIVES) <= lint.PRIMITIVES - NOT_BUILT
    assert set(library.PROBES) <= lint.PROBES
    assert NOT_BUILT == lint.LOAD_PRIMITIVES | set(lint.TOOLS) | lint.UNSUPPORTED
    assert NOT_BUILT <= lint.PRIMITIVES and len(NOT_BUILT) == 10
    for name, step in {**library.PRIMITIVES, **library.PROBES}.items():
        assert step.name == name
        assert library.primitive(name) if name in library.PRIMITIVES else library.probe(name)


def test_every_program_is_the_service_s_own_or_in_every_golden_image() -> None:
    from potka_core.images import load_table

    table = load_table()
    assert BASE_IMAGE == {"coreutils", "util-linux", "systemd"}
    # The table lists what a blueprint adds to its operating system image: it has none
    # of the three, which is why the library names them.
    assert not any(table.everywhere(package) for package in BASE_IMAGE)
    steps: list[type[Step[Any]]] = [Note, *library.PRIMITIVES.values(), *library.PROBES.values()]
    for step in steps:
        for program in step.requires.programs:
            assert (
                program.package == step.requires.service
                or program.package in BASE_IMAGE
                or table.everywhere(program.package)
            ), (step.name, program.name)
    assert base.Requires() == Requires((), None)
