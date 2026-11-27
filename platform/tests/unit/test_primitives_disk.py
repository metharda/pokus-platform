"""The faults and probes of a full disk, on the in-memory lab."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from potka_cli.main import library_views
from potka_core.catalog.lint import lint
from potka_incus.fake import FakeIncus
from potka_incus.inside import Ended
from potka_primitives import disk, library
from potka_primitives.base import Effects, Requires, Verdict
from potka_primitives.context import Targets
from potka_primitives.disk import (
    DiskFill,
    DiskUsageBelow,
    InodeExhaust,
    InodeUsageBelow,
    PgFillDisk,
)
from potka_primitives.errors import FaultFailed, SetupRefused, TargetFailure
from potka_testing.fixture_catalog import copy_fixture, edit_yaml
from potka_testing.outputs import output
from potka_testing.target_disk import Disk
from potka_testing.targets import INSTANCES, PROJECT, inside, run, state
from potka_testing.tool_views import TOOLS
from pydantic import ValidationError

MIB = 1024**2
GIB = 1024**3
SIZE = 8 * GIB
IMAGE = 900 * MIB
MARK = "zq-marked"
FILE = f"/srv/{MARK}.part"
PROGRAMS = ("findmnt", "fallocate", "stat", "mkdir", "touch")
FINDMNT = ("findmnt", "--noheadings", "--output", "TARGET")


def lab(driver: str = "lvm", **more: Any) -> tuple[FakeIncus, Targets, Disk]:
    fake, targets = run(driver, programs=more.pop("programs", PROGRAMS))
    return fake, targets, Disk(inside(fake, "db"), **more)


def fill(percent: int, path: str = FILE) -> disk.FillParams:
    return DiskFill.parameters({"node": "db", "path": path, "percent": percent})


def ran(fake: FakeIncus) -> list[tuple[str, ...]]:
    return [asked.argv for asked in inside(fake, "db").executions]


def refused(error: Any, reason: str) -> None:
    """ERROR is the library's, says REASON alone and holds nothing of the step."""
    assert (str(error), error.alias) == (reason, "db")
    assert error.__context__ is None and error.__cause__ is None
    text = f"{error!r} {vars(error)}"
    assert MARK not in text and INSTANCES["db"] not in text and "/srv" not in text


def test_the_library_has_the_disk_family() -> None:
    assert set(library.PRIMITIVES) >= {"disk.fill", "disk.inode_exhaust", "pg.fill_disk"}
    assert set(library.PROBES) >= {"disk_usage_below", "inode_usage_below"}
    assert DiskFill.requires == Requires((disk.FINDMNT, disk.FALLOCATE))
    assert PgFillDisk.requires == Requires((disk.FINDMNT, disk.FALLOCATE), "postgresql")
    assert InodeExhaust.requires.programs == (disk.FINDMNT, disk.STAT, disk.MKDIR, disk.TOUCH)
    assert (DiskUsageBelow.requires, InodeUsageBelow.requires) == (
        Requires(),
        Requires((disk.STAT,)),
    )
    packages = {program.name: program.package for program in InodeExhaust.requires.programs}
    assert packages == {
        "findmnt": "util-linux",
        "stat": "coreutils",
        "mkdir": "coreutils",
        "touch": "coreutils",
    }
    assert disk.FALLOCATE.package == "util-linux"


def test_the_kept_outputs_are_what_the_stand_in_s_programs_print() -> None:
    fake, targets, programs = lab()
    node = targets.node("db")
    assert node.run([*FINDMNT, "--target=/srv"]).stdout == output("findmnt-root").content
    programs.mounts["/srv"] = "/var/lib/postgresql"
    assert node.run([*FINDMNT, "--target=/srv"]).stdout == output("findmnt-other").content
    programs.inodes, programs.taken = 524_288, 524_288 - 402_113 - len(inside(fake, "db").nodes)
    counted = node.run(["stat", "--file-system", "--format=%c:%d:%T", "--", "/srv"])
    assert counted.stdout == output("stat-fs-ext4").content
    for name in ("fallocate-enospc", "fallocate-edquot", "touch-enospc", "touch-edquot"):
        kept = output(name)
        assert (kept.status, kept.stream, kept.source) == (1, "stderr", "manual")
        assert disk.FULL[kept.text().rstrip("\n").rsplit(": ", 1)[1]] == name[-6:].upper()


def test_a_file_is_allocated_in_steps_up_to_the_share_of_the_configured_size() -> None:
    fake, targets, _ = lab()
    primitive, params = DiskFill(), fill(50)
    before = state(fake, "db")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert ran(fake) == [(*FINDMNT, "--target=/srv")]
    assert state(fake, "db") == before
    facts = primitive.apply(targets, params)
    missing = SIZE // 2 - IMAGE
    step = -(-missing // 16)
    assert ran(fake)[1:] == [
        ("fallocate", f"--length={min(step * count, missing)}", "--", FILE)
        for count in range(1, 17)
    ]
    assert facts == {
        "size_bytes": SIZE,
        "target_bytes": SIZE // 2,
        "usage_bytes": SIZE // 2,
        "steps": 16,
        "stopped_by": "length",
    }
    assert set(facts) == DiskFill.reports == PgFillDisk.reports
    after, used = state(fake, "db")
    assert used == SIZE // 2
    assert set(after) - set(before[0]) == {FILE} == set(primitive.side_effects(params).files)
    assert primitive.side_effects(params) == Effects(files=(FILE,))
    # The share is rounded up: a byte below it is not there yet.
    assert disk.share(1001, 50) == 501 and disk.share(1000, 50) == 500
    assert disk.share(SIZE, 100) == SIZE and disk.share(3, 1) == 1


def test_a_small_fill_takes_steps_of_four_mebibytes() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    within.base = SIZE // 2 - 10 * MIB
    assert DiskFill().apply(targets, fill(50))["steps"] == 3
    assert ran(fake) == [
        ("fallocate", f"--length={length * MIB}", "--", FILE) for length in (4, 8, 10)
    ]
    assert within.usage == SIZE // 2


@pytest.mark.parametrize(("driver", "error"), [("lvm", "ENOSPC"), ("btrfs", "EDQUOT")])
def test_the_fill_stops_at_the_first_error_of_a_full_disk_and_says_what_it_reached(
    driver: str, error: str
) -> None:
    fake, targets, _ = lab(driver)
    within = inside(fake, "db")
    primitive, params = DiskFill(), fill(100)
    primitive.ready(targets, params)

    def other(_: Any, asked: Any) -> Ended | None:
        # Something else takes room while the file grows: the disk is full before the target.
        if asked.argv[0] == "fallocate" and "/srv/other" not in within.nodes:
            within.write("/srv/other", b"x" * (300 * MIB))
        return None

    within.answer(other)
    facts = primitive.apply(targets, params)
    step = -(-(SIZE - IMAGE) // 16)
    assert facts == {
        "size_bytes": SIZE,
        "target_bytes": SIZE,
        "usage_bytes": IMAGE + 300 * MIB + 15 * step,
        "steps": 16,
        "stopped_by": error,
    }
    assert len(ran(fake)) == 16 and within.usage == facts["usage_bytes"]
    assert within.usage < SIZE


def test_what_the_fill_reports_holds_the_share_it_aimed_at_and_the_use_it_read() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")

    def uncounted(_: Any, asked: Any) -> Ended | None:
        # Room that was taken and that the use did not hold yet when the fill read it.
        if asked.argv[0] == "fallocate" and "/srv/other" not in within.nodes:
            within.write("/srv/other", b"x" * (300 * MIB))
        return None

    within.answer(uncounted)
    facts = DiskFill().apply(targets, fill(50))
    # The file has its whole length; that the use is past the share shows in the record.
    assert (facts["stopped_by"], facts["steps"]) == ("length", 16)
    assert (facts["target_bytes"], facts["usage_bytes"]) == (SIZE // 2, SIZE // 2 + 300 * MIB)
    assert within.usage == facts["usage_bytes"]


def test_a_virtual_machine_s_root_disk_is_one_the_measure_is_not_taken_on() -> None:
    fake, targets, _ = lab()
    fake.instance(PROJECT, INSTANCES["db"]).kind = "virtual-machine"
    below = PgFillDisk.parameters(
        {"node": "db", "path": f"/var/lib/postgresql/{MARK}", "percent": 50}
    )
    faults: list[tuple[Any, Any]] = [(DiskFill(), fill(50)), (PgFillDisk(), below)]
    for primitive, params in faults:
        for call in (primitive.check, primitive.ready):
            with pytest.raises(SetupRefused) as refusal:
                call(targets, params)
            refused(refusal.value, "not_a_container")
        with pytest.raises(FaultFailed) as failed:
            primitive.apply(targets, params)
        refused(failed.value, "not_a_container")
    for asked in (level(90), level(90, below=False)):
        assert DiskUsageBelow().run(targets, asked) == Verdict("error", "not_a_container")
    assert ran(fake) == [] and FILE not in inside(fake, "db").nodes
    # The same node as a container is measured.
    fake.instance(PROJECT, INSTANCES["db"]).kind = "container"
    assert DiskUsageBelow().run(targets, level(90)).outcome == "passed"


def test_a_command_that_fails_for_another_reason_is_no_stop_but_a_failed_fault() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    step = -(-(SIZE // 2 - IMAGE) // 16)
    within.on(
        ["fallocate", f"--length={step * 2}", "--", FILE],
        1,
        b"",
        f"fallocate: cannot open {FILE}: Permission denied\n".encode(),
    )
    with pytest.raises(FaultFailed) as failed:
        DiskFill().apply(targets, fill(50))
    refused(failed.value, "command_failed")
    assert (failed.value.program, failed.value.status) == ("fallocate", 1)
    assert len(ran(fake)) == 2


def test_check_refuses_a_disk_the_measure_cannot_be_taken_on() -> None:
    primitive, params = DiskFill(), fill(50)
    fake, targets, _ = lab()
    del fake.profiles[PROJECT, "target-container"].devices["root"]["size"]
    with pytest.raises(SetupRefused) as unsized:
        primitive.check(targets, params)
    refused(unsized.value, "root_disk_size_missing")
    fake, targets, _ = lab()
    fake.instance(PROJECT, INSTANCES["db"]).measured = False
    for call in (primitive.check, primitive.ready):
        with pytest.raises(SetupRefused) as unmeasured:
            call(targets, params)
        refused(unmeasured.value, "root_disk_usage_missing")
    with pytest.raises(FaultFailed) as blind:
        primitive.apply(targets, params)
    refused(blind.value, "root_disk_usage_missing")
    assert ran(fake) == [] and FILE not in inside(fake, "db").nodes


def test_check_refuses_a_path_that_is_not_on_the_root_disk() -> None:
    primitive = DiskFill()
    fake, targets, programs = lab()
    with pytest.raises(SetupRefused) as absent:
        primitive.check(targets, fill(50, f"/srv/{MARK}/file"))
    refused(absent.value, "file_not_found")
    inside(fake, "db").write(f"/srv/{MARK}", b"")
    with pytest.raises(SetupRefused) as no_directory:
        primitive.check(targets, fill(50, f"/srv/{MARK}/file"))
    refused(no_directory.value, "file_not_found")
    programs.mounts["/srv"] = "/srv"
    with pytest.raises(SetupRefused) as elsewhere:
        primitive.check(targets, fill(50))
    refused(elsewhere.value, "path_not_on_root_disk")
    assert ran(fake) == [(*FINDMNT, "--target=/srv")]


@pytest.mark.parametrize(
    "said", [b"", b"/", b"/\n/boot\n", b"\n", b"TARGET\n/\n", b"srv\n", b"/\n\n", b"\xff\n"]
)
def test_a_mount_said_in_another_form_is_refused_and_never_guessed(said: bytes) -> None:
    fake, targets, _ = lab()
    inside(fake, "db").on([*FINDMNT, "--target=/srv"], 0, said)
    with pytest.raises(SetupRefused) as unread:
        DiskFill().check(targets, fill(50))
    refused(unread.value, "unexpected_output")
    inside(fake, "db").on([*FINDMNT, "--target=/srv"], 1, b"", b"findmnt: failed\n")
    with pytest.raises(SetupRefused) as failed:
        DiskFill().check(targets, fill(50))
    refused(failed.value, "command_failed")


def test_check_asks_for_both_programs_before_anything_else() -> None:
    fake, targets, _ = lab(programs=("findmnt",))
    with pytest.raises(SetupRefused) as missing:
        DiskFill().check(targets, fill(50))
    refused(missing.value, "tool_missing")
    assert missing.value.program == "fallocate" and ran(fake) == []


def test_ready_refuses_a_file_that_is_there_and_a_disk_that_is_full_enough_already() -> None:
    primitive = DiskFill()
    fake, targets, _ = lab()
    within = inside(fake, "db")
    makers: list[Callable[[], None]] = [
        lambda: within.write(FILE, b""),
        lambda: within.mkdir(FILE),
        lambda: within.link(FILE, "/etc"),
    ]
    for made in makers:
        made()
        with pytest.raises(SetupRefused) as there:
            primitive.ready(targets, fill(50))
        refused(there.value, "file_exists")
        within.delete(FILE)
    within.base = SIZE // 2 - 1
    primitive.ready(targets, fill(50))
    within.base = SIZE // 2
    with pytest.raises(SetupRefused) as full:
        primitive.ready(targets, fill(50))
    refused(full.value, "already_in_that_state")
    with pytest.raises(FaultFailed) as late:
        primitive.apply(targets, fill(50))
    refused(late.value, "already_in_that_state")
    assert ran(fake) == [] and FILE not in within.nodes


@pytest.mark.parametrize(
    "wrong",
    [
        {"percent": 0},
        {"percent": 101},
        {"percent": "50"},
        {"percent": 50.0},
        {"percent": True},
        {"path": "/"},
        {"path": "srv/x"},
        {"path": "/proc/x"},
        {"mount": "/srv"},
    ],
)
def test_the_fill_s_parameters_are_a_path_and_a_percentage(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DiskFill.parameters({"node": "db", "path": FILE, "percent": 50, **wrong})


def test_the_database_s_fill_is_the_same_fault_below_its_own_directory() -> None:
    for path in ("/srv/x", "/var/lib/postgresql", "/var/lib/postgresql2/x", "/var/lib/x"):
        with pytest.raises(ValidationError):
            PgFillDisk.parameters({"node": "db", "path": path, "percent": 50})
    fake, targets, _ = lab()
    inside(fake, "db").mkdir("/var/lib/postgresql/16/main")
    path = "/var/lib/postgresql/16/main/pg_import.part"
    params = PgFillDisk.parameters({"node": "db", "path": path, "percent": 97})
    primitive = PgFillDisk()
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert primitive.apply(targets, params)["usage_bytes"] == disk.share(SIZE, 97)
    assert ran(fake)[0] == (*FINDMNT, "--target=/var/lib/postgresql/16/main")
    assert primitive.side_effects(params) == Effects(files=(path,))
    assert "/var/lib/postgresql/16/main/pg_import.part" in primitive.describe(params)


def test_the_same_parameters_give_the_same_commands() -> None:
    def commands() -> list[tuple[str, ...]]:
        fake, targets, _ = lab()
        primitive, params = DiskFill(), fill(73)
        primitive.check(targets, params)
        primitive.ready(targets, params)
        primitive.apply(targets, params)
        return ran(fake)

    assert commands() == commands()


def test_a_failure_of_incus_is_no_refusal() -> None:
    fake, targets, _ = lab()
    record = f"/1.0/instances/{INSTANCES['db']}"
    with (
        fake.refusing("GET", f"{record}/state", 500, MARK),
        pytest.raises(TargetFailure) as failed,
    ):
        DiskFill().check(targets, fill(50))
    assert (str(failed.value), failed.value.alias) == ("incus_error", "db")
    with fake.refusing("POST", f"{record}/exec", 500, MARK), pytest.raises(TargetFailure):
        DiskFill().apply(targets, fill(50))


def level(percent: int, below: bool | None = None, node: str = "db") -> disk.UsageParams:
    given: dict[str, Any] = {"node": node, "percent": percent}
    return DiskUsageBelow.parameters(given if below is None else {**given, "below": below})


def test_the_probe_of_a_disk_s_use_reads_incus_and_runs_nothing() -> None:
    fake, targets, _ = lab()
    within = inside(fake, "db")
    probe = DiskUsageBelow()
    facts = {"size_bytes": SIZE, "usage_bytes": IMAGE}
    before = state(fake, "db")
    assert probe.run(targets, level(80)) == Verdict("passed", None, facts)
    assert probe.run(targets, level(80, below=False)) == Verdict("failed", None, facts)
    assert probe.run(targets, level(10)) == Verdict("failed", None, facts)
    # At the share itself the use is not below it.
    within.base = SIZE // 2
    assert probe.run(targets, level(50)).outcome == "failed"
    assert probe.run(targets, level(50, below=False)).outcome == "passed"
    within.base = SIZE // 2 - 1
    assert probe.run(targets, level(50)).outcome == "passed"
    assert ran(fake) == []
    within.base = IMAGE
    assert state(fake, "db") == before


def test_the_probe_and_the_fill_measure_alike() -> None:
    for percent in (37, 50, 96, 100):
        _, targets, _ = lab()
        DiskFill().apply(targets, fill(percent))
        probe = DiskUsageBelow()
        assert probe.run(targets, level(percent, below=False)).outcome == "passed"
        assert probe.run(targets, level(percent)).outcome == "failed"
        if percent < 100:
            assert probe.run(targets, level(percent + 1)).outcome == "passed"


def test_a_measure_that_is_missing_is_an_error_and_never_a_number() -> None:
    probe = DiskUsageBelow()
    fake, targets, _ = lab()
    fake.instance(PROJECT, INSTANCES["db"]).measured = False
    assert probe.run(targets, level(90)) == Verdict("error", "root_disk_usage_missing")
    assert probe.run(targets, level(90, below=False)) == Verdict("error", "root_disk_usage_missing")
    fake, targets, _ = lab()
    fake.profiles[PROJECT, "target-container"].devices["root"]["size"] = "0"
    assert probe.run(targets, level(90)) == Verdict("error", "root_disk_size_missing")
    del fake.profiles[PROJECT, "target-container"].devices["root"]["size"]
    assert probe.run(targets, level(90)) == Verdict("error", "root_disk_size_missing")
    with pytest.raises(ValidationError):
        DiskUsageBelow.parameters({"node": "db", "percent": 90, "mount": "/srv"})
    with pytest.raises(ValidationError):
        DiskUsageBelow.parameters({"node": "db", "percent": 90, "below": "false"})


def files(percent: int, path: str = f"/srv/{MARK}", prefix: str = "spool") -> disk.InodeParams:
    return InodeExhaust.parameters(
        {"node": "db", "path": path, "prefix": prefix, "percent": percent}
    )


STAT = ("stat", "--file-system", "--format=%c:%d:%T", "--", "/srv")


def test_empty_files_are_made_until_the_share_of_the_inodes_is_in_use() -> None:
    fake, targets, programs = lab(inodes=10_000, taken=0)
    within = inside(fake, "db")
    programs.taken = 1000 - len(within.nodes)
    primitive, params = InodeExhaust(), files(50)
    before = state(fake, "db")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert ran(fake) == [(*FINDMNT, "--target=/srv"), STAT, STAT]
    assert state(fake, "db") == before
    facts = primitive.apply(targets, params)
    assert facts == {"inodes_total": 10_000, "inodes_used": 5000, "stopped_by": "count"}
    assert set(facts) == InodeExhaust.reports
    commands = inside(fake, "db").executions[3:]
    assert [asked.argv[:2] for asked in commands] == [
        STAT[:2],
        ("mkdir", "--"),
        ("touch", "--"),
        ("touch", "--"),
        STAT[:2],
    ]
    assert commands[1].argv == ("mkdir", "--", f"/srv/{MARK}")
    first, second = commands[2], commands[3]
    assert (first.cwd, second.cwd) == (f"/srv/{MARK}", f"/srv/{MARK}")
    assert first.argv[2:] == tuple(f"spool{number:07d}" for number in range(1, 2001))
    assert second.argv[2:] == tuple(f"spool{number:07d}" for number in range(2001, 4000))
    # The directory and 3,999 files: 4,000 inodes on the 1,000 that were in use.
    made = set(within.nodes) - set(before[0])
    assert len(made) == 4000 and f"/srv/{MARK}" in made
    assert all(path == f"/srv/{MARK}" or path.startswith(f"/srv/{MARK}/spool") for path in made)
    assert primitive.side_effects(params) == Effects(files=(f"/srv/{MARK}",))


def test_the_files_stop_at_the_first_error_of_a_full_file_system() -> None:
    error = "ENOSPC"
    fake, targets, programs = lab(inodes=10_000, taken=0)
    programs.taken = 9000 - len(inside(fake, "db").nodes)
    primitive, params = InodeExhaust(), files(100)
    primitive.ready(targets, params)

    def other(_: Any, asked: Any) -> Ended | None:
        # Something else takes inodes while the files are made.
        if asked.argv[0] == "mkdir":
            programs.taken += 500
        return None

    inside(fake, "db").answer(other)
    facts = primitive.apply(targets, params)
    assert facts == {"inodes_total": 10_000, "inodes_used": 10_000, "stopped_by": error}
    touched = [asked for asked in inside(fake, "db").executions if asked.argv[0] == "touch"]
    assert len(touched) == 1 and len(touched[0].argv) == 2 + 999


def test_the_files_stop_at_a_quota_s_error_as_well() -> None:
    fake, targets, programs = lab(inodes=10_000, taken=0)
    within = inside(fake, "db")
    programs.taken = 1000 - len(within.nodes)

    def quota(_: Any, asked: Any) -> Ended | None:
        # The second command's files are more than a quota on the file system allows.
        if asked.argv[0] == "touch" and asked.argv[2] == "spool0002001":
            return output("touch-edquot").ended()
        return None

    within.answer(quota)
    facts = InodeExhaust().apply(targets, files(50))
    # The directory and the first command's 2,000 files, on the 1,000 that were in use.
    assert facts == {"inodes_total": 10_000, "inodes_used": 3001, "stopped_by": "EDQUOT"}
    touched = [asked for asked in within.executions if asked.argv[0] == "touch"]
    assert len(touched) == 2 and InodeExhaust.words == {"count", "ENOSPC", "EDQUOT"}


def test_a_file_system_that_makes_inodes_as_it_needs_them_is_refused() -> None:
    primitive, params = InodeExhaust(), files(50)
    fake, targets, _ = lab("btrfs")
    with pytest.raises(SetupRefused) as btrfs:
        primitive.check(targets, params)
    refused(btrfs.value, "inode_count_not_fixed")
    for said in (b"1000:900:xfs\n", b"0:0:ext2/ext3\n", b"1000:900:tmpfs\n", b"1000:900:zfs\n"):
        fake, targets, _ = lab()
        inside(fake, "db").on(list(STAT), 0, said)
        with pytest.raises(SetupRefused) as other:
            primitive.check(targets, params)
        refused(other.value, "inode_count_not_fixed")
        assert f"/srv/{MARK}" not in inside(fake, "db").nodes


@pytest.mark.parametrize(
    "said",
    [
        b"",
        b"524288:402113:ext2/ext3",
        b"524288:402113\n",
        b"524288:402113:ext2/ext3:more\n",
        b"524288:402113:ext2/ext3\nmore\n",
        b"524288 402113 ext2/ext3\n",
        b"-1:402113:ext2/ext3\n",
        b"0x10:402113:ext2/ext3\n",
        b"100:101:ext2/ext3\n",
        b"524288:402113:EXT2/EXT3\n",
        b"\xff:1:ext2/ext3\n",
    ],
)
def test_an_inode_count_in_another_form_is_refused_and_never_guessed(said: bytes) -> None:
    fake, targets, _ = lab()
    inside(fake, "db").on(list(STAT), 0, said)
    with pytest.raises(SetupRefused) as unread:
        InodeExhaust().check(targets, files(50))
    refused(unread.value, "unexpected_output")
    probe = InodeUsageBelow()
    asked = InodeUsageBelow.parameters({"node": "db", "path": "/srv", "percent": 50})
    assert probe.run(targets, asked) == Verdict("error", "unexpected_output")


def test_the_inode_fault_s_other_refusals() -> None:
    primitive = InodeExhaust()
    fake, targets, programs = lab(inodes=10_000_000, taken=100)
    within = inside(fake, "db")
    with pytest.raises(SetupRefused) as absent:
        primitive.check(targets, files(50, f"/srv/{MARK}/deeper"))
    refused(absent.value, "file_not_found")
    with pytest.raises(SetupRefused) as many:
        primitive.ready(targets, files(50))
    refused(many.value, "too_many_inodes")
    primitive.ready(targets, files(10))
    programs.taken = 9_999_900
    with pytest.raises(SetupRefused) as full:
        primitive.ready(targets, files(50))
    refused(full.value, "already_in_that_state")
    with pytest.raises(FaultFailed) as late:
        primitive.apply(targets, files(50))
    refused(late.value, "already_in_that_state")
    programs.taken = 100
    within.mkdir(f"/srv/{MARK}")
    with pytest.raises(SetupRefused) as there:
        primitive.ready(targets, files(10))
    refused(there.value, "file_exists")
    with pytest.raises(FaultFailed) as unmade:
        primitive.apply(targets, files(10))
    refused(unmade.value, "command_failed")
    assert unmade.value.program == "mkdir"
    programs.mounts["/srv"] = "/srv"
    with pytest.raises(SetupRefused) as elsewhere:
        primitive.check(targets, files(10))
    refused(elsewhere.value, "path_not_on_root_disk")
    assert not [asked for asked in within.executions if asked.argv[0] == "touch"]


def test_a_touch_that_fails_for_another_reason_is_a_failed_fault() -> None:
    fake, targets, programs = lab(inodes=10_000, taken=0)
    within = inside(fake, "db")
    programs.taken = 9000 - len(within.nodes)

    def denied(_: Any, asked: Any) -> Ended | None:
        if asked.argv[0] != "touch":
            return None
        return Ended(1, b"", f"touch: cannot touch '{MARK}': Permission denied\n".encode())

    within.answer(denied)
    with pytest.raises(FaultFailed) as failed:
        InodeExhaust().apply(targets, files(100))
    refused(failed.value, "command_failed")
    assert (failed.value.program, failed.value.status) == ("touch", 1)


@pytest.mark.parametrize(
    "wrong",
    [
        {"prefix": ""},
        {"prefix": "-rf"},
        {"prefix": "a/b"},
        {"prefix": "Spool"},
        {"prefix": "a" * 17},
        {"prefix": "a b"},
        {"prefix": "../x"},
        {"percent": 0},
        {"path": "/"},
    ],
)
def test_the_inode_fault_s_names_cannot_be_read_as_anything_else(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InodeExhaust.parameters(
            {"node": "db", "path": "/srv/x", "prefix": "spool", "percent": 50, **wrong}
        )


def test_the_probe_of_inodes_measures_inside_the_target_and_changes_nothing() -> None:
    fake, targets, programs = lab(inodes=10_000, taken=0)
    within = inside(fake, "db")
    programs.taken = 5000 - len(within.nodes)
    probe = InodeUsageBelow()

    def asked(percent: int, **more: Any) -> Any:
        return InodeUsageBelow.parameters({"node": "db", "percent": percent, **more})

    facts = {"inodes_total": 10_000, "inodes_used": 5000}
    before = state(fake, "db")
    assert probe.run(targets, asked(51)) == Verdict("passed", None, facts)
    assert probe.run(targets, asked(50)) == Verdict("failed", None, facts)
    assert probe.run(targets, asked(50, below=False)) == Verdict("passed", None, facts)
    assert probe.run(targets, asked(51, path="/srv")).outcome == "passed"
    assert ran(fake) == [(*STAT[:4], "/")] * 3 + [STAT]
    assert state(fake, "db") == before
    assert asked(50).path == "/"
    for path in ("relative", "/proc", "//", ""):
        with pytest.raises(ValidationError):
            asked(50, path=path)


def test_the_probe_of_inodes_cannot_measure_where_the_count_is_not_fixed() -> None:
    probe = InodeUsageBelow()
    asked = InodeUsageBelow.parameters({"node": "db", "percent": 90})
    fake, targets, _ = lab("btrfs")
    assert probe.run(targets, asked) == Verdict("error", "inode_count_not_fixed")
    fake, targets, _ = lab()
    inside(fake, "db").on(["stat", "--file-system", "--format=%c:%d:%T", "--", "/"], 127)
    assert probe.run(targets, asked) == Verdict("error", "tool_missing")
    inside(fake, "db").on(["stat", "--file-system", "--format=%c:%d:%T", "--", "/"], 1)
    assert probe.run(targets, asked) == Verdict("error", "command_failed")
    exec_path = f"/1.0/instances/{INSTANCES['db']}/exec"
    with fake.refusing("POST", exec_path, 500, MARK), pytest.raises(TargetFailure):
        probe.run(targets, asked)


def test_what_a_step_says_of_itself_is_one_sentence() -> None:
    said = [
        DiskFill().describe(fill(50)),
        InodeExhaust().describe(files(50)),
        DiskUsageBelow().describe(level(50)),
        DiskUsageBelow().describe(level(50, below=False)),
        InodeUsageBelow().describe(InodeUsageBelow.parameters({"node": "db", "percent": 5})),
    ]
    for sentence in said:
        assert sentence.endswith(".") and sentence.count(". ") == 0 and "db" in sentence
    assert "at or above" in said[3] and "below" in said[2]


def test_the_fixture_scenario_s_disk_steps_fit_these_models(tmp_path: Path) -> None:
    catalog = copy_fixture(tmp_path / "catalog")
    name = "scenarios/FIXTURE-002/1/scenario.yaml"

    def found() -> list[tuple[str, str, str]]:
        findings = lint(catalog, TOOLS, library_views())
        return [(finding.path, finding.rule, finding.message) for finding in findings]

    assert found() == []

    def mounted(data: Any) -> None:
        data["inject"][0]["params"]["mount"] = "/srv"
        data["confirm"][0]["params"]["percent"] = 101

    edit_yaml(catalog, name, mounted)
    assert found() == [
        (
            name,
            "params",
            "confirm.0: disk_usage_below: percent: is not within the parameter's bounds",
        ),
        (name, "params", "inject.0: disk.fill: mount: is no parameter of it"),
    ]
