"""The faults that act on a unit, on the in-memory lab."""

from __future__ import annotations

from typing import Any

import pytest
from potka_incus.fake import FakeIncus
from potka_primitives import library, units
from potka_primitives.base import Effects, Requires
from potka_primitives.context import Targets
from potka_primitives.errors import FaultFailed, Refusal, SetupRefused
from potka_primitives.units import SystemdAction, SystemdBreakUnit, UnitState
from potka_testing.outputs import output
from potka_testing.target_units import SHOW, Files, Units
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

MARK = "zq-marked"
UNIT = f"{MARK}.service"
DROP_IN = f"/etc/systemd/system/{UNIT}.d/override.conf"


def lab(*programs: str) -> tuple[FakeIncus, Targets, Units]:
    fake, targets = run(programs=programs or ("systemctl", "mkdir"))
    within = inside(fake, "web")
    within.mkdir("/etc/systemd/system")
    Files(within)
    table = Units(within)
    table.add(UNIT)
    return fake, targets, table


def act(action: str, unit: str = UNIT) -> Any:
    return SystemdAction.parameters({"node": "web", "unit": unit, "action": action})


def refused(error: Any, reason: str) -> None:
    assert (str(error), error.alias) == (reason, "web")
    assert error.__context__ is None and error.__cause__ is None
    assert MARK not in f"{error!r} {vars(error)}" and INSTANCES["web"] not in repr(error)


def test_the_library_has_the_two_systemd_faults() -> None:
    assert {"systemd.action", "systemd.break_unit"} <= set(library.PRIMITIVES)
    assert SystemdAction.requires == Requires((units.SYSTEMCTL,))
    assert SystemdBreakUnit.requires == Requires((units.SYSTEMCTL, units.MKDIR))
    assert (units.SYSTEMCTL.package, units.MKDIR.package) == ("systemd", "coreutils")


def test_the_kept_outputs_are_what_the_stand_in_s_systemd_says() -> None:
    _, targets, table = lab()
    node = targets.node("web")
    cases = {
        "systemctl-show-active": ["loaded", "active", "enabled"],
        "systemctl-show-inactive": ["loaded", "inactive", "disabled"],
        "systemctl-show-failed": ["loaded", "failed", "enabled"],
        "systemctl-show-masked": ["masked", "inactive", "masked"],
        "systemctl-show-static": ["loaded", "active", "static"],
    }
    for name, said in cases.items():
        table.units[UNIT] = list(said)
        assert node.run([*SHOW, UNIT]).stdout == output(name).content
        assert units.unit_state(node, UNIT) == UnitState(*said)
    assert node.run([*SHOW, "none.service"]).stdout == output("systemctl-show-not-found").content
    assert units.unit_state(node, "none.service") == UnitState("not-found", "inactive", "")
    with pytest.raises(Refusal, match="unit_not_found"):
        units.loaded(node, "none.service")


@pytest.mark.parametrize(
    "said",
    [
        b"",
        b"LoadState=loaded\nActiveState=active\n",
        b"LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nMore=x\n",
        b"LoadState=loaded\nLoadState=loaded\nActiveState=active\nUnitFileState=enabled\n",
        b"LoadState=loaded\nActiveState=running\nUnitFileState=enabled\n",
        b"LoadState=there\nActiveState=active\nUnitFileState=enabled\n",
        b"LoadState=loaded\nActiveState=active\nUnitFileState=on\n",
        b"LoadState loaded\nActiveState=active\nUnitFileState=enabled\n",
        b"loaded\nactive\nenabled\n",
        b"LoadState=loaded\nActiveState=Active\nUnitFileState=enabled\n",
        b"\xff\n",
    ],
)
def test_a_unit_s_state_in_another_form_is_refused_and_never_guessed(said: bytes) -> None:
    fake, targets, _ = lab()
    inside(fake, "web").on([*SHOW, UNIT], 0, said)
    with pytest.raises(SetupRefused) as unread:
        SystemdAction().ready(targets, act("stop"))
    refused(unread.value, "unexpected_output")
    inside(fake, "web").on([*SHOW, UNIT], 1, b"", b"Failed to connect to bus\n")
    with pytest.raises(SetupRefused) as failed:
        SystemdAction().ready(targets, act("stop"))
    refused(failed.value, "command_failed")


@pytest.mark.parametrize(
    ("action", "before", "after"),
    [
        ("stop", ["loaded", "active", "enabled"], ["loaded", "inactive", "enabled"]),
        ("start", ["loaded", "inactive", "enabled"], ["loaded", "active", "enabled"]),
        ("start", ["loaded", "failed", "enabled"], ["loaded", "active", "enabled"]),
        ("restart", ["loaded", "active", "enabled"], ["loaded", "active", "enabled"]),
        ("restart", ["loaded", "inactive", "disabled"], ["loaded", "active", "disabled"]),
        ("disable", ["loaded", "active", "enabled"], ["loaded", "active", "disabled"]),
        ("mask", ["loaded", "active", "enabled"], ["masked", "active", "masked"]),
    ],
)
def test_an_action_is_asked_of_systemd_and_the_unit_s_state_is_the_fact(
    action: str, before: list[str], after: list[str]
) -> None:
    fake, targets, table = lab()
    table.units[UNIT] = list(before)
    primitive, params = SystemdAction(), act(action)
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert table.asked == [] and state(fake, "web") == held
    facts = primitive.apply(targets, params)
    assert table.asked == [(action, UNIT)] and table.units[UNIT] == after
    assert facts == {"active_state": after[1], "unit_file_state": after[2]}
    assert set(facts) == SystemdAction.reports
    assert primitive.side_effects(params).units == (UNIT,)
    commands = [asked.argv for asked in inside(fake, "web").executions]
    assert commands == [(*SHOW, UNIT), ("systemctl", action, "--", UNIT), (*SHOW, UNIT)]


def test_what_systemd_keeps_of_a_mask_or_of_a_unit_no_longer_enabled_is_declared() -> None:
    primitive = SystemdAction()
    for action in ("start", "stop", "restart"):
        assert primitive.side_effects(act(action)) == Effects(units=(UNIT,))
    # A mask is a link of the unit's name; what enables a unit are links that systemd takes away.
    assert primitive.side_effects(act("mask")) == Effects(
        files=(f"/etc/systemd/system/{UNIT}",), units=(UNIT,)
    )
    assert primitive.side_effects(act("disable")) == Effects(
        units=(UNIT,), other=("the links that enable the unit",)
    )


@pytest.mark.parametrize(
    ("action", "left"),
    [
        ("stop", ["loaded", "active", "enabled"]),
        ("stop", ["loaded", "reloading", "enabled"]),
        ("disable", ["loaded", "active", "enabled-runtime"]),
        ("disable", ["loaded", "active", "enabled"]),
        ("mask", ["loaded", "active", "enabled"]),
    ],
)
def test_an_action_that_systemd_ends_well_and_that_did_not_take_is_a_failed_fault(
    action: str, left: list[str]
) -> None:
    fake, targets, table = lab()
    table.units[UNIT] = list(left)
    # systemd says 0 and the unit is as it was.
    inside(fake, "web").on(["systemctl", action, "--", UNIT], 0)
    with pytest.raises(FaultFailed) as untaken:
        SystemdAction().apply(targets, act(action))
    refused(untaken.value, "command_failed")
    assert (untaken.value.program, untaken.value.status) == ("systemctl", 0)
    assert table.units[UNIT] == left
    # What a start or a restart leaves is the fact, whatever it is.
    inside(fake, "web").on(["systemctl", "restart", "--", UNIT], 0)
    assert SystemdAction().apply(targets, act("restart"))["active_state"] == left[1]


@pytest.mark.parametrize(
    ("action", "said"),
    [
        ("stop", ["loaded", "inactive", "enabled"]),
        ("stop", ["loaded", "failed", "enabled"]),
        ("start", ["loaded", "active", "enabled"]),
        ("start", ["loaded", "activating", "enabled"]),
        ("disable", ["loaded", "active", "disabled"]),
        ("disable", ["loaded", "active", "static"]),
        ("mask", ["masked", "inactive", "masked"]),
        ("mask", ["loaded", "active", "masked-runtime"]),
    ],
)
def test_a_fault_already_in_place_is_refused(action: str, said: list[str]) -> None:
    _, targets, table = lab()
    table.units[UNIT] = list(said)
    with pytest.raises(SetupRefused) as there:
        SystemdAction().ready(targets, act(action))
    refused(there.value, "already_in_that_state")
    assert table.asked == []


@pytest.mark.parametrize("action", ["start", "stop", "restart", "disable", "mask"])
def test_a_unit_systemd_has_not_loaded_is_refused(action: str) -> None:
    _, targets, table = lab()
    del table.units[UNIT]
    with pytest.raises(SetupRefused) as unknown:
        SystemdAction().ready(targets, act(action))
    refused(unknown.value, "unit_not_found")
    if action != "mask":
        table.units[UNIT] = ["masked", "inactive", "masked"]
        with pytest.raises(SetupRefused) as masked:
            SystemdAction().ready(targets, act(action))
        refused(masked.value, "unit_not_found")


def test_a_start_that_fails_is_the_fault_and_any_other_failure_is_not() -> None:
    _, targets, table = lab()
    table.broken.add(UNIT)
    facts = SystemdAction().apply(targets, act("restart"))
    assert facts == {"active_state": "failed", "unit_file_state": "enabled"}
    fake, targets, table = lab()
    inside(fake, "web").on(["systemctl", "restart", "--", UNIT], 5, b"", b"not found\n")
    with pytest.raises(FaultFailed) as failed:
        SystemdAction().apply(targets, act("restart"))
    refused(failed.value, "command_failed")
    assert (failed.value.program, failed.value.status) == ("systemctl", 5)
    # A start that fails is the same fault as a restart that does.
    _, targets, table = lab()
    table.units[UNIT] = ["loaded", "inactive", "enabled"]
    table.broken.add(UNIT)
    assert SystemdAction().apply(targets, act("start")) == {
        "active_state": "failed",
        "unit_file_state": "enabled",
    }
    # The status 1 of the other three is a failure: nothing of them is a unit that does not come up.
    for action in ("stop", "disable", "mask"):
        fake, targets, table = lab()
        inside(fake, "web").on(["systemctl", action, "--", UNIT], 1, b"", b"Job failed.\n")
        with pytest.raises(FaultFailed) as other:
            SystemdAction().apply(targets, act(action))
        refused(other.value, "command_failed")
        assert (other.value.program, other.value.status) == ("systemctl", 1)
        assert table.units[UNIT] == ["loaded", "active", "enabled"]


def test_a_unit_without_a_file_state_has_the_fact_none() -> None:
    _, targets, table = lab()
    table.units[UNIT] = ["loaded", "active", ""]
    facts = SystemdAction().apply(targets, act("restart"))
    assert facts == {"active_state": "active", "unit_file_state": "none"}
    assert "" not in SystemdAction.words and "none" in SystemdAction.words


@pytest.mark.parametrize(
    "wrong",
    [
        {"action": "enable"},
        {"action": "kill"},
        {"action": "reload"},
        {"unit": "systemd-journald.service"},
        {"unit": "dbus.socket"},
        {"unit": "incus-agent.service"},
        {"unit": "user@0.service"},
        {"unit": "user-runtime-dir@0.service"},
        {"unit": "getty@tty1.service"},
        {"unit": "serial-getty@hvc0.service"},
        {"unit": "container-getty@1.service"},
        {"unit": "console-getty.service"},
        {"unit": "emergency.service"},
        {"unit": "rescue.service"},
        {"unit": "app@.service"},
        {"unit": "nginx"},
        {"unit": "--now.service"},
        {"unit": "a/b.service"},
        {"more": 1},
    ],
)
def test_an_action_is_one_of_five_on_a_unit_that_is_a_fault_s(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SystemdAction.parameters({"node": "web", "unit": UNIT, "action": "stop", **wrong})
    for unit in (
        "docker.socket",
        "apt-daily.timer",
        "postgresql@16-main.service",
        "users.service",
        "rescued.service",
        "emergency-stop.service",
        "gettysburg.service",
    ):
        assert act("stop", unit).unit == unit


def broken(exec_start: str = f"/opt/{MARK}/bin/served --port 80", unit: str = UNIT) -> Any:
    return SystemdBreakUnit.parameters({"node": "web", "unit": unit, "exec_start": exec_start})


def test_a_service_gets_a_command_that_cannot_be_run_in_a_drop_in_file() -> None:
    fake, targets, table = lab()
    within = inside(fake, "web")
    primitive, params = SystemdBreakUnit(), broken()
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held and table.asked == []
    assert primitive.apply(targets, params) == {}
    written = within.nodes[DROP_IN]
    assert written.content == (
        f"[Service]\nExecStart=\nExecStart=/opt/{MARK}/bin/served --port 80\n".encode()
    )
    assert (written.uid, written.gid, written.mode) == (0, 0, 0o644)
    # Nothing is restarted: systemd only reads its files again.
    assert table.asked == [("daemon-reload",)] and table.units[UNIT][1] == "active"
    made = set(within.nodes) - set(held[0])
    assert made == set(primitive.side_effects(params).files)
    assert primitive.side_effects(params) == Effects(
        files=(f"/etc/systemd/system/{UNIT}.d", DROP_IN), units=(UNIT,)
    )
    commands = [asked.argv for asked in within.executions]
    assert commands == [
        (*SHOW, UNIT),
        ("mkdir", "--parents", "--", f"/etc/systemd/system/{UNIT}.d"),
        ("systemctl", "daemon-reload"),
    ]
    # No word of the library's own is in what it wrote.
    assert b"potka" not in written.content and b"break" not in written.content


def test_the_command_s_program_is_one_that_is_not_in_the_target() -> None:
    fake, targets, table = lab()
    within = inside(fake, "web")
    primitive = SystemdBreakUnit()
    for there in ("/usr/bin/systemctl", "/etc", "/bin"):
        with pytest.raises(SetupRefused) as exists:
            primitive.ready(targets, broken(f"{there} --{MARK}"))
        refused(exists.value, "program_exists")
    within.mkdir(f"/etc/systemd/system/{UNIT}.d")
    within.write(DROP_IN, b"[Service]\n")
    with pytest.raises(SetupRefused) as written:
        primitive.ready(targets, broken())
    refused(written.value, "file_exists")
    del table.units[UNIT]
    with pytest.raises(SetupRefused) as unknown:
        primitive.ready(targets, broken())
    refused(unknown.value, "unit_not_found")
    assert within.nodes[DROP_IN].content == b"[Service]\n" and table.asked == []


@pytest.mark.parametrize(
    "exec_start",
    [
        "served --port 80",
        "-/opt/x/served",
        "@/opt/x/served",
        "/opt/x/served ; /bin/true",
        "/opt/x/served;/bin/true",
        "/opt/x/served $OPTIONS",
        "/opt/x/served %i",
        "/opt/x/served  --two-blanks",
        "/opt/x/served ",
        "/opt/x/served\n/bin/true",
        '/opt/x/served "quoted"',
        "/opt/x/../served",
        "/opt//served",
        "/",
        "/bin/sh -c x",
        "/opt/x/bash",
        "/opt/x/served /usr/bin/dash",
        "/opt/x/served " + "a" * 1000,
        "",
    ],
)
def test_a_command_that_could_run_something_is_refused(exec_start: str) -> None:
    with pytest.raises(ValidationError) as wrong:
        broken(exec_start)
    # No value is shown (a single sign can be in the error's own words).
    assert len(exec_start) < 2 or exec_start not in str(wrong.value)
    for unit in ("docker.socket", "apt-daily.timer", "systemd-x.service"):
        with pytest.raises(ValidationError):
            broken(unit=unit)


def test_what_a_systemd_fault_says_of_itself() -> None:
    assert SystemdAction().describe(act("stop")) == (
        f"Asks systemd on the node web to stop {UNIT}."
    )
    assert "whose program is not there" in SystemdBreakUnit().describe(broken())
