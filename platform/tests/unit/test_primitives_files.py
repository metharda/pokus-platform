"""The faults that write a file, edit one, or change its mode and owner."""

from __future__ import annotations

from typing import Any

import pytest
from potka_incus.fake import FakeIncus
from potka_primitives import files, library
from potka_primitives.base import Effects, Requires
from potka_primitives.context import Targets
from potka_primitives.errors import FaultFailed, Refusal, SetupRefused
from potka_primitives.files import FileMutate, FileWrite, PermissionsBreak, edited
from potka_primitives.values import below
from potka_testing.outputs import output
from potka_testing.target_units import Files
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

MARK = "zq-marked"
PATH = f"/etc/{MARK}.conf"
STAT = ("stat", "--format=%a:%u:%g:%F", "--")


def lab(*programs: str) -> tuple[FakeIncus, Targets]:
    fake, targets = run(programs=programs or ("chmod", "chown", "stat"))
    Files(inside(fake, "web"))
    return fake, targets


def refused(error: Any, reason: str) -> None:
    assert (str(error), error.alias) == (reason, "web")
    assert error.__context__ is None and error.__cause__ is None
    assert MARK not in f"{error!r} {vars(error)}" and INSTANCES["web"] not in repr(error)


def ran(fake: FakeIncus) -> list[tuple[str, ...]]:
    return [asked.argv for asked in inside(fake, "web").executions]


def write(**more: Any) -> Any:
    return FileWrite.parameters({"node": "web", "path": PATH, "content": f"a = {MARK}\n", **more})


def test_the_library_has_the_three_file_faults() -> None:
    assert {"file.write", "file.mutate", "permissions.break"} <= set(library.PRIMITIVES)
    assert FileWrite.requires == Requires((files.CHMOD, files.CHOWN))
    assert FileMutate.requires == Requires()
    assert PermissionsBreak.requires == Requires((files.STAT, files.CHMOD, files.CHOWN))
    assert {files.CHMOD.package, files.CHOWN.package, files.STAT.package} == {"coreutils"}


def test_a_new_file_is_written_with_its_owner_and_mode_by_the_files_call() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive, params = FileWrite(), write(mode="640", uid=33, gid=34)
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held
    assert primitive.apply(targets, params) == {"bytes": len(f"a = {MARK}\n")}
    made = within.nodes[PATH]
    assert (made.content, made.uid, made.gid, made.mode) == (
        f"a = {MARK}\n".encode(),
        33,
        34,
        0o640,
    )
    assert ran(fake) == [] and set(within.nodes) - set(held[0]) == {PATH}
    assert primitive.side_effects(params) == Effects(files=(PATH,))
    # Without them a new file is root's and 644.
    fake, targets = lab()
    FileWrite().apply(targets, write())
    made = inside(fake, "web").nodes[PATH]
    assert (made.uid, made.gid, made.mode) == (0, 0, 0o644)


def test_a_file_that_exists_gets_the_content_and_then_what_was_asked_of_owner_and_mode() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, b"old\n", uid=7, gid=8, mode=0o600)
    FileWrite().apply(targets, write())
    kept = within.nodes[PATH]
    assert (kept.content, kept.uid, kept.gid, kept.mode) == (f"a = {MARK}\n".encode(), 7, 8, 0o600)
    assert ran(fake) == []
    FileWrite().apply(targets, write(content="new\n", mode="644", gid=34))
    assert (kept.content, kept.uid, kept.gid, kept.mode) == (b"new\n", 7, 34, 0o644)
    assert ran(fake) == [
        ("chown", "--no-dereference", "--", ":+34", PATH),
        ("chmod", "--", "644", PATH),
    ]
    # A number with its plus is never looked up as a name.
    assert files.owner(33, None) == "+33" and files.owner(33, 34) == "+33:+34"
    assert files.owner(None, 34) == ":+34" and files.owner(0, 0) == "+0:+0"


def test_a_write_whose_owner_cannot_be_set_is_a_failed_fault_and_what_it_wrote_stays() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, b"old\n", uid=7, gid=8, mode=0o600)
    within.on(["chown", "--no-dereference", "--", "+33", PATH], 1, b"", b"chown: no\n")
    with pytest.raises(FaultFailed) as failed:
        FileWrite().apply(targets, write(uid=33, mode="644"))
    refused(failed.value, "command_failed")
    assert (failed.value.program, failed.value.status) == ("chown", 1)
    kept = within.nodes[PATH]
    assert (kept.content, kept.uid, kept.mode) == (f"a = {MARK}\n".encode(), 7, 0o600)
    assert ran(fake) == [("chown", "--no-dereference", "--", "+33", PATH)]


def test_a_write_that_changes_nothing_or_has_no_place_is_refused() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive = FileWrite()
    within.write(PATH, f"a = {MARK}\n".encode(), uid=7, gid=8, mode=0o600)
    for same in (write(), write(mode="600"), write(uid=7, gid=8), write(mode="600", uid=7)):
        with pytest.raises(SetupRefused) as there:
            primitive.ready(targets, same)
        refused(there.value, "already_in_that_state")
    for other in (write(mode="640"), write(uid=0), write(gid=0), write(content="b\n")):
        primitive.ready(targets, other)
    within.delete(PATH)
    within.mkdir(PATH)
    with pytest.raises(SetupRefused) as directory:
        primitive.ready(targets, write())
    refused(directory.value, "not_a_regular_file")
    within.delete(PATH)
    for target in ("/etc/nothing-there", "/etc"):
        within.link(PATH, target)
        with pytest.raises(SetupRefused) as link:
            primitive.ready(targets, write())
        refused(link.value, "not_a_regular_file")
        within.delete(PATH)
    with pytest.raises(SetupRefused) as nowhere:
        primitive.ready(targets, write(path=f"/etc/{MARK}/deeper/file"))
    refused(nowhere.value, "file_not_found")
    assert ran(fake) == []


def test_a_file_in_a_directory_that_is_a_link_is_not_written_through_the_link() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.mkdir("/srv/real")
    within.link(f"/etc/{MARK}", "/srv/real")
    within.link(f"/etc/{MARK}-gone", "/srv/nothing-there")
    primitive = FileWrite()
    for directory in (f"/etc/{MARK}", f"/etc/{MARK}-gone"):
        params = write(path=f"{directory}/file")
        with pytest.raises(SetupRefused) as linked:
            primitive.ready(targets, params)
        refused(linked.value, "not_a_regular_file")
        with pytest.raises(FaultFailed) as late:
            primitive.apply(targets, params)
        refused(late.value, "not_a_regular_file")
    assert "/srv/real/file" not in within.nodes
    # Named by where it leads, the same file is written.
    primitive.ready(targets, write(path="/srv/real/file"))
    primitive.apply(targets, write(path="/srv/real/file"))
    assert "/srv/real/file" in within.nodes


@pytest.mark.parametrize("mode", [0o755, 0o744, 0o610, 0o601, 0o100])
def test_a_file_that_can_be_run_is_not_rewritten(mode: int) -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, b"a = 1\n", mode=mode)
    edit = mutate({"edit": "set_key", "key": "a", "value": "2"})
    for primitive, params in ((FileWrite(), write(mode="644")), (FileMutate(), edit)):
        for call, raised in ((primitive.ready, SetupRefused), (primitive.apply, FaultFailed)):
            with pytest.raises(raised) as runs:
                call(targets, params)
            refused(runs.value, "file_is_executable")
    kept = within.nodes[PATH]
    assert (kept.content, kept.mode) == (b"a = 1\n", mode) and ran(fake) == []
    # Without the bit the same file is written.
    kept.mode = mode & 0o666
    FileWrite().apply(targets, write())
    assert kept.content == f"a = {MARK}\n".encode()


@pytest.mark.parametrize(
    "path",
    [
        "/etc/cron.d/zq",
        "/etc/crontab",
        "/etc/cron.daily/zq",
        "/var/spool/cron/crontabs/root",
        "/etc/systemd/system/nginx.service.d/zq.conf",
        "/etc/systemd/system/zq.service",
        "/usr/lib/systemd/system/zq.service",
        "/lib/systemd/system/zq.timer",
        "/run/systemd/system/zq.service",
        "/etc/init.d/zq",
        "/etc/profile",
        "/etc/profile.d/zq.sh",
        "/etc/rc.local",
        "/etc/sudoers",
        "/etc/sudoers.d/zq",
        "/etc/ld.so.preload",
        "/etc/logrotate.d/zq",
        "/etc/udev/rules.d/zq.rules",
    ],
)
def test_no_file_is_written_where_files_are_run_by_themselves(path: str) -> None:
    assert any(below(path, place) for place in files.COMMAND_PLACES)
    with pytest.raises(ValidationError) as written:
        write(path=path)
    assert "zq" not in str(written.value)
    with pytest.raises(ValidationError):
        mutate({"edit": "delete_line", "line": "a"}, path=path)
    # The mode and the owner of such a file are another fault's, which writes no content.
    assert perms(path=path, mode="600").path == path


def test_the_places_where_files_are_run_are_a_list_of_whole_names() -> None:
    assert sorted(files.COMMAND_PLACES) == list(files.COMMAND_PLACES)
    assert len(set(files.COMMAND_PLACES)) == len(files.COMMAND_PLACES) == 21
    for path in ("/etc/cron.deny", "/etc/crontab.old", "/etc/profiles", "/etc/systemd-x/a"):
        assert write(path=path).path == path
        assert mutate({"edit": "delete_line", "line": "a"}, path=path).path == path


@pytest.mark.parametrize(
    "wrong",
    [
        {"mode": "755"},
        {"mode": "641"},
        {"mode": "610"},
        {"mode": "0644"},
        {"mode": "64"},
        {"mode": 644},
        {"mode": "u+x"},
        {"uid": -1},
        {"uid": 2**31},
        {"gid": "0"},
        {"content": "a\0b"},
        {"content": "\ud800"},
        {"content": "a" * 65537},
        {"content": 5},
        {"path": "/proc/sys/kernel/x"},
        {"owner": "root"},
    ],
)
def test_a_write_places_no_program_and_nothing_outside_its_forms(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as refusal:
        write(**wrong)
    assert MARK not in str(refusal.value)


def mutate(*edits: dict[str, Any], path: str = PATH) -> Any:
    return FileMutate.parameters({"node": "web", "path": path, "edits": list(edits)})


SETTINGS = "# batch = 10\nbatch = 100\nparallel=1\n; old = 1\nname = a = b\n[next]\nmode=\n"


def test_a_setting_s_value_is_replaced_in_its_own_line() -> None:
    def set_key(key: str, value: str, text: str = SETTINGS) -> str:
        return edited(text, mutate({"edit": "set_key", "key": key, "value": value}).edits)

    assert set_key("batch", "5") == SETTINGS.replace("\nbatch = 100\n", "\nbatch = 5\n")
    assert set_key("parallel", "64") == SETTINGS.replace("parallel=1\n", "parallel=64\n")
    assert set_key("name", "c") == SETTINGS.replace("name = a = b\n", "name = c\n")
    # Where no blank stood after the sign, none is put: a shell would read one as a word's end.
    assert set_key("mode", "x y") == SETTINGS.replace("mode=\n", "mode=x y\n")
    assert set_key("OPTS", "--verbose", "OPTS=\n") == "OPTS=--verbose\n"
    assert set_key("OPTS", "b", "OPTS=a\n") == "OPTS=b\n"
    # The line's own end, its start and the blanks after the sign are kept.
    assert set_key("a", "2", "  a  =  1\r\nb = 1") == "  a  =  2\r\nb = 1"
    assert set_key("b", "2", "  a  =  1\r\nb = 1") == "  a  =  1\r\nb = 2"
    assert set_key("a", "2", "a=\t1\n") == "a=\t2\n" and set_key("a", "2", "a= \n") == "a= 2\n"
    for key in ("old", "missing", "next", "bat", "name.a"):
        with pytest.raises(Refusal, match="anchor_not_found"):
            set_key(key, "1")
    with pytest.raises(Refusal, match="anchor_not_unique"):
        set_key("a", "1", "a = 1\na=2\n")


def test_lines_are_replaced_inserted_after_and_deleted_by_their_whole_text() -> None:
    text = "one\ntwo\r\n  two\nthree"
    assert edited(
        text, mutate({"edit": "replace_line", "line": "two", "replacement": "2"}).edits
    ) == ("one\n2\r\n  two\nthree")
    assert edited(text, mutate({"edit": "insert_after", "line": "one", "text": "1.5"}).edits) == (
        "one\n1.5\ntwo\r\n  two\nthree"
    )
    assert edited(text, mutate({"edit": "insert_after", "line": "two", "text": "x"}).edits) == (
        "one\ntwo\r\nx\r\n  two\nthree"
    )
    # After the last line, which has no end, both lines get one.
    assert edited(text, mutate({"edit": "insert_after", "line": "three", "text": "4"}).edits) == (
        "one\ntwo\r\n  two\nthree\n4\n"
    )
    assert edited(text, mutate({"edit": "delete_line", "line": "  two"}).edits) == (
        "one\ntwo\r\nthree"
    )
    # Each edit is made on what the one before it left.
    both = mutate(
        {"edit": "replace_line", "line": "one", "replacement": "uno"},
        {"edit": "delete_line", "line": "uno"},
    )
    assert edited(text, both.edits) == "two\r\n  two\nthree"
    for line in ("tw", "two ", "", "four"):
        with pytest.raises(Refusal, match="anchor_not_found"):
            edited(text, mutate({"edit": "delete_line", "line": line}).edits)
    with pytest.raises(Refusal, match="anchor_not_unique"):
        edited("a\na\n", mutate({"edit": "delete_line", "line": "a"}).edits)


def test_a_line_ends_at_a_line_feed_and_nowhere_else() -> None:
    text = "key = 1\fnext\none\x85two\u2028three\rfour\n\nlast"
    assert files.lines_of(text) == [
        "key = 1\fnext\n",
        "one\x85two\u2028three\rfour\n",
        "\n",
        "last",
    ]
    assert "".join(files.lines_of(text)) == text and files.lines_of("") == []
    # What another reader would take for a line's end stays where it is.
    added = mutate({"edit": "insert_after", "line": "key = 1\fnext", "text": "x"})
    assert edited(text, added.edits) == text.replace("next\n", "next\nx\n")
    gone = mutate({"edit": "delete_line", "line": "one\x85two\u2028three"})
    assert edited("a\none\x85two\u2028three\nb\n", gone.edits) == "a\nb\n"
    # A carriage return alone ends no line, and no edit names a line by one.
    with pytest.raises(ValidationError):
        mutate({"edit": "delete_line", "line": "three\rfour"})
    with pytest.raises(Refusal, match="anchor_not_found"):
        edited(text, mutate({"edit": "delete_line", "line": "key = 1"}).edits)


def test_a_file_is_edited_in_place_and_keeps_its_owner_and_mode() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, SETTINGS.encode(), uid=7, gid=8, mode=0o640)
    primitive = FileMutate()
    params = mutate({"edit": "set_key", "key": "batch", "value": MARK})
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held
    result = SETTINGS.replace("\nbatch = 100\n", f"\nbatch = {MARK}\n").encode()
    assert primitive.apply(targets, params) == {"bytes": len(result), "edits": 1}
    kept = within.nodes[PATH]
    assert (kept.content, kept.uid, kept.gid, kept.mode) == (result, 7, 8, 0o640)
    assert ran(fake) == [] and set(within.nodes) == set(held[0])
    assert primitive.side_effects(params) == Effects(files=(PATH,))
    # The same edit again changes nothing: the fault is in place.
    for call, raised in ((primitive.ready, SetupRefused), (primitive.apply, FaultFailed)):
        with pytest.raises(raised) as there:
            call(targets, params)
        refused(there.value, "already_in_that_state")


def test_an_edit_that_finds_no_line_or_no_text_file_is_refused() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive = FileMutate()
    params = mutate({"edit": "delete_line", "line": MARK})
    with pytest.raises(SetupRefused) as absent:
        primitive.ready(targets, params)
    refused(absent.value, "file_not_found")
    within.write(PATH, b"a\n")
    with pytest.raises(SetupRefused) as unanchored:
        primitive.ready(targets, params)
    refused(unanchored.value, "anchor_not_found")
    with pytest.raises(FaultFailed) as late:
        primitive.apply(targets, params)
    refused(late.value, "anchor_not_found")
    within.write(PATH, f"{MARK}\n{MARK}\n".encode())
    with pytest.raises(SetupRefused) as twice:
        primitive.ready(targets, params)
    refused(twice.value, "anchor_not_unique")
    within.write(PATH, b"\xff\xfe binary")
    with pytest.raises(SetupRefused) as binary:
        primitive.ready(targets, params)
    refused(binary.value, "unexpected_output")
    within.delete(PATH)
    within.write("/etc/other", b"a\n")
    for target in ("/etc/other", "/etc/nothing-there", "/etc"):
        within.link(PATH, target)
        for call, raised in ((primitive.ready, SetupRefused), (primitive.apply, FaultFailed)):
            with pytest.raises(raised) as link:
                call(targets, params)
            refused(link.value, "not_a_regular_file")
        within.delete(PATH)
    assert within.nodes["/etc/other"].content == b"a\n" and "/etc/nothing-there" not in within.nodes


@pytest.mark.parametrize(
    "edits",
    [
        [],
        [{"edit": "delete_line", "line": "a"}] * 17,
        [{"edit": "append", "line": "a"}],
        [{"edit": "delete_line"}],
        [{"edit": "delete_line", "line": "a\nb"}],
        [{"edit": "delete_line", "line": "a", "regex": True}],
        [{"edit": "set_key", "key": "a b", "value": "1"}],
        [{"edit": "set_key", "key": "", "value": "1"}],
        [{"edit": "set_key", "key": "a", "value": "1\r"}],
        [{"edit": "replace_line", "line": "a"}],
        [{"edit": "insert_after", "line": "a", "text": "b\0"}],
        [{"line": "a"}],
        ["delete a"],
    ],
)
def test_the_edits_are_a_closed_list_of_whole_lines(edits: list[Any]) -> None:
    with pytest.raises(ValidationError):
        FileMutate.parameters({"node": "web", "path": PATH, "edits": edits})


def perms(**more: Any) -> Any:
    return PermissionsBreak.parameters({"node": "web", "path": PATH, **more})


def test_a_file_s_mode_and_owner_are_changed_and_never_a_link_s_end() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, b"x", uid=33, gid=33, mode=0o640)
    primitive, params = PermissionsBreak(), perms(mode="600", uid=0)
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held and ran(fake) == [(*STAT, PATH)]
    assert primitive.apply(targets, params) == {}
    kept = within.nodes[PATH]
    assert (kept.uid, kept.gid, kept.mode, kept.content) == (0, 33, 0o600, b"x")
    assert ran(fake)[1:] == [
        ("chown", "--no-dereference", "--", "+0", PATH),
        ("chmod", "--", "600", PATH),
    ]
    assert primitive.side_effects(params) == Effects(files=(PATH,))
    # A directory is changed the same way.
    within.mkdir(f"/etc/{MARK}", mode=0o755)
    PermissionsBreak().ready(targets, perms(path=f"/etc/{MARK}", mode="700"))
    PermissionsBreak().apply(targets, perms(path=f"/etc/{MARK}", mode="700"))
    assert within.nodes[f"/etc/{MARK}"].mode == 0o700


def test_the_kept_outputs_are_what_the_stand_in_s_stat_says() -> None:
    fake, targets = lab()
    within, node = inside(fake, "web"), targets.node("web")
    within.write("/etc/file", b"x", mode=0o644)
    within.mkdir("/etc/directory")
    within.link("/etc/link", "file")
    for name, path in (
        ("stat-file-regular", "file"),
        ("stat-file-directory", "directory"),
        ("stat-file-link", "link"),
    ):
        assert node.run([*STAT, f"/etc/{path}"]).stdout == output(name).content


def test_a_mode_that_adds_an_execute_bit_or_changes_nothing_is_refused() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    within.write(PATH, b"x", uid=33, gid=34, mode=0o640)
    primitive = PermissionsBreak()
    for mode in ("750", "641", "700", "777"):
        with pytest.raises(SetupRefused) as adds:
            primitive.ready(targets, perms(mode=mode))
        refused(adds.value, "adds_execute_bit")
    within.nodes[PATH].mode = 0o750
    for mode in ("700", "640", "000", "710"):
        primitive.ready(targets, perms(mode=mode))
    with pytest.raises(SetupRefused) as one_more:
        primitive.ready(targets, perms(mode="751"))
    refused(one_more.value, "adds_execute_bit")
    for same in (
        perms(mode="750"),
        perms(uid=33),
        perms(gid=34),
        perms(mode="750", uid=33, gid=34),
    ):
        with pytest.raises(SetupRefused) as there:
            primitive.ready(targets, same)
        refused(there.value, "already_in_that_state")
    assert within.nodes[PATH].mode == 0o750


def test_a_mode_is_read_as_stat_prints_it_and_compared_by_its_nine_bits() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive = PermissionsBreak()
    # stat prints no leading zeros: 0 for no bit at all, 44 for what only others may read.
    for mode, said in ((0, b"0"), (0o44, b"44"), (0o4, b"4"), (0o600, b"600")):
        within.write(PATH, b"x", mode=mode)
        assert targets.node("web").run([*STAT, PATH]).stdout.startswith(said + b":")
        primitive.ready(targets, perms(uid=5))
        with pytest.raises(SetupRefused) as same:
            primitive.ready(targets, perms(mode=f"{mode:03o}"))
        refused(same.value, "already_in_that_state")
        within.delete(PATH)
    # A mode of three digits says nothing of the bits above the nine, and chmod leaves a
    # directory's as they are: such a change would change nothing.
    within.mkdir(f"/etc/{MARK}", mode=0o2775)
    with pytest.raises(SetupRefused) as kept:
        primitive.ready(targets, perms(path=f"/etc/{MARK}", mode="775"))
    refused(kept.value, "already_in_that_state")
    primitive.ready(targets, perms(path=f"/etc/{MARK}", mode="770"))
    within.write(PATH, b"x", mode=0o4755)
    with pytest.raises(SetupRefused) as special:
        primitive.ready(targets, perms(mode="755"))
    refused(special.value, "already_in_that_state")
    primitive.ready(targets, perms(mode="750"))
    within.on([*STAT, PATH], 0, b"17777:0:0:regular file\n")
    with pytest.raises(SetupRefused) as long:
        primitive.ready(targets, perms(mode="600"))
    refused(long.value, "unexpected_output")


def test_what_is_not_there_or_is_no_file_or_directory_is_refused() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive, params = PermissionsBreak(), perms(mode="600")
    with pytest.raises(SetupRefused) as absent:
        primitive.ready(targets, params)
    refused(absent.value, "file_not_found")
    for target in ("/etc/nothing-there", "/etc"):
        within.link(PATH, target)
        with pytest.raises(SetupRefused) as link:
            primitive.ready(targets, params)
        refused(link.value, "not_a_regular_file")
        within.delete(PATH)
    within.write(PATH, b"x")
    for said, reason in (
        (b"644:0:0:socket\n", "not_a_regular_file"),
        (b"644:0:0:symbolic link\n", "not_a_regular_file"),
        (b"644:0:0:character special file\n", "not_a_regular_file"),
        (b"644:0:0\n", "unexpected_output"),
        (b"rw-r--r--:0:0:regular file\n", "unexpected_output"),
        (b"644:root:root:regular file\n", "unexpected_output"),
        (b"644:0:0:regular file\nmore\n", "unexpected_output"),
        (b"644:0:0:regular file", "unexpected_output"),
        (b"", "unexpected_output"),
    ):
        within.on([*STAT, PATH], 0, said)
        with pytest.raises(SetupRefused) as unread:
            primitive.ready(targets, params)
        refused(unread.value, reason)
    within.on([*STAT, PATH], 1, b"", b"stat: cannot statx\n")
    with pytest.raises(SetupRefused) as failed:
        primitive.ready(targets, params)
    refused(failed.value, "command_failed")
    within.on(["chmod", "--", "600", PATH], 1, b"", b"chmod: not permitted\n")
    with pytest.raises(FaultFailed) as unchanged:
        primitive.apply(targets, params)
    refused(unchanged.value, "command_failed")
    assert unchanged.value.program == "chmod"


@pytest.mark.parametrize(
    "wrong", [{}, {"mode": "4755"}, {"mode": "75"}, {"mode": "rwx"}, {"uid": -1}, {"gid": 1.5}]
)
def test_a_change_of_permissions_names_a_mode_or_an_owner(wrong: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        perms(**wrong)


def test_what_a_file_fault_says_of_itself() -> None:
    assert FileWrite().describe(write()) == f"Writes the file {PATH} on the node web."
    edits = mutate(
        {"edit": "delete_line", "line": "a"}, {"edit": "set_key", "key": "a", "value": "b"}
    )
    assert FileMutate().describe(edits) == (
        f"Edits the file {PATH} on the node web (delete_line, set_key)."
    )
    assert PATH in PermissionsBreak().describe(perms(mode="600"))
