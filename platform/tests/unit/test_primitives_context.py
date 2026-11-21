"""A run's nodes as the fault primitives and probes reach them."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from potka_incus import client as incus
from potka_incus.inside import Ended, Execution, Inside
from potka_primitives import context
from potka_primitives.context import Node, Ran, Targets
from potka_primitives.errors import (
    FAILURES,
    REASONS,
    PrimitiveError,
    Refusal,
    TargetFailure,
    UnknownNode,
)
from potka_testing.targets import INSTANCES, inside, install, run

WEB = INSTANCES["web"]
EXEC = f"/1.0/instances/{WEB}/exec"
FILES = f"/1.0/instances/{WEB}/files"
PLACE = "/etc/zq-secret-place/app.conf"


def clean(error: BaseException) -> str:
    """ERROR's text, after checking that nothing of the client's came with it."""
    assert error.__cause__ is None and error.__context__ is None
    text = f"{error} {error!r} {error.args}"
    assert WEB not in text and "zq-secret" not in text and "/etc" not in text
    return str(error)


def test_a_command_runs_as_a_list_with_the_library_s_environment() -> None:
    fake, targets = run()
    web = inside(fake, "web")
    web.on(["stat", "--format=%a", "--", "/etc"], 3, b"755\n", b"stat: a word\n")
    ran = targets.node("web").run(
        ["stat", "--format=%a", "--", "/etc"], cwd="/srv", user=33, group=34
    )
    assert ran == Ran("stat", 3, b"755\n", b"stat: a word\n")
    assert web.executions == [
        Execution(
            ("stat", "--format=%a", "--", "/etc"),
            (
                ("LC_ALL", "C"),
                ("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
            ),
            "/srv",
            33,
            34,
        )
    ]
    # The path is Incus's own default, in its order, and nothing else is set.
    assert context.ENVIRONMENT == {"PATH": ":".join(context.PATH), "LC_ALL": "C"}
    assert inside(fake, "db").executions == []


def test_a_result_is_read_strictly() -> None:
    ran = Ran("stat", 1, b"a\xffb", b"stat: cannot statx: No space left on device\n")
    assert ran.expect(1, 2) is ran
    with pytest.raises(Refusal) as failed:
        ran.expect()
    assert (failed.value.reason, failed.value.program, failed.value.status) == (
        "command_failed",
        "stat",
        1,
    )
    with pytest.raises(Refusal) as unread:
        ran.text()
    assert unread.value.reason == "unexpected_output" and unread.value.__context__ is None
    assert Ran("stat", 0, "é\n".encode(), b"").text() == "é\n"
    assert ran.said("No space left on device") and not ran.said("Disk quota exceeded")


@pytest.mark.parametrize("argv", [[], [""], ["/usr/bin/stat"], ["bin/stat", "x"]])
def test_a_program_is_a_name_without_a_directory(argv: list[str]) -> None:
    fake, targets = run()
    with pytest.raises(ValueError, match="a name without a directory"):
        targets.node("web").run(argv)
    assert inside(fake, "web").executions == []


@pytest.mark.parametrize("argv", ["stat -- /etc", "stat", ["stat", 5], ["stat", b"/etc"], [None]])
def test_a_command_is_a_list_of_strings_and_never_a_command_line(argv: Any) -> None:
    fake, targets = run()
    before = len(fake.asked)
    with pytest.raises(TypeError, match="never a command line"):
        targets.node("web").run(argv)
    assert inside(fake, "web").executions == [] and len(fake.asked) == before
    # Any sequence of strings is one.
    inside(fake, "web").on(["true"], 0)
    assert targets.node("web").run(("true",)).exit_code == 0


@pytest.mark.parametrize("status", [126, 127])
def test_a_program_that_is_not_there_or_cannot_be_run_is_a_missing_tool(status: int) -> None:
    fake, targets = run()
    inside(fake, "web").on(["tc", "-V"], status, b"", b"exec failed\n")
    with pytest.raises(Refusal) as missing:
        targets.node("web").run(["tc", "-V"])
    assert clean(missing.value) == "tool_missing"
    assert (missing.value.alias, missing.value.program, missing.value.status) == (
        "web",
        "tc",
        status,
    )


def test_files_are_read_written_and_removed_through_the_files_calls() -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    node.write("/etc/app.conf", b"a = 1\n", uid=33, gid=34, mode=0o640)
    assert node.read("/etc/app.conf") == incus.File(b"a = 1\n", 33, 34, 0o640)
    # A file that exists keeps its owner and mode, as the files call has it.
    node.write("/etc/app.conf", b"a = 2\n", uid=0, gid=0, mode=0o600)
    assert node.read("/etc/app.conf") == incus.File(b"a = 2\n", 33, 34, 0o640)
    web.link("/etc/link", "app.conf")
    assert [node.kind(path) for path in ("/etc/app.conf", "/etc", "/etc/link", "/etc/none")] == [
        "file",
        "directory",
        "symlink",
        None,
    ]
    # A link that leads nowhere is still something that is there.
    node.remove("/etc/app.conf")
    assert node.kind("/etc/link") == "symlink" and node.kind("/etc/app.conf") is None
    node.remove("/etc/link")
    assert node.kind("/etc/link") is None and node.kind("/nowhere/at/all") is None
    assert node.kind("/") == "directory"
    assert web.executions == []


def listings(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """The paths the client is asked to list and to read from here on, in order."""
    listed: list[str] = []
    read: list[str] = []
    list_directory, read_file = incus.Incus.list_directory, incus.Incus.read_file

    def listing(self: incus.Incus, name: str, path: str) -> tuple[str, ...]:
        listed.append(path)
        return list_directory(self, name, path)

    def reading(self: incus.Incus, name: str, path: str) -> incus.File:
        read.append(path)
        return read_file(self, name, path)

    monkeypatch.setattr(incus.Incus, "list_directory", listing)
    monkeypatch.setattr(incus.Incus, "read_file", reading)
    return listed, read


def test_whether_something_is_at_a_path_is_read_from_its_directory_s_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.write("/etc/app.conf", b"x" * 4096)
    web.mkdir("/etc/directory")
    web.link("/etc/link", "app.conf")
    web.link("/etc/gone", "/nowhere/at/all")
    web.link("/etc/linked", "/srv")
    web.write("/srv/file", b"")
    there = {
        "/": True,
        "/etc": True,
        "/etc/app.conf": True,
        "/etc/directory": True,
        "/etc/link": True,
        "/etc/gone": True,
        "/etc/none": False,
        "/none/at/all": False,
        "/etc/app.conf/below": False,
        "/etc/linked": True,
        "/etc/linked/file": True,
        "/etc/linked/none": False,
    }
    assert {path: node.exists(path) for path in there} == there
    listed, read = listings(monkeypatch)
    # One listing, of the directory above: nothing of the file itself is fetched.
    assert node.exists("/etc/app.conf") and (listed, read) == (["/etc"], [])
    del listed[:]
    assert not node.exists("/etc/none") and (listed, read) == (["/etc"], [])
    assert web.executions == []


def test_a_link_that_leads_nowhere_is_read_as_a_link() -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.mkdir("/etc/zq-secret-place")
    web.link(PLACE, "/nowhere/at/all")
    with pytest.raises(Refusal) as link:
        node.read(PLACE)
    assert clean(link.value) == "not_a_regular_file" and link.value.alias == "web"
    web.delete(PLACE)
    with pytest.raises(Refusal) as nothing:
        node.read(PLACE)
    assert clean(nothing.value) == "file_not_found"


def test_an_instance_that_is_gone_is_a_failure_and_never_a_path_that_is_not_there() -> None:
    fake, targets = run(programs=("stat",))
    web, node = inside(fake, "web"), targets.node("web")
    web.mkdir("/etc/zq-secret-place")
    web.write(PLACE, b"x")
    web.on(["stat", "--", PLACE], 0)
    calls: list[Callable[[], Any]] = [
        lambda: node.has("stat"),
        lambda: node.kind(PLACE),
        lambda: node.kind("/etc/zq-secret-none"),
        lambda: node.exists(PLACE),
        lambda: node.exists("/etc/zq-secret-none/below"),
        lambda: node.read(PLACE),
        lambda: node.read("/etc/zq-secret-none"),
        lambda: node.write(PLACE, b"y"),
        lambda: node.write("/etc/zq-secret-none/file", b"y"),
        lambda: node.remove(PLACE),
        lambda: node.run(["stat", "--", PLACE]),
        node.is_container,
        node.root_size,
        node.root_usage,
    ]
    assert node.kind("/etc/zq-secret-none") is None and node.read(PLACE).content == b"x"
    # Incus answers for a path of an instance that is gone as for a path that is not there.
    del fake.instances["bench", WEB]
    for call in calls:
        with pytest.raises(TargetFailure) as gone:
            call()
        assert clean(gone.value) == "not_found" and gone.value.alias == "web"


def test_a_path_is_said_to_be_not_there_only_when_the_instance_is_known_to_be() -> None:
    fake, targets = run()
    node = targets.node("web")
    inside(fake, "web").delete("/usr/local/sbin")
    record = f"/1.0/instances/{WEB}"
    asks: list[Callable[[], Any]] = [
        lambda: node.read("/etc/zq-secret-none"),
        lambda: node.kind("/etc/zq-secret-none"),
        lambda: node.exists("/zq-secret-none/below"),
        lambda: node.remove("/etc/zq-secret-none"),
        lambda: node.has("stat"),
    ]
    for ask in asks:
        with (
            fake.refusing("GET", record, 500, f"lost {WEB}"),
            pytest.raises(TargetFailure) as unsure,
        ):
            ask()
        assert clean(unsure.value) == "incus_error"


@pytest.mark.parametrize(
    "error",
    [
        incus.OperationTimeout(f"{WEB}: not ended", {}),
        incus.OperationFailed(f"{WEB}: {PLACE}", {}),
        incus.OutputLost(f"{WEB}: {PLACE}", 500, 0),
        incus.WrongKind(f"{PLACE}: a file, not a directory", "file"),
        incus.NotFound(f"{WEB}", 404),
        incus.Forbidden(f"{WEB}", 403),
        incus.Unreachable(f"{WEB}"),
        incus.IncusError(f"{WEB}: {PLACE}", 500),
        ValueError(f"{PLACE}"),
        TypeError(f"{PLACE}"),
    ],
)
def test_every_error_of_the_client_under_a_command_is_the_library_s(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _, targets = run()

    def refuse(*_: Any, **__: Any) -> Any:
        raise error

    monkeypatch.setattr(incus.Incus, "exec", refuse)
    with pytest.raises(TargetFailure) as failed:
        targets.node("web").run(["stat", "--", PLACE])
    assert clean(failed.value) == context._failure(error)
    assert failed.value.alias == "web" and str(failed.value) in FAILURES


def test_what_is_not_a_regular_file_or_is_not_there_is_a_refusal() -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.mkdir("/etc/zq-secret-place")
    web.write(PLACE, b"x")
    web.link("/etc/zq-secret-place/link", "app.conf")
    calls: list[tuple[Callable[[], Any], str]] = [
        (lambda: node.read("/etc/zq-secret-place/none"), "file_not_found"),
        (lambda: node.read("/etc/zq-secret-place"), "not_a_regular_file"),
        (lambda: node.read("/etc/zq-secret-place/link"), "not_a_regular_file"),
        (lambda: node.write("/etc/zq-secret-none/app.conf", b"x"), "file_not_found"),
        (lambda: node.remove("/etc/zq-secret-place/none"), "file_not_found"),
    ]
    for call, reason in calls:
        with pytest.raises(Refusal) as refused:
            call()
        assert clean(refused.value) == reason
        assert refused.value.alias == "web"
    assert web.nodes[PLACE].content == b"x"


LOST = httpx.ConnectError("no route to t-7f3a91c2-n1")


def failures(fake: Any) -> list[tuple[str, Callable[[], Any]]]:
    """Each kind of failure of the client, as a block to make a call in."""
    return [
        ("forbidden", lambda: fake.refusing("GET", FILES, 403, f"not allowed: {PLACE}")),
        ("incus_error", lambda: fake.refusing("GET", FILES, 500, f"broken: {PLACE}")),
        ("unreachable", lambda: fake.losing(LOST)),
    ]


@pytest.mark.parametrize("kind", ["forbidden", "incus_error", "unreachable"])
def test_no_error_of_the_client_leaves_a_node(kind: str) -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.mkdir("/etc/zq-secret-place")
    web.write(PLACE, b"x")
    block = dict(failures(fake))[kind]
    for call in (
        lambda: node.read(PLACE),
        lambda: node.kind(PLACE),
        lambda: node.has("stat"),
    ):
        with block(), pytest.raises(TargetFailure) as failed:
            call()
        assert clean(failed.value) == kind
        assert failed.value.alias == "web"
    # The same for a write and a removal, whose requests are others.
    with fake.refusing("POST", FILES, 500, f"broken: {PLACE}"), pytest.raises(TargetFailure) as w:
        node.write(PLACE, b"y")
    assert clean(w.value) == "incus_error"
    with fake.refusing("DELETE", FILES, 403, f"no: {PLACE}"), pytest.raises(TargetFailure) as r:
        node.remove(PLACE)
    assert clean(r.value) == "forbidden"
    assert web.nodes[PLACE].content == b"x"


def test_a_command_s_failures_are_the_library_s_too() -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.on(["stat", "--", PLACE], 0, b"", b"")
    with fake.failing(f"cannot run in {WEB}"), pytest.raises(TargetFailure) as failed:
        node.run(["stat", "--", PLACE])
    assert clean(failed.value) == "operation_failed"
    with fake.refusing("POST", EXEC, 404, f"no instance {WEB}"), pytest.raises(TargetFailure) as g:
        node.run(["stat", "--", PLACE])
    assert clean(g.value) == "not_found"
    # An argument the client will not send is no request and no client's error either.
    with pytest.raises(TargetFailure) as unsent:
        node.run(["stat", "--", "zq-secret\0"])
    assert clean(unsent.value) == "not_sent"
    with pytest.raises(TargetFailure) as relative:
        node.read("etc/zq-secret-place")
    assert clean(relative.value) == "not_sent"
    record = f"/1.0/instances/{WEB}"
    with fake.refusing("GET", record, 500, f"lost {WEB}"), pytest.raises(TargetFailure) as sized:
        node.root_size()
    assert clean(sized.value) == "incus_error"
    with (
        fake.refusing("GET", f"{record}/state", 500, f"lost {WEB}"),
        pytest.raises(TargetFailure) as used,
    ):
        node.root_usage()
    assert clean(used.value) == "incus_error"


def test_every_kind_of_the_client_s_errors_has_a_reason() -> None:
    kinds = {
        incus.WrongKind("x", "file"): "wrong_kind",
        incus.NotFound("x", 404): "not_found",
        incus.Forbidden("x", 403): "forbidden",
        incus.Unreachable("x"): "unreachable",
        incus.OperationTimeout("x", {}): "operation_timeout",
        incus.OperationFailed("x", {}): "operation_failed",
        incus.OutputLost("x", 500, 0): "output_lost",
        incus.IncusError("x", 500): "incus_error",
        ValueError("x"): "not_sent",
        TypeError("x"): "not_sent",
    }
    for error, kind in kinds.items():
        assert context._failure(error) == kind
    assert set(kinds.values()) == FAILURES
    # Every subclass the client has is one of them: a new one would be an incus_error.
    classes = {
        cls
        for cls in vars(incus).values()
        if isinstance(cls, type) and issubclass(cls, incus.IncusError)
    }
    assert classes == {type(error) for error in kinds if isinstance(error, incus.IncusError)}
    assert not REASONS & FAILURES


def test_an_error_s_text_is_its_reason_and_the_reason_is_of_its_list() -> None:
    failed = TargetFailure("unreachable", alias="web", program="tc", status=2)
    assert (str(failed), failed.args) == ("unreachable", ("unreachable",))
    assert (failed.reason, failed.alias, failed.program, failed.status) == (
        "unreachable",
        "web",
        "tc",
        2,
    )
    for cls, reason in ((TargetFailure, "tool_missing"), (Refusal, "unreachable")):
        with pytest.raises(ValueError, match="not a reason") as wrong:
            cls(reason)
        assert reason not in str(wrong.value)
    with pytest.raises(ValueError, match="not a reason"):
        PrimitiveError("/etc/zq-secret-place")
    unknown = UnknownNode("zqalias")
    assert (str(unknown), unknown.alias) == ("unknown_node", "zqalias")


def test_a_program_is_looked_for_in_the_directories_of_the_command_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, targets = run(programs=("stat", "tc"))
    listed, read = listings(monkeypatch)
    web, node = inside(fake, "web"), targets.node("web")
    web.write("/usr/local/bin/local-tool", b"", mode=0o755)
    web.mkdir("/opt/bin")
    web.write("/opt/bin/elsewhere", b"", mode=0o755)
    before = len(fake.asked)
    assert node.has("stat") and node.has("tc") and node.has("local-tool")
    assert not node.has("fallocate") and not node.has("elsewhere")
    # Six listings, of which two find a link and are passed over; then nothing more is
    # asked, and no command ever runs for it.
    assert [asked.path for asked in fake.asked[before:]] == [FILES] * 6
    assert (listed, read) == (list(context.PATH), [])
    assert node.has("stat") and len(fake.asked) == before + 6
    assert web.executions == []
    # Another node has its own programs.
    assert not targets.node("db").has("local-tool")


def test_a_path_directory_that_is_not_there_holds_nothing_and_a_file_there_is_a_failure() -> None:
    fake, targets = run(programs=("stat",))
    web = inside(fake, "web")
    web.delete("/usr/local/sbin")
    assert targets.node("web").has("stat")
    fake2, targets2 = run(programs=("stat",))
    web2 = inside(fake2, "web")
    web2.delete("/usr/local/bin")
    web2.write("/usr/local/bin", b"")
    with pytest.raises(TargetFailure) as failed:
        targets2.node("web").has("stat")
    assert clean(failed.value) == "wrong_kind"
    # A directory of the path that is a link holds nothing here, wherever it leads: what
    # the other directories hold is all that is said to be there.
    fake3, targets3 = run()
    web3 = inside(fake3, "web")
    web3.mkdir("/opt/bin")
    web3.write("/opt/bin/elsewhere", b"", mode=0o755)
    web3.delete("/usr/local/bin")
    web3.link("/usr/local/bin", "/opt/bin")
    assert not targets3.node("web").has("elsewhere")


def test_a_name_that_is_listed_and_cannot_be_run_shows_when_it_is_run() -> None:
    fake, targets = run()
    web, node = inside(fake, "web"), targets.node("web")
    web.link("/usr/bin/stat", "/nowhere/stat")
    assert node.has("stat")

    def not_there(_: Inside, asked: Execution) -> Ended | None:
        return Ended(127, b"", b"not found\n") if asked.argv[0] == "stat" else None

    web.answer(not_there)
    with pytest.raises(Refusal) as missing:
        node.run(["stat", "--", "/etc"])
    assert missing.value.reason == "tool_missing"


def test_the_root_disk_s_size_and_use_are_incus_s_or_none() -> None:
    fake, targets = run()
    node = targets.node("web")
    assert node.root_size() == 8 * 1024**3
    assert node.root_usage() == inside(fake, "web").usage == 900 * 1024**2
    instance = fake.instance("bench", WEB)
    # A size in a form that cannot be read is no size.
    fake.profiles["bench", "target-container"].devices["root"]["size"] = "8 gigabytes"
    assert node.root_size() is None
    del fake.profiles["bench", "target-container"].devices["root"]["size"]
    assert node.root_size() is None
    # Without a root disk device there is no use to read.
    fake.profiles["bench", "target-container"].devices.clear()
    assert instance.devices == {}
    assert node.root_usage() is None


def test_a_use_that_the_state_does_not_say_is_none_and_no_zero() -> None:
    fake, targets = run()
    node = targets.node("web")
    fake.instance("bench", WEB).measured = False
    assert node.root_usage() is None and node.root_size() == 8 * 1024**3
    fake.instance("bench", WEB).measured = True
    inside(fake, "web").base = 0
    assert node.root_usage() == 0


def test_a_node_says_whether_it_is_a_container() -> None:
    fake, targets = run()
    assert targets.node("web").is_container() and targets.node("db").is_container()
    fake.instance("bench", WEB).kind = "virtual-machine"
    assert not targets.node("web").is_container() and targets.node("db").is_container()


def test_a_run_s_nodes_are_named_by_alias() -> None:
    fake, targets = run()
    assert targets.node("web") is targets.node("web")
    assert (targets.node("web").alias, targets.node("db").alias) == ("web", "db")
    with pytest.raises(UnknownNode) as unknown:
        targets.node("zqalias")
    assert (str(unknown.value), unknown.value.alias) == ("unknown_node", "zqalias")
    with pytest.raises(UnknownNode):
        targets.probe_client()
    bench = fake.client("bench")
    with_probe = Targets(bench, {"web": WEB}, probe_client=INSTANCES["db"])
    assert isinstance(with_probe.probe_client(), Node)
    inside(fake, "db").on(["true"], 0)
    assert with_probe.probe_client().run(["true"]).exit_code == 0


def test_install_puts_a_program_where_the_images_have_it() -> None:
    fake, _ = run()
    web = inside(fake, "web")
    install(web, "tc")
    install(web, "stat")
    assert {"/usr/sbin/tc", "/usr/bin/stat"} <= set(web.nodes)
