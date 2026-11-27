"""FakeIncus answers the client as Incus does, and keeps what a test needs to see."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from potka_incus import client as incus
from potka_incus.fake import URL, FakeIncus
from potka_incus.inside import Ended, Execution, Inside
from potka_testing.fake_incus import (
    ALIAS,
    COPIED,
    FINGERPRINT,
    IMAGE_USED,
    ROOT_SIZE,
    TARGET,
    client,
    connection,
    lab,
    running,
)

BENCH = {"project": "bench"}
GIB = 1024**3
OPERATION = "/1.0/operations/00000000-0000-0000-0000-00000000000{}/wait"
EXEC = {"record-output": True, "interactive": False, "wait-for-websocket": False}


def test_an_instance_is_created_stopped_with_its_profiles_applied() -> None:
    fake = lab()
    bench = client(fake)
    bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,), config={"limits.cpu": "2-3"})
    found = bench.instance("web")
    assert (found.status, found.status_code, found.kind) == ("Stopped", 102, "container")
    assert found.profiles == (TARGET,)
    # Its own configuration: what was asked for, the image's properties, the image itself.
    own = {**COPIED, "limits.cpu": "2-3", "volatile.base_image": FINGERPRINT}
    assert found.config == own
    assert found.expanded_config == {"security.nesting": "false", "limits.memory": "1GiB", **own}
    assert found.base_image == FINGERPRINT
    assert (found.root_device, found.root_size) == ("root", ROOT_SIZE)
    assert bench.instances() == ("web",)
    assert fake.instance("bench", "web").inside.size == ROOT_SIZE


def test_profiles_apply_in_order_and_an_instance_s_own_keys_win() -> None:
    fake = lab()
    wide = {"type": "disk", "path": "/", "pool": "pinka-targets", "size": "16GiB"}
    fake.profile(
        "bench",
        "wide",
        {"security.nesting": "true", "limits.memory": "4GiB", "limits.processes": "100"},
        {"root": wide, "eth0": {"type": "nic", "name": "eth0", "network": "r-abc"}},
    )
    bench = client(fake)
    bench.create("late", fingerprint=FINGERPRINT, profiles=(TARGET, "wide"))
    bench.create("early", fingerprint=FINGERPRINT, profiles=("wide", TARGET))
    late, early = bench.instance("late"), bench.instance("early")
    # Of two profiles with one key, the later one's value stays; a device is replaced whole.
    assert (late.expanded_config["security.nesting"], late.root_size) == ("true", 16 * GIB)
    assert (early.expanded_config["security.nesting"], early.root_size) == ("false", 8 * GIB)
    assert (late.expanded_config["limits.memory"], early.expanded_config["limits.memory"]) == (
        "4GiB",
        "1GiB",
    )
    assert early.expanded_config["limits.processes"] == "100"
    assert early.expanded_devices["root"] == {**wide, "size": "8GiB"}
    assert sorted(early.expanded_devices) == ["eth0", "root"]
    # Whole: a later device with fewer keys leaves none of the earlier one's.
    fake.profile("bench", "plain", devices={"root": {"type": "disk", "path": "/"}})
    bench.create("plain", fingerprint=FINGERPRINT, profiles=(TARGET, "plain"))
    assert bench.instance("plain").expanded_devices["root"] == {"type": "disk", "path": "/"}
    assert bench.instance("plain").root_size is None
    # What the instance itself was given wins over every profile. The client gives an
    # instance no disk of its own, so this creation is asked of Incus directly.
    body = {
        "name": "own",
        "type": "container",
        "source": {"type": "image", "fingerprint": FINGERPRINT},
        "profiles": [TARGET, "wide"],
        "config": {"security.nesting": "false"},
        "devices": {"root": {**wide, "size": "32GiB"}},
    }
    with connection(fake, "bench") as http:
        assert http.post("/1.0/instances", params={"project": "bench"}, json=body).is_success
    own = bench.instance("own")
    assert (own.expanded_config["security.nesting"], own.root_size) == ("false", 32 * GIB)
    assert (own.config["security.nesting"], sorted(own.devices)) == ("false", ["root"])
    assert bench.state("own").disk == {"root": incus.Disk(IMAGE_USED, 32 * GIB)}


def test_the_root_disk_is_the_disk_at_the_root_whatever_its_name() -> None:
    fake = FakeIncus()
    sized = {"type": "disk", "path": "/", "pool": "p", "size": "2GiB"}
    data = {"type": "disk", "path": "/data", "source": "/srv/data"}
    fake.profile("bench", "named", devices={"data": data, "rootfs": sized})
    fake.profile("bench", "bare", devices={"root": {"type": "disk", "path": "/", "pool": "p"}})
    fake.profile("bench", "diskless", devices={"data": data})
    # A disk with a source is no root disk, wherever it is mounted.
    fake.profile("bench", "mounted", devices={"root": {**sized, "source": "/srv/tree"}})
    fake.image(FINGERPRINT)
    bench = client(fake)
    for name in ("named", "bare", "diskless", "mounted"):
        bench.create(name, fingerprint=FINGERPRINT, profiles=(name,))
    found = {name: bench.instance(name) for name in bench.instances()}
    assert {name: (i.root_device, i.root_size) for name, i in found.items()} == {
        "named": ("rootfs", 2 * GIB),
        "bare": ("root", None),
        "diskless": (None, None),
        "mounted": (None, None),
    }
    assert bench.state("named").disk == {"rootfs": incus.Disk(0, 2 * GIB)}
    assert bench.state("bare").disk == {"root": incus.Disk(0, 0)}
    assert (bench.state("diskless").disk, bench.state("mounted").disk) == ({}, {})
    assert fake.instance("bench", "named").inside.size == 2 * GIB
    assert fake.instance("bench", "bare").inside.size is None
    assert fake.instance("bench", "mounted").inside.size is None


def test_a_creation_that_incus_refuses_is_refused() -> None:
    fake = lab()
    fake.profile("build", "builder")
    bench = client(fake)
    bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    with pytest.raises(incus.IncusError, match='500 Instance "web" already exists'):
        bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    with pytest.raises(incus.NotFound, match="Image not found"):
        bench.create("db", fingerprint="cd" * 32, profiles=(TARGET,))
    with pytest.raises(incus.NotFound, match="Profile not found"):
        bench.create("db", fingerprint=FINGERPRINT, profiles=(TARGET, "no-such-profile"))
    # A profile of another project is none of this one's.
    with pytest.raises(incus.NotFound, match="Profile not found"):
        bench.create("db", fingerprint=FINGERPRINT, profiles=("builder",))
    assert bench.instances() == ("web",)
    # Every creation asked for is kept, the refused ones too.
    assert [body["name"] for body in fake.created()] == ["web", "web", "db", "db", "db"]


def test_incus_itself_would_take_an_alias_and_an_image_s_profiles() -> None:
    # What the client never sends: a creation by alias, without a profile list or a type.
    # It shows that the fake, like Incus, would follow the image's record.
    fake = lab()
    fake.images[FINGERPRINT].profiles = [TARGET]
    fake.image("cd" * 32, "machine/1", kind="virtual-machine").profiles = ["default", TARGET]
    with connection(fake, "bench") as http:
        body = {"name": "by-alias", "source": {"type": "image", "alias": ALIAS}}
        assert http.post("/1.0/instances", params=BENCH, json=body).status_code == 202
        machine = {"name": "machine", "source": {"type": "image", "alias": "machine/1"}}
        # Without a type Incus asks for a container, whatever the image is.
        with pytest.raises(AssertionError, match="no instance of another kind than its image"):
            http.post("/1.0/instances", params=BENCH, json=machine)
        named = {**machine, "type": "virtual-machine"}
        assert http.post("/1.0/instances", params=BENCH, json=named).status_code == 202
        # A fingerprint no image has is not found, whatever alias comes with it.
        source = {"type": "image", "fingerprint": "ef" * 32, "alias": ALIAS}
        refused = http.post("/1.0/instances", params=BENCH, json={"name": "x", "source": source})
        assert (refused.status_code, refused.json()["error"]) == (404, "Image not found")
    made = fake.instance("bench", "by-alias"), fake.instance("bench", "machine")
    assert [(i.kind, i.profiles) for i in made] == [
        ("container", [TARGET]),
        ("virtual-machine", ["default", TARGET]),
    ]
    assert made[1].config["volatile.base_image"] == "cd" * 32


def test_a_creation_the_stand_in_has_no_model_of_fails_the_test() -> None:
    fake = lab()
    fake.image("cd" * 32, kind="virtual-machine")
    with connection(fake, "bench") as http:
        for source in ({"type": "copy", "source": "web"}, {"type": "none"}, {}):
            with pytest.raises(AssertionError, match="makes an instance from an image only"):
                http.post("/1.0/instances", params=BENCH, json={"name": "x", "source": source})
    with pytest.raises(AssertionError, match="no instance of another kind than its image"):
        client(fake).create("x", fingerprint="cd" * 32, profiles=(TARGET,))
    # A key that Incus itself writes into an instance is none a creation brings.
    with connection(fake, "bench") as http:
        source = {"type": "image", "fingerprint": FINGERPRINT}
        for key in ("image.os", "volatile.base_image"):
            body = {"name": "x", "source": source, "config": {key: "x"}}
            with pytest.raises(AssertionError, match=r"no image\.\* or volatile\.\* key"):
                http.post("/1.0/instances", params=BENCH, json=body)
    assert client(fake).instances() == ()


def test_an_instance_starts_stops_and_is_deleted_only_when_stopped() -> None:
    fake = lab()
    bench = running(fake)
    assert bench.instance("web").status == "Running"
    assert bench.state("web").pid > 0
    with pytest.raises(
        incus.OperationFailed, match="Starting instance: The instance is already running"
    ):
        bench.start("web")
    with pytest.raises(incus.IncusError, match="400 Instance is running"):
        bench.delete("web")
    bench.stop("web", force=True)
    assert (bench.state("web").status, bench.state("web").pid) == ("Stopped", 0)
    with pytest.raises(incus.OperationFailed, match="The instance is already stopped"):
        bench.stop("web")
    bench.delete("web")
    assert bench.instances() == ()
    with pytest.raises(incus.NotFound, match="404 Instance not found"):
        bench.instance("web")


def test_an_image_that_requires_nesting_does_not_start_without_it() -> None:
    # Incus reads this one property of an image's record when an instance starts.
    fake = lab()
    fake.profile("bench", "nested", {"security.nesting": "true"})
    fake.images[FINGERPRINT].properties["requirements.nesting"] = "true"
    bench = client(fake)
    bench.create("flat", fingerprint=FINGERPRINT, profiles=(TARGET,))
    bench.create("nested", fingerprint=FINGERPRINT, profiles=(TARGET, "nested"))
    bench.create(
        "own", fingerprint=FINGERPRINT, profiles=(TARGET,), config={"security.nesting": "true"}
    )
    with pytest.raises(incus.OperationFailed, match=r"requires nesting\. Please set security"):
        bench.start("flat")
    assert bench.instance("flat").status == "Stopped"
    bench.start("nested")
    bench.start("own")
    # An image that asks for nothing starts either way.
    fake.images[FINGERPRINT].properties["requirements.nesting"] = "false"
    bench.create("later", fingerprint=FINGERPRINT, profiles=(TARGET,))
    bench.start("later")
    assert bench.instances() == ("flat", "later", "nested", "own")


def test_a_certificate_sees_only_its_projects_in_every_call() -> None:
    fake = lab()
    bench = running(fake)
    web = fake.instance("bench", "web")
    web.inside.write("/a", b"x")
    build = client(fake, "build")
    assert build.instances() == ()
    with pytest.raises(incus.NotFound):
        build.instance("web")
    # A connection of build that names bench is refused in every call, in the same way for
    # an instance that exists and for one that does not.
    other = client(fake, "bench", "build")
    calls: list[Callable[[str], object]] = [
        lambda name: other.instances(),
        lambda name: other.create(name, fingerprint=FINGERPRINT, profiles=(TARGET,)),
        other.instance,
        other.state,
        other.start,
        other.stop,
        other.delete,
        lambda name: other.exec(name, ["true"]),
        lambda name: other.read_file(name, "/a"),
        lambda name: other.list_directory(name, "/"),
        lambda name: other.write_file(name, "/a", b"y"),
        lambda name: other.delete_file(name, "/a"),
    ]
    for name in ("web", "missing"):
        for call in calls:
            with pytest.raises(incus.Forbidden, match=r"403 not authorized$"):
                call(name)
    with connection(fake, "build") as http:
        record = "/1.0/instances/web/logs/exec-output/exec_x.stdout"
        assert http.get(record, params=BENCH).status_code == 403
        assert http.delete(record, params=BENCH).status_code == 403
        # A request without a project is one for the project "default".
        assert http.get("/1.0/instances").status_code == 403
    assert (web.running, web.inside.nodes["/a"].content, web.inside.executions) == (True, b"x", [])
    assert bench.instances() == ("web",)
    with pytest.raises(incus.NotFound, match="Project not found"):
        client(fake, "lab").instances()
    assert [asked.project for asked in fake.asked[-3:]] == [None, "bench", "lab"]


def test_an_operation_is_seen_only_by_the_connections_of_its_project() -> None:
    fake = lab()
    running(fake)
    created = OPERATION.format(1)
    with connection(fake, "bench") as own, connection(fake, "build") as other:
        # A wait names no project: Incus knows the operation's.
        answered = own.get(created)
        assert answered.status_code == 200
        assert answered.json()["metadata"]["description"] == "Creating instance"
        assert other.get(created).status_code == 403
        # An operation that has ended can be waited for again.
        assert own.get(created).json() == answered.json()
        for anyone in (own, other):
            assert anyone.get(OPERATION.format(9)).json()["error"] == "Operation not found"
    with connection(fake, "bench", "build") as both:
        assert both.get(OPERATION.format(2)).json()["metadata"]["description"] == (
            "Starting instance"
        )


def test_a_request_the_client_never_makes_fails_the_test() -> None:
    fake = lab()
    bench = running(fake)
    # Without the comparison, which would refuse the first two before the fake sees them
    # (they are no calls of the extract; the state changes are, with actions of Incus's).
    with httpx.Client(transport=fake.transport("bench"), base_url=URL) as http:
        with pytest.raises(AssertionError, match=r"FakeIncus has no GET /1\.0/images"):
            http.get("/1.0/images")
        with pytest.raises(AssertionError, match=r"FakeIncus has no PATCH /1\.0/instances/web"):
            http.patch("/1.0/instances/web")
        # A call that does not exist fails even when the test asked for no answer to it;
        # and what it asked for is gone with its block.
        with (
            pytest.raises(AssertionError, match=r"FakeIncus has no GET /1\.0/images"),
            fake.losing(httpx.ReadTimeout("no answer")),
        ):
            http.get("/1.0/images")
        for action in ("restart", "freeze", "unfreeze", None):
            with pytest.raises(AssertionError, match="FakeIncus has no state change"):
                http.put("/1.0/instances/web/state", params=BENCH, json={"action": action})
    assert bench.instance("web").status == "Running"
    # Each was kept all the same.
    assert [(asked.method, asked.path) for asked in fake.asked[-8:-1]] == [
        ("GET", "/1.0/images"),
        ("PATCH", "/1.0/instances/web"),
        ("GET", "/1.0/images"),
        *[("PUT", "/1.0/instances/web/state")] * 4,
    ]


def test_a_test_can_refuse_a_request_fail_an_operation_and_lose_an_answer() -> None:
    fake = lab()
    bench = client(fake)
    with (
        fake.refusing("POST", "/1.0/instances", 500, "storage pool is unavailable"),
        pytest.raises(incus.IncusError, match="500 storage pool is unavailable"),
    ):
        bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    with (
        fake.failing("Failed creating instance from image"),
        pytest.raises(incus.OperationFailed, match="Creating instance: Failed creating instance"),
    ):
        bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    # Nothing was done by the operation that failed.
    assert bench.instances() == ()
    with fake.losing(httpx.ReadTimeout("no answer")), pytest.raises(incus.Unreachable):
        bench.instances()
    # Each acted once, and each request was kept before it was refused or lost.
    bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    assert bench.instances() == ("web",)
    assert [(asked.method, asked.path) for asked in fake.asked[:5]] == [
        ("POST", "/1.0/instances"),
        ("POST", "/1.0/instances"),
        ("GET", OPERATION.format(1)),
        ("GET", "/1.0/instances"),
        ("GET", "/1.0/instances"),
    ]
    # The failure a test asked for comes before the one the operation has of its own.
    bench.start("web")
    with fake.failing("no reason"), pytest.raises(incus.OperationFailed, match="no reason"):
        bench.start("web")


def test_an_answer_to_a_wait_can_be_lost_or_refused_after_the_operation_was_done() -> None:
    fake = lab()
    body = {"name": "web", "source": {"type": "image", "fingerprint": FINGERPRINT}}
    with connection(fake, "bench") as http:
        announced = http.post("/1.0/instances", params=BENCH, json={**body, "profiles": [TARGET]})
        wait = announced.json()["operation"] + "/wait"
        assert wait == OPERATION.format(1)
        with fake.losing(httpx.ReadTimeout("no answer")), pytest.raises(httpx.ReadTimeout):
            http.get(wait)
        with fake.refusing("GET", wait, 500, "the database is locked"):
            refused = http.get(wait)
        assert (refused.status_code, refused.json()["error"]) == (500, "the database is locked")
        assert http.get(wait).json()["metadata"]["status"] == "Success"
    assert client(fake).instances() == ("web",)


def test_a_lever_nothing_came_to_fails_the_test() -> None:
    fake = lab()
    bench = running(fake)
    bench.stop("web")
    # The command is refused before any operation starts: the failure asked for is not used.
    with (
        pytest.raises(AssertionError, match="FakeIncus failed no operation: nothing in the block"),
        fake.failing("no reason"),
        pytest.raises(incus.IncusError, match="400 Instance is not running"),
    ):
        bench.exec("web", ["true"])
    # In the other order too, where the error the test waits for would hide it.
    with (
        pytest.raises(AssertionError, match=r"failed no operation: .*; the block ended with Incus"),
        pytest.raises(incus.IncusError),
        fake.failing("no reason"),
    ):
        bench.exec("web", ["true"])
    with (
        pytest.raises(AssertionError, match=r"FakeIncus refused no DELETE /1\.0/instances/web:"),
        fake.refusing("DELETE", "/1.0/instances/web", 500, "no reason"),
    ):
        bench.instances()
    # A request for a project the connection may not see is a 403, never the refusal.
    with (
        pytest.raises(AssertionError, match=r"FakeIncus refused no GET /1\.0/instances:"),
        fake.refusing("GET", "/1.0/instances", 500, "no reason"),
        pytest.raises(incus.Forbidden),
    ):
        client(fake, "bench", "build").instances()
    with (
        pytest.raises(AssertionError, match="FakeIncus lost no request:"),
        fake.losing(httpx.ReadTimeout("no answer")),
    ):
        pass
    # A lever that was not used is gone with its block.
    bench.start("web")
    bench.stop("web")
    bench.delete("web")
    assert bench.instances() == ()


def test_a_command_runs_only_in_a_running_instance_and_ends_as_scripted() -> None:
    fake = lab()
    bench = running(fake)
    web = fake.instance("bench", "web")
    command = ["systemctl", "is-active", "nginx.service"]
    web.inside.on(command, 3, b"inactive\n", b"warning\n")
    result = bench.exec("web", command, environment={"LANG": "C"}, cwd="/srv", user=33, group=34)
    assert result == incus.Result(3, b"inactive\n", b"warning\n")
    assert web.inside.executions == [Execution(tuple(command), (("LANG", "C"),), "/srv", 33, 34)]
    # The client took the two records of the output and removed them.
    assert web.logs == {}
    with pytest.raises(AssertionError, match="no answer scripted for the command"):
        bench.exec("web", ["systemctl", "restart", "nginx.service"])
    bench.stop("web")
    with pytest.raises(incus.IncusError, match="400 Instance is not running"):
        bench.exec("web", command)
    assert len(web.inside.executions) == 2


def test_a_command_s_output_is_kept_in_two_records_named_after_its_operation() -> None:
    fake = lab()
    running(fake)
    web = fake.instance("bench", "web")
    web.inside.on(["id"], 0, b"uid=0\n", b"")
    with connection(fake, "bench") as http:
        body = {"command": ["id"], **EXEC}
        announced = http.post("/1.0/instances/web/exec", params=BENCH, json=body).json()
        identifier = announced["metadata"]["id"]
        ended = http.get(f"/1.0/operations/{identifier}/wait").json()["metadata"]
        logs = f"/1.0/instances/web/logs/exec-output/exec_{identifier}"
        assert ended["metadata"] == {
            "return": 0,
            "output": {"1": f"{logs}.stdout", "2": f"{logs}.stderr"},
        }
        assert web.logs == {
            f"exec_{identifier}.stdout": b"uid=0\n",
            f"exec_{identifier}.stderr": b"",
        }
        assert http.get(f"{logs}.stdout", params=BENCH).content == b"uid=0\n"
        assert http.delete(f"{logs}.stdout", params=BENCH).status_code == 200
        # A record that is gone is not found, to read or to remove.
        assert http.get(f"{logs}.stdout", params=BENCH).status_code == 404
        assert http.delete(f"{logs}.stdout", params=BENCH).status_code == 404
        assert list(web.logs) == [f"exec_{identifier}.stderr"]
        # A command is run only with its output recorded and no terminal.
        for other in (
            {"record-output": False},
            {"interactive": True},
            {"wait-for-websocket": True},
        ):
            with pytest.raises(AssertionError, match="runs a command only with recorded output"):
                http.post("/1.0/instances/web/exec", params=BENCH, json={**body, **other})


@pytest.mark.parametrize(
    ("status", "text"), [(127, "Command not found"), (126, "Command not executable")]
)
def test_a_program_that_is_missing_or_cannot_be_run_fails_its_operation(
    status: int, text: str
) -> None:
    fake = lab()
    bench = running(fake)
    web = fake.instance("bench", "web")
    web.inside.on(["no-such-program"], status, b"", b"no-such-program\n")
    # For the client it is a result like any other, and the records are removed.
    assert bench.exec("web", ["no-such-program"]) == incus.Result(status, b"", b"no-such-program\n")
    assert web.logs == {}
    with connection(fake, "bench") as http:
        body = {"command": ["no-such-program"], **EXEC}
        announced = http.post("/1.0/instances/web/exec", params=BENCH, json=body).json()
        ended = http.get(announced["operation"] + "/wait").json()["metadata"]
    assert (ended["status"], ended["status_code"], ended["err"]) == ("Failure", 400, text)
    assert ended["metadata"]["return"] == status
    assert sorted(ended["metadata"]["output"]) == ["1", "2"]


def test_files_are_read_listed_written_and_deleted_through_the_client() -> None:
    fake = lab()
    bench = running(fake)
    inside = fake.instance("bench", "web").inside
    inside.mkdir("/etc/nginx")
    conf = "/etc/nginx/nginx.conf"
    bench.write_file("web", conf, b"events {}\n", uid=33, gid=34, mode=0o640)
    bench.write_file("web", conf, b"http {}\n", append=True)
    assert bench.read_file("web", conf) == incus.File(b"events {}\nhttp {}\n", 33, 34, 0o640)
    # A file that was there keeps its owner and its mode, whatever a write names.
    bench.write_file("web", conf, b"x", uid=1, gid=2, mode=0o600)
    assert bench.read_file("web", conf) == incus.File(b"x", 33, 34, 0o640)
    inside.link("/etc/nginx/link", "nginx.conf")
    assert bench.list_directory("web", "/etc/nginx") == ("link", "nginx.conf")
    with pytest.raises(incus.IncusError, match="a symlink, not a file"):
        bench.read_file("web", "/etc/nginx/link")
    with pytest.raises(incus.IncusError, match="a directory, not a file"):
        bench.read_file("web", "/etc/nginx")
    with pytest.raises(incus.IncusError, match="a file, not a directory"):
        bench.list_directory("web", conf)
    # What does not exist is a 404; an append makes no file.
    with pytest.raises(incus.NotFound, match="404 /etc/missing: no such file or directory"):
        bench.read_file("web", "/etc/missing")
    with pytest.raises(incus.NotFound, match="404 /missing/file: no such file or directory"):
        bench.write_file("web", "/missing/file", b"x")
    with pytest.raises(incus.NotFound, match="404 /etc/nginx/new: no such file or directory"):
        bench.write_file("web", "/etc/nginx/new", b"x", append=True)
    assert "/etc/nginx/new" not in inside.nodes
    # Any other error of a file is a 500.
    for call, text in (
        (lambda: bench.delete_file("web", "/etc/nginx"), "/etc/nginx: directory not empty"),
        (lambda: bench.write_file("web", "/etc/nginx", b"x"), "/etc/nginx: is a directory"),
        (lambda: bench.read_file("web", f"{conf}/x"), f"{conf}/x: not a directory"),
    ):
        with pytest.raises(incus.IncusError, match=f"500 {text}$") as refused:
            call()
        assert (type(refused.value), refused.value.status) == (incus.IncusError, 500)
    bench.delete_file("web", "/etc/nginx/link")
    bench.delete_file("web", conf)
    bench.delete_file("web", "/etc/nginx")
    with pytest.raises(incus.NotFound):
        bench.delete_file("web", "/etc/nginx")


def test_files_are_reached_through_links_as_linux_reaches_them() -> None:
    fake = lab()
    bench = running(fake)
    inside = fake.instance("bench", "web").inside
    inside.mkdir("/etc/nginx")
    inside.write("/etc/nginx/nginx.conf", b"events {}\n")
    inside.link("/conf", "/etc/nginx")
    inside.link("/etc/nginx/site", "../nginx/nginx.conf")
    inside.link("/etc/nginx/later", "/etc/nginx/later.conf")
    # A link in the middle of a path is followed, to read, to write and to delete.
    assert bench.read_file("web", "/conf/nginx.conf").content == b"events {}\n"
    bench.write_file("web", "/conf/new", b"x")
    assert inside.nodes["/etc/nginx/new"].content == b"x"
    bench.delete_file("web", "/conf/new")
    assert "/etc/nginx/new" not in inside.nodes
    # A link at the end is answered as one: its content is where it leads.
    with pytest.raises(incus.IncusError, match="/conf: a symlink, not a directory"):
        bench.list_directory("web", "/conf")
    files = "/1.0/instances/web/files"
    with connection(fake, "bench") as http:
        answered = http.get(files, params={"path": "/etc/nginx/site", **BENCH})
        assert (answered.headers["X-Incus-type"], answered.content) == (
            "symlink",
            b"/etc/nginx/nginx.conf",
        )
        assert answered.headers["X-Incus-modified"] == "2026-01-01 00:00:00 +0000 UTC"
        # Incus joins a relative target with the directory of the path it was asked for,
        # as text: asked through another link, the same link leads elsewhere, here nowhere.
        assert http.get(files, params={"path": "/conf/site", **BENCH}).status_code == 404
        inside.mkdir("/nginx")
        inside.write("/nginx/nginx.conf", b"")
        through = http.get(files, params={"path": "/conf/site", **BENCH})
        assert through.content == b"/nginx/nginx.conf"
        inside.delete("/nginx/nginx.conf")
        inside.delete("/nginx")
        # A link that leads nowhere has no such content.
        assert http.get(files, params={"path": "/conf/later", **BENCH}).status_code == 404
    # A write goes through it, and makes the file a link leads to when it is missing.
    bench.write_file("web", "/conf/site", b"http {}\n")
    bench.write_file("web", "/conf/later", b"y")
    assert inside.nodes["/etc/nginx/nginx.conf"].content == b"http {}\n"
    assert inside.nodes["/etc/nginx/later.conf"].content == b"y"
    # A delete takes the link itself.
    bench.delete_file("web", "/conf/site")
    assert bench.list_directory("web", "/etc/nginx") == ("later", "later.conf", "nginx.conf")


def test_a_file_s_mode_is_read_from_a_write_as_incus_reads_it() -> None:
    fake = lab()
    running(fake)
    inside = fake.instance("bench", "web").inside
    files = "/1.0/instances/web/files"
    with connection(fake, "bench") as http:
        # A leading zero makes it octal; without one it is decimal; nine bits are kept.
        for number, (header, mode) in enumerate(
            [("0640", 0o640), ("640", 0o200), ("07777", 0o777)]
        ):
            written = http.post(
                files,
                params={"path": f"/file{number}", **BENCH},
                content=b"x",
                headers={"X-Incus-mode": header, "X-Incus-uid": "33", "X-Incus-gid": "34"},
            )
            assert written.status_code == 200
            assert inside.nodes[f"/file{number}"].mode == mode
        assert (inside.nodes["/file0"].uid, inside.nodes["/file0"].gid) == (33, 34)
        read = http.get(files, params={"path": "/file1", **BENCH}).headers
        assert (read["X-Incus-mode"], read["X-Incus-uid"], read["X-Incus-type"]) == (
            "0200",
            "33",
            "file",
        )
        for headers in (
            {"X-Incus-type": "directory"},
            {"X-Incus-type": "symlink"},
            {"X-Incus-write": "truncate"},
        ):
            with pytest.raises(AssertionError, match="writes regular files only"):
                http.post(files, params={"path": "/other", **BENCH}, content=b"x", headers=headers)
        # A write names its owner and its mode, and every files call an absolute path.
        for headers in ({"X-Incus-uid": "0", "X-Incus-gid": "0"}, {"X-Incus-mode": "0644"}, {}):
            with pytest.raises(AssertionError, match="with its owner and its mode only"):
                http.post(files, params={"path": "/other", **BENCH}, content=b"x", headers=headers)
        for send in (http.get, http.delete):
            for path in ("other", ""):
                with pytest.raises(AssertionError, match="by an absolute path only"):
                    send(files, params={"path": path, **BENCH})
    assert "/other" not in inside.nodes


@pytest.mark.parametrize(
    ("driver", "error", "text"),
    [("lvm", "ENOSPC", "no space left on device"), ("btrfs", "EDQUOT", "disk quota exceeded")],
)
def test_the_state_reports_the_root_disk_and_a_full_one_refuses_a_write(
    driver: str, error: str, text: str
) -> None:
    fake = lab(driver)
    bench = running(fake)
    inside = fake.instance("bench", "web").inside
    assert (inside.driver, inside.size, inside.usage) == (driver, ROOT_SIZE, IMAGE_USED)

    def fallocate(target: Inside, asked: Execution) -> Ended | None:
        if asked.argv[:2] != ("fallocate", "--length"):
            return None
        full = target.allocate(asked.argv[3], int(asked.argv[2]))
        return Ended(1, b"", f"fallocate: {full}".encode()) if full else Ended()

    inside.answer(fallocate)
    free = ROOT_SIZE - IMAGE_USED
    assert bench.exec("web", ["fallocate", "--length", str(free - 10), "/fill"]).exit_code == 0
    assert bench.state("web").disk == {"root": incus.Disk(ROOT_SIZE - 10, ROOT_SIZE)}
    assert bench.exec("web", ["fallocate", "--length", "11", "/more"]) == incus.Result(
        1, b"", f"fallocate: {error}".encode()
    )
    bench.write_file("web", "/nine", b"x" * 9)
    # A write that does not fit is refused, and leaves what fitted.
    with pytest.raises(incus.IncusError, match=f"500 /two: {text}$"):
        bench.write_file("web", "/two", b"xy")
    assert inside.nodes["/two"].content == b"x"
    assert bench.state("web").disk["root"].usage == ROOT_SIZE
    # A deleted file gives its bytes back.
    bench.delete_file("web", "/fill")
    assert bench.state("web").disk["root"].usage == IMAGE_USED + 10


def test_a_storage_driver_the_stand_in_has_no_model_of_is_refused_at_once() -> None:
    with pytest.raises(ValueError, match="not a storage pool driver of the stand-in"):
        FakeIncus(driver="zfs")
    assert (FakeIncus().driver, FakeIncus(driver="btrfs").driver) == ("lvm", "btrfs")
