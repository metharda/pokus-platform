"""The Incus client inside an instance: commands as lists, files by normal absolute paths."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_incus import client as incus
from potka_testing.incus import OPERATION, PROJECT, Script, ended, error, started, sync

EXEC = "/1.0/instances/web/exec"
FILES = "/1.0/instances/web/files"
WAIT = f"/1.0/operations/{OPERATION}/wait"
LOGS = "/1.0/instances/web/logs/exec-output"
# Incus names the two records of a command's output after its operation.
OUT, ERR = f"{LOGS}/exec_{OPERATION}.stdout", f"{LOGS}/exec_{OPERATION}.stderr"
EMPTY = httpx.Response(200, json={"type": "sync", "status": "Success", "status_code": 200})
OWNED = {"X-Incus-uid": "33", "X-Incus-gid": "0", "X-Incus-mode": "0640"}
RECORDS = "a command's operation without its exit status and output records"


def ran(code: Any = 0, **output: str) -> httpx.Response:
    return ended(metadata={"return": code, "output": output or {"1": OUT, "2": ERR}})


def raw(content: bytes, kind: str = "file", **headers: str) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"X-Incus-type": kind, **OWNED, **headers})


def command(end: httpx.Response | None = None) -> Script:
    script = Script().on("POST", EXEC, started()).on("GET", WAIT, end or ran())
    script.on("GET", OUT, httpx.Response(200, content=b"out\n"))
    script.on("GET", ERR, httpx.Response(200, content=b"\xff err"))
    return script.on("DELETE", OUT, EMPTY).on("DELETE", ERR, EMPTY)


def test_a_command_is_a_list_run_without_a_shell_and_its_output_is_fetched_and_removed() -> None:
    script = command(ran(3))
    argv = ["printf", "%s", "a b; c $(d) 'e'"]
    result = script.client().exec(
        "web", argv, environment={"LANG": "C"}, cwd="/srv/app", user=33, group=34
    )
    assert result == incus.Result(3, b"out\n", b"\xff err")
    assert script.body(0) == {
        "command": argv,
        "environment": {"LANG": "C"},
        "interactive": False,
        "wait-for-websocket": False,
        "record-output": True,
        "user": 33,
        "group": 34,
        "cwd": "/srv/app",
    }
    assert script.asked() == [
        ("POST", EXEC, {"project": PROJECT}),
        ("GET", WAIT, {"timeout": str(incus.EXEC_TIMEOUT)}),
        ("GET", OUT, {"project": PROJECT}),
        ("GET", ERR, {"project": PROJECT}),
        ("DELETE", OUT, {"project": PROJECT}),
        ("DELETE", ERR, {"project": PROJECT}),
    ]


def test_a_command_s_wait_is_the_caller_s_and_it_has_no_directory_unless_given() -> None:
    script = command()
    script.client().exec("web", ("true",), wait=5)
    assert "cwd" not in script.body(0)
    assert (script.body(0)["user"], script.body(0)["group"]) == (0, 0)
    assert script.asked()[1] == ("GET", WAIT, {"timeout": "5"})


@pytest.mark.parametrize(
    ("argv", "refusal"),
    [
        ("systemctl restart nginx", TypeError),
        (b"true", TypeError),
        (["systemctl", 7], TypeError),
        (["echo", b"x"], TypeError),
        ((a for a in ["true"]), TypeError),
        ({"true": 1}, TypeError),
        ({"true"}, TypeError),
        (7, TypeError),
        (None, TypeError),
        ([], ValueError),
        ((), ValueError),
        ([""], ValueError),
        (["", "true"], ValueError),
        (["echo", "a\0b"], ValueError),
        (["a\0b"], ValueError),
        (["echo", "a\ud800b"], ValueError),
        (["\ud800"], ValueError),
    ],
)
def test_what_is_not_a_list_of_strings_is_never_sent(argv: Any, refusal: type[Exception]) -> None:
    script = Script()
    with pytest.raises(refusal, match="a command") as raised:
        script.client().exec("web", argv)
    assert type(raised.value) is refusal
    assert script.requests == []


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ({"environment": {"A": 1}}, TypeError),
        ({"environment": {1: "a"}}, TypeError),
        ({"environment": [("A", "b")]}, TypeError),
        ({"environment": "A=b"}, TypeError),
        ({"environment": {"A": "\udcff"}}, ValueError),
        ({"environment": {"\ud800": "a"}}, ValueError),
        ({"environment": {"A": "x\0y"}}, ValueError),
        ({"environment": {"A\0": "x"}}, ValueError),
        ({"environment": {"A=B": "c"}}, ValueError),
        ({"environment": {"": "x"}}, ValueError),
        ({"cwd": "var/tmp"}, ValueError),
        ({"cwd": "/var/../etc"}, ValueError),
        ({"cwd": "/var/"}, ValueError),
        ({"cwd": 7}, ValueError),
        ({"user": True}, ValueError),
        ({"user": -1}, ValueError),
        ({"user": "33"}, ValueError),
        ({"user": 33.0}, ValueError),
        ({"user": 2**32}, ValueError),
        # The number that means "leave as it is" to chown is no ID.
        ({"user": 2**32 - 1}, ValueError),
        ({"group": 2**32 - 1}, ValueError),
        ({"group": True}, ValueError),
        ({"group": -1}, ValueError),
        ({"wait": -1}, ValueError),
        ({"wait": True}, ValueError),
        ({"wait": 2.5}, ValueError),
        ({"wait": "5"}, ValueError),
    ],
)
def test_a_command_s_environment_directory_user_and_wait_are_checked_before_it_is_sent(
    arguments: dict[str, Any], refusal: type[Exception]
) -> None:
    script = Script()
    with pytest.raises(refusal) as raised:
        script.client().exec("web", ["true"], **arguments)
    assert type(raised.value) is refusal
    assert script.requests == []


def test_the_largest_user_and_group_and_an_empty_value_are_sent() -> None:
    script = command()
    script.client().exec("web", ["true"], environment={"A": ""}, user=2**32 - 2, group=2**32 - 2)
    body = script.body(0)
    assert (body["environment"], body["user"], body["group"]) == ({"A": ""}, 2**32 - 2, 2**32 - 2)


@given(
    st.lists(
        st.text(st.characters(exclude_characters="\0", exclude_categories=["Cs"])),
        min_size=1,
        max_size=6,
    ).filter(lambda argv: argv[0] != "")
)
def test_every_argument_reaches_incus_as_one_element_unchanged(argv: list[str]) -> None:
    script = command()
    script.client().exec("web", argv)
    assert script.body(0)["command"] == argv


def test_an_argument_may_be_empty_and_a_whole_command_line_is_one_argument() -> None:
    script = command()
    script.client().exec("web", ["echo", "", "systemctl restart nginx; id"])
    assert script.body(0)["command"] == ["echo", "", "systemctl restart nginx; id"]


@pytest.mark.parametrize(
    "end",
    [
        ended(metadata={"return": 0}),
        ended(metadata={"output": {"1": OUT, "2": ERR}}),
        ran("0"),
        ran(True),
        ran(0.0),
        ended(metadata={"return": 0, "output": OUT}),
        ended(metadata={"return": 0, "output": None}),
        ran(**{"1": OUT}),
        ran(**{"1": OUT, "2": ERR, "3": ERR}),
        ran(**{"1": ERR, "2": OUT}),
        ran(**{"1": OUT, "2": "/1.0/instances/db/logs/exec-output/exec_7.stderr"}),
        ran(**{"1": OUT, "2": f"{LOGS}/exec_7.stderr"}),
        ran(**{"1": OUT, "2": f"{LOGS}/.."}),
        ran(**{"1": OUT, "2": f"{LOGS}/../../../db/state"}),
        ran(**{"1": OUT, "2": f"{ERR}?project=default"}),
        ended(),
    ],
)
def test_an_operation_that_is_no_command_s_result_is_refused(end: httpx.Response) -> None:
    script = command(end)
    with pytest.raises(incus.IncusError, match=RECORDS):
        script.client().exec("web", ["true"])
    assert [path for _, path, _ in script.asked()] == [EXEC, WAIT]


@pytest.mark.parametrize(
    ("code", "text"), [(127, "Command not found"), (126, "Command not executable")]
)
def test_a_program_that_is_not_there_or_cannot_run_is_a_result_and_its_records_are_removed(
    code: int, text: str
) -> None:
    # Incus fails the operation of a command that ended with 126 or 127; its record still
    # holds the status and the two records of the output.
    failed = ended(400, err=text, metadata={"return": code, "output": {"1": OUT, "2": ERR}})
    script = command(failed)
    assert script.client().exec("web", ["no-such-program"]) == incus.Result(
        code, b"out\n", b"\xff err"
    )
    assert [(method, path) for method, path, _ in script.asked()[2:]] == [
        ("GET", OUT),
        ("GET", ERR),
        ("DELETE", OUT),
        ("DELETE", ERR),
    ]


def test_a_command_that_has_not_ended_or_could_not_run_is_an_exception() -> None:
    script = command(ended(103))
    with pytest.raises(incus.OperationTimeout) as late:
        script.client().exec("web", ["sleep", "100"], wait=1)
    # The operation's record goes with the exception: Incus goes on with the command.
    assert late.value.operation["id"] == OPERATION
    assert [path for _, path, _ in script.asked()] == [EXEC, WAIT]
    # A failed operation that holds no status of the command is a failure, whatever its text;
    # so is one whose command ended with another status than the two Incus fails it for.
    # The operation has ended: what it recorded is removed, and fetched by nobody.
    gone = error(404, "Log file not found")
    for end, removed in (
        (ended(400, err="Command not found"), (EMPTY, EMPTY)),
        (
            ended(400, err="x", metadata={"return": 1, "output": {"1": OUT, "2": ERR}}),
            (EMPTY, EMPTY),
        ),
        (ended(401, err="Cancelled", metadata={"return": True}), (EMPTY, EMPTY)),
        # Records that were never made, or cannot be removed, change nothing of the failure.
        (ended(400, err="Failed to start"), (gone, gone)),
        (ended(400, err="Failed to start"), (error(500, "busy"), EMPTY)),
    ):
        script = Script().on("POST", EXEC, started()).on("GET", WAIT, end)
        script.on("DELETE", OUT, removed[0]).on("DELETE", ERR, removed[1])
        with pytest.raises(incus.OperationFailed) as failed:
            script.client().exec("web", ["no-such-program"])
        assert failed.value.operation["id"] == OPERATION
        assert [(method, path) for method, path, _ in script.asked()] == [
            ("POST", EXEC),
            ("GET", WAIT),
            ("DELETE", OUT),
            ("DELETE", ERR),
        ]


@pytest.mark.parametrize(
    "output",
    [
        {"1": OUT, "2": "/1.0/instances/db/logs/exec-output/exec_7.stderr"},
        {"1": OUT, "2": ERR, "3": ERR},
        {"1": OUT},
    ],
)
def test_a_program_that_is_not_there_with_other_records_than_its_own_is_no_result(
    output: dict[str, str],
) -> None:
    script = command(
        ended(400, err="Command not found", metadata={"return": 127, "output": output})
    )
    with pytest.raises(incus.IncusError, match=RECORDS) as refused:
        script.client().exec("web", ["no-such-program"])
    assert type(refused.value) is incus.IncusError
    assert [path for _, path, _ in script.asked()] == [EXEC, WAIT]


def test_a_record_that_cannot_be_fetched_or_removed_is_an_error_and_both_are_still_removed() -> (
    None
):
    gone = error(404, "Log file not found")
    script = Script().on("POST", EXEC, started()).on("GET", WAIT, ran())
    script.on("GET", OUT, gone).on("GET", ERR, httpx.Response(200, content=b"err"))
    script.on("DELETE", OUT, gone).on("DELETE", ERR, EMPTY)
    with pytest.raises(incus.OutputLost) as lost:
        script.client().exec("web", ["true"])
    # Every failure is named, the first one's status kept, and the command's own status:
    # the command did run.
    assert str(lost.value) == (
        f"GET {OUT}: 404 Log file not found; DELETE {OUT}: 404 Log file not found"
        " (the command ended with 0)"
    )
    assert (lost.value.status, lost.value.exit_code) == (404, 0)
    assert [(method, path) for method, path, _ in script.asked()[2:]] == [
        ("GET", OUT),
        ("GET", ERR),
        ("DELETE", OUT),
        ("DELETE", ERR),
    ]
    script = command(ran(3))
    script.answers[("DELETE", OUT)] = [error(500, "busy")]
    with pytest.raises(incus.OutputLost, match=r"DELETE .*stdout: 500 busy") as kept:
        script.client().exec("web", ["true"])
    assert (kept.value.status, kept.value.exit_code) == (500, 3)
    assert script.asked()[-1][:2] == ("DELETE", ERR)


def test_a_file_is_read_with_its_owner_and_mode() -> None:
    script = Script().on("GET", FILES, raw(b"\x00\xffbytes"))
    found = script.client().read_file("web", "/etc/nginx/nginx.conf")
    assert found == incus.File(b"\x00\xffbytes", 33, 0, 0o640)
    assert script.asked() == [("GET", FILES, {"project": PROJECT, "path": "/etc/nginx/nginx.conf"})]


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (raw(b"/etc/target", "symlink"), "/etc/x: a symlink, not a file"),
        (raw(b"", "directory"), "/etc/x: a directory, not a file"),
        (httpx.Response(200, content=b"x"), "/etc/x: a thing of no type, not a file"),
        (raw(b"x", **{"X-Incus-mode": "rw-r-----"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-mode": "-1"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-mode": "+644"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-mode": "644"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-mode": "100644"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-mode": "0o644"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-uid": ""}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-uid": "-5"}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-gid": " 33 "}), "/etc/x: a file without its owner and mode"),
        (raw(b"x", **{"X-Incus-gid": "0x21"}), "/etc/x: a file without its owner and mode"),
    ],
)
def test_what_is_not_a_regular_file_with_an_owner_is_not_read(
    answer: httpx.Response, text: str
) -> None:
    script = Script().on("GET", FILES, answer)
    with pytest.raises(incus.IncusError) as raised:
        script.client().read_file("web", "/etc/x")
    assert str(raised.value) == text


def test_a_file_that_is_not_there_is_not_found() -> None:
    script = Script().on("GET", FILES, error(404, "file does not exist"))
    with pytest.raises(incus.NotFound, match="404 file does not exist"):
        script.client().read_file("web", "/etc/x")


def listing(names: Any) -> httpx.Response:
    return httpx.Response(200, json=sync(names).json(), headers={"X-Incus-type": "directory"})


def test_a_directory_is_listed_and_a_file_is_not() -> None:
    script = Script().on("GET", FILES, listing(["a.conf", "b.conf"]), listing(None), raw(b"x"))
    client = script.client()
    assert client.list_directory("web", "/etc/nginx") == ("a.conf", "b.conf")
    assert client.list_directory("web", "/etc/empty") == ()
    with pytest.raises(incus.IncusError, match="/etc/x: a file, not a directory"):
        client.list_directory("web", "/etc/x")


@pytest.mark.parametrize("names", [{"a.conf": 1}, ["a.conf", 7], [["a.conf"]], "a.conf"])
def test_a_listing_that_is_no_list_of_names_is_not_read(names: Any) -> None:
    # Without the comparison, which refuses such an answer before the client reads it.
    script = Script().on("GET", FILES, listing(names))
    with pytest.raises(incus.IncusError, match="an answer that is not in Incus's form"):
        script.bare().list_directory("web", "/etc/nginx")


def test_a_file_is_written_whole_or_appended_with_its_owner_and_mode() -> None:
    script = Script().on("POST", FILES, EMPTY, EMPTY, EMPTY)
    client = script.client()
    client.write_file("web", "/srv/app/a", b"\x00one")
    client.write_file("web", "/srv/app/a", b"two", uid=33, gid=34, mode=0o600, append=True)
    client.write_file("web", "/srv/app/empty", b"", mode=0o7)
    first, second, third = script.requests
    assert (first.content, second.content, third.content) == (b"\x00one", b"two", b"")
    assert dict(first.url.params) == {"project": PROJECT, "path": "/srv/app/a"}
    incus_headers = [
        {k: v for k, v in r.headers.items() if k.startswith("x-incus-")} for r in script.requests
    ]
    # The mode always has a leading zero: Incus reads it by its prefix, octal only then.
    assert incus_headers == [
        {
            "x-incus-type": "file",
            "x-incus-write": "overwrite",
            "x-incus-uid": "0",
            "x-incus-gid": "0",
            "x-incus-mode": "0644",
        },
        {
            "x-incus-type": "file",
            "x-incus-write": "append",
            "x-incus-uid": "33",
            "x-incus-gid": "34",
            "x-incus-mode": "0600",
        },
        {
            "x-incus-type": "file",
            "x-incus-write": "overwrite",
            "x-incus-uid": "0",
            "x-incus-gid": "0",
            "x-incus-mode": "0007",
        },
    ]


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        ({"content": "text"}, TypeError),
        ({"content": bytearray(b"x")}, TypeError),
        # The files API sets the nine permission bits and no other.
        ({"mode": 0o1000}, ValueError),
        ({"mode": 0o4755}, ValueError),
        ({"mode": -1}, ValueError),
        ({"mode": True}, ValueError),
        ({"mode": "644"}, ValueError),
        ({"mode": 420.0}, ValueError),
        ({"uid": -1}, ValueError),
        ({"uid": True}, ValueError),
        ({"uid": "33"}, ValueError),
        ({"uid": 33.9}, ValueError),
        ({"uid": 2**32 - 1}, ValueError),
        ({"gid": 2**32}, ValueError),
    ],
)
def test_a_write_of_what_is_no_file_is_never_sent(
    arguments: dict[str, Any], refusal: type[Exception]
) -> None:
    script = Script()
    with pytest.raises(refusal) as raised:
        script.client().write_file("web", "/srv/app/a", **{"content": b"x", **arguments})
    assert type(raised.value) is refusal
    assert script.requests == []


def test_a_file_is_deleted_and_a_missing_one_is_not_found() -> None:
    script = Script().on("DELETE", FILES, EMPTY, error(404, "file not found"))
    client = script.client()
    client.delete_file("web", "/srv/app/a")
    with pytest.raises(incus.NotFound):
        client.delete_file("web", "/srv/app/a")
    assert script.asked()[0] == ("DELETE", FILES, {"project": PROJECT, "path": "/srv/app/a"})
    assert not [h for h in script.requests[0].headers if h.startswith("x-incus-")]


@pytest.mark.parametrize(
    "path",
    [
        "",
        "etc/x",
        "./etc/x",
        "/etc/../x",
        "/etc/./x",
        "/etc//x",
        "//etc/x",
        "/etc/x/",
        "/etc/x\0",
        "/etc/\ud800",
        "/..",
        7,
        None,
        b"/etc/x",
    ],
)
def test_a_path_that_is_not_absolute_and_normal_reaches_no_request(path: Any) -> None:
    script = Script()
    client = script.client()
    calls = (
        lambda: client.read_file("web", path),
        lambda: client.list_directory("web", path),
        lambda: client.write_file("web", path, b"x"),
        lambda: client.delete_file("web", path),
    )
    for call in calls:
        with pytest.raises(ValueError, match="not a") as raised:
            call()
        assert type(raised.value) is ValueError
    assert script.requests == []
    assert [incus.checked_path(p) for p in ("/", "/a", "/a/b.c", "/a b/ü")] == [
        "/",
        "/a",
        "/a/b.c",
        "/a b/ü",
    ]


@pytest.mark.parametrize(
    "path", ["/a?b=c", "/a#b", "/a&project=build", "/a%2e%2e/b", "/a b", "/a\nb", "/a+b", "/ü/…"]
)
def test_a_path_reaches_incus_as_one_query_value_whatever_it_holds(path: str) -> None:
    script = Script().on("GET", FILES, raw(b"x"), listing([])).on("DELETE", FILES, EMPTY)
    script.on("POST", FILES, EMPTY)
    client = script.client()
    client.read_file("web", path)
    client.list_directory("web", path)
    client.write_file("web", path, b"x")
    client.delete_file("web", path)
    assert len(script.requests) == 4
    for request in script.requests:
        assert request.url.path == FILES
        assert request.url.params.multi_items() == [("path", path), ("project", PROJECT)]


@pytest.mark.parametrize("name", ["", "web/../db", "web?x", "-web", "1234", "a" * 64, 7, None])
def test_what_is_not_an_instance_name_reaches_no_address_of_a_command_or_a_file(name: Any) -> None:
    script = Script()
    client = script.client()
    calls = (
        lambda: client.exec(name, ["true"]),
        lambda: client.read_file(name, "/etc/x"),
        lambda: client.list_directory(name, "/etc"),
        lambda: client.write_file(name, "/etc/x", b"x"),
        lambda: client.delete_file(name, "/etc/x"),
    )
    for call in calls:
        with pytest.raises(ValueError, match="not an instance name"):
            call()
    assert script.requests == []
