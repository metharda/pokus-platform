"""An Incus in memory, for tests: it answers the client through its own transport.

``FakeIncus`` keeps projects, profiles, images and instances and answers the
requests of ``potka_incus.client.Incus`` in the form Incus answers them, so
a test runs the client's real code. It knows the client's calls and no
other: a request for anything else, or one of these calls in a form the
client never sends, fails the test. What is inside an instance (files, the
root disk, commands) is ``potka_incus.inside``.

A test sets the lab up (:meth:`FakeIncus.profile`, :meth:`FakeIncus.image`),
takes a client that is confined to some projects as a restricted
certificate is (:meth:`FakeIncus.client`), and scripts what is inside an
instance (:attr:`FakeInstance.inside`). Within a block it can have the next
request refused, the next operation fail or the next request get no answer
(:meth:`FakeIncus.refusing`, :meth:`FakeIncus.failing`,
:meth:`FakeIncus.losing`); a block in which that did not happen fails the
test. Every request is kept (:attr:`FakeIncus.asked`).

An image's record can be rewritten by a client of the project in Incus;
here a test rewrites it directly (:class:`Image`). As in Incus, an image's
properties become ``image.*`` keys of the instances made from it, and an
instance whose image says it requires nesting does not start without it.

The texts of the refusals are this module's own: Incus's are not known to
it, and no test should read more from them than which refusal it was.
"""

from __future__ import annotations

import itertools
import json
import posixpath
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from potka_incus.client import Incus
from potka_incus.inside import Execution, Inside, Problem
from potka_incus.units import parse_size

URL = "https://incus.invalid"
CREATED = "2026-01-01T00:00:00Z"
# A file's time of change, in the form Incus writes it in a header.
MODIFIED = "2026-01-01 00:00:00 +0000 UTC"
# How Incus reads a boolean of the configuration.
TRUE, FALSE_OR_EMPTY = ("true", "1", "yes", "on"), ("", "false", "0", "no", "off")
NEEDS_NESTING = (
    "The image used by this instance requires nesting."
    " Please set security.nesting=true on the instance"
)
RUNNING, STOPPED = ("Running", 103), ("Stopped", 102)
CHANGES = {"start": "Starting instance", "stop": "Stopping instance"}
# The status of a file operation's error, as Incus maps errors: what does not exist is
# a 404, anything else a 500.
STATUSES = {"ENOENT": 404}
# The exit statuses with which Incus fails a command's operation, and its texts for them.
NOT_RUN = {126: "Command not executable", 127: "Command not found"}
INSTANCE = r"/1\.0/instances/(?P<name>[^/]+)"
ROUTES = [
    (method, re.compile(pattern), handler)
    for method, pattern, handler in (
        ("GET", r"/1\.0/instances", "_list"),
        ("POST", r"/1\.0/instances", "_create"),
        ("GET", INSTANCE, "_record"),
        ("DELETE", INSTANCE, "_delete"),
        ("GET", INSTANCE + "/state", "_state"),
        ("PUT", INSTANCE + "/state", "_change"),
        ("POST", INSTANCE + "/exec", "_exec"),
        ("GET", INSTANCE + r"/logs/exec-output/(?P<file>[^/]+)", "_log"),
        ("DELETE", INSTANCE + r"/logs/exec-output/(?P<file>[^/]+)", "_drop_log"),
        ("GET", INSTANCE + "/files", "_read"),
        ("POST", INSTANCE + "/files", "_write"),
        ("DELETE", INSTANCE + "/files", "_remove"),
        ("GET", r"/1\.0/operations/(?P<id>[^/]+)/wait", "_wait"),
    )
]


@dataclass
class Profile:
    """A profile: configuration and devices its instances get."""

    config: dict[str, str] = field(default_factory=dict)
    devices: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Image:
    """An image and its record, which a test may rewrite.

    Attributes:
        fingerprint: Its fingerprint, which never changes.
        aliases: The aliases that name it.
        kind: ``container`` or ``virtual-machine``.
        properties: Its properties: ``image.*`` keys of an instance made from it.
        profiles: The profiles of an instance created without a profile list.
        used: The bytes its files take on an instance's root disk.
    """

    fingerprint: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "container"
    properties: dict[str, str] = field(default_factory=dict)
    profiles: list[str] = field(default_factory=lambda: ["default"])
    used: int = 0


@dataclass
class FakeInstance:
    """An instance.

    Attributes:
        name: Its name.
        project: Its project.
        kind: ``container`` or ``virtual-machine``.
        profiles: Its profiles, in order.
        config: Its own configuration.
        devices: Its own devices.
        inside: Its files, root disk and commands.
        running: Whether it runs.
        logs: The recorded output of its commands, by file name.
    """

    name: str
    project: str
    kind: str
    profiles: list[str]
    config: dict[str, str]
    devices: dict[str, dict[str, str]]
    inside: Inside
    running: bool = False
    logs: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class Asked:
    """A request FakeIncus received.

    Attributes:
        method: Its method.
        path: Its path.
        project: The project it names, or None.
        body: Its JSON body, its raw body, or None.
    """

    method: str
    path: str
    project: str | None
    body: Any


class _Failure(Exception):
    """An operation that ends in failure: its text, and what its record still holds."""

    def __init__(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(text)
        self.text, self.metadata = text, metadata or {}


def _answer(http: int, kind: str, **more: Any) -> httpx.Response:
    """An answer of Incus with the HTTP status given: the seven keys of every one."""
    body = {
        "type": kind,
        "status": "",
        "status_code": 0,
        "operation": "",
        "error_code": 0,
        "error": "",
        "metadata": None,
    }
    return httpx.Response(http, json={**body, **more})


def _sync(metadata: Any = None) -> httpx.Response:
    return _answer(200, "sync", status="Success", status_code=200, metadata=metadata)


def _error(status: int, text: str) -> httpx.Response:
    return _answer(status, "error", error_code=status, error=text)


class FakeIncus:
    """An Incus with projects, profiles, images and instances, all in memory."""

    def __init__(self, projects: Sequence[str] = ("bench", "build"), driver: str = "lvm") -> None:
        """Start an empty Incus.

        Args:
            projects: Its projects.
            driver: The driver of its storage pool, ``lvm`` (a thin pool:
                an instance has a disk of its own) or ``btrfs`` (an
                instance's limit is a quota).
        """
        Inside(driver=driver)  # refuses a driver the stand-in does not know
        self.projects = tuple(projects)
        self.driver = driver
        self.profiles: dict[tuple[str, str], Profile] = {}
        self.images: dict[str, Image] = {}
        self.instances: dict[tuple[str, str], FakeInstance] = {}
        self.asked: list[Asked] = []
        self._operations: dict[str, dict[str, Any]] = {}
        self._refusals: list[dict[str, Any]] = []
        self._failures: list[dict[str, Any]] = []
        self._lost: list[dict[str, Any]] = []
        self._serial = itertools.count(1)

    def profile(
        self,
        project: str,
        name: str,
        config: dict[str, str] | None = None,
        devices: dict[str, dict[str, str]] | None = None,
    ) -> Profile:
        """Add a profile to a project."""
        self.profiles[project, name] = Profile(dict(config or {}), dict(devices or {}))
        return self.profiles[project, name]

    def image(
        self,
        fingerprint: str,
        *aliases: str,
        kind: str = "container",
        used: int = 0,
        properties: dict[str, str] | None = None,
    ) -> Image:
        """Add an image to the image store all projects share."""
        made = Image(fingerprint, list(aliases), kind, dict(properties or {}), used=used)
        self.images[fingerprint] = made
        return made

    def instance(self, project: str, name: str) -> FakeInstance:
        """An instance of a project (KeyError when there is none)."""
        return self.instances[project, name]

    @contextmanager
    def _once(self, levers: list[dict[str, Any]], what: str, **lever: Any) -> Iterator[None]:
        levers.append(lever)
        nothing = f"FakeIncus {what}: nothing in the block came to it"
        try:
            yield
        except Exception as exc:
            # A block that ends in an error has not used its lever either: the error is
            # another one than the test asked for.
            if any(left is lever for left in levers):
                ended = f"{type(exc).__name__}: {exc}"
                raise AssertionError(f"{nothing}; the block ended with {ended}") from exc
            raise
        finally:
            # Used or not, it ends with its block.
            unused = any(left is lever for left in levers)
            levers[:] = [left for left in levers if left is not lever]
        if unused:
            raise AssertionError(nothing)

    def refusing(
        self, method: str, path: str, status: int, text: str
    ) -> AbstractContextManager[None]:
        """Within the block, refuse the next request of METHOD and PATH with STATUS.

        Whatever its project and its query. Only a request Incus would
        have answered is refused: one for a project or an operation the
        connection may not see is a 403 as always, and one for a project
        or an operation that does not exist a 404. The block fails the
        test when no request came that was refused so.
        """
        what = f"refused no {method} {path}"
        return self._once(self._refusals, what, method=method, path=path, status=status, text=text)

    def failing(self, text: str) -> AbstractContextManager[None]:
        """Within the block, let the next operation end in failure, with nothing done.

        The block fails the test when no operation was started in it.
        """
        return self._once(self._failures, "failed no operation", text=text)

    def losing(self, exception: Exception) -> AbstractContextManager[None]:
        """Within the block, give the next request no answer: the transport raises EXCEPTION.

        The block fails the test when no request came.
        """
        return self._once(self._lost, "lost no request", exception=exception)

    def created(self) -> list[Any]:
        """The body of every request to create an instance, the refused ones too."""
        return [a.body for a in self.asked if (a.method, a.path) == ("POST", "/1.0/instances")]

    def transport(self, *projects: str) -> httpx.MockTransport:
        """A connection that sees only PROJECTS, as a restricted certificate does.

        Their instances, and the operations started in them.
        """
        return httpx.MockTransport(lambda request: self._handle(request, projects))

    def client(self, project: str) -> Incus:
        """A client of PROJECT whose certificate is restricted to it."""
        return Incus(URL, project, transport=self.transport(project))

    def _handle(self, request: httpx.Request, allowed: Sequence[str]) -> httpx.Response:
        raw = request.content
        typed = "json" in request.headers.get("content-type", "")
        project = request.url.params.get("project")
        path = request.url.path
        self.asked.append(
            Asked(
                request.method, path, project, (json.loads(raw) if typed else raw) if raw else None
            )
        )
        found = [
            (handler, match)
            for method, route, handler in ROUTES
            if method == request.method and (match := route.fullmatch(path))
        ]
        if not found:
            raise AssertionError(
                f"FakeIncus has no {request.method} {path}: the client makes no such call"
            )
        if self._lost:
            raise self._lost.pop(0)["exception"]
        handler, match = found[0]
        if "id" in match.groupdict():
            return self._wait(request, match.group("id"), allowed)
        project = project or "default"
        if project not in allowed:
            return _error(403, "not authorized")
        if project not in self.projects:
            return _error(404, "Project not found")
        refused = self._refused(request)
        if refused is not None:
            return refused
        if "name" not in match.groupdict():
            answer: httpx.Response = getattr(self, handler)(project, request)
            return answer
        instance = self.instances.get((project, match.group("name")))
        if instance is None:
            return _error(404, "Instance not found")
        answer = getattr(self, handler)(
            instance, request, **{"file": match.groupdict().get("file")}
        )
        return answer

    def _operation(
        self, project: str, description: str, apply: Callable[[str], dict[str, Any] | None]
    ) -> httpx.Response:
        """Announce an operation; it has ended, in success or failure, when it is waited for.

        APPLY gets the operation's identifier and does the work; it raises
        ``_Failure`` when the operation fails by itself. A failure the test
        asked for comes first, and then nothing is done.
        """
        identifier = str(uuid.UUID(int=next(self._serial)))
        record: dict[str, Any] = {"id": identifier, "class": "task", "description": description}
        try:
            if self._failures:
                raise _Failure(self._failures.pop(0)["text"])
            done = apply(identifier) or {}
            ended = {"status": "Success", "status_code": 200, "err": "", "metadata": done}
        except _Failure as failure:
            left = failure.metadata
            ended = {"status": "Failure", "status_code": 400, "err": failure.text, "metadata": left}
        self._operations[identifier] = {"project": project, "record": {**record, **ended}}
        return _answer(
            202,
            "async",
            status="Operation created",
            status_code=100,
            operation=f"/1.0/operations/{identifier}",
            metadata={**record, "status": "Running", "status_code": 103},
        )

    def _refused(self, request: httpx.Request) -> httpx.Response | None:
        """The refusal a test asked for this request, once; None when it asked for none."""
        for lever in self._refusals:
            if (lever["method"], lever["path"]) == (request.method, request.url.path):
                self._refusals[:] = [left for left in self._refusals if left is not lever]
                return _error(lever["status"], lever["text"])
        return None

    def _wait(
        self, request: httpx.Request, identifier: str, allowed: Sequence[str]
    ) -> httpx.Response:
        # Every operation is kept; Incus forgets one five seconds after its end.
        operation = self._operations.get(identifier)
        if operation is None:
            return _error(404, "Operation not found")
        # An operation is its project's: no request names the project, Incus knows it.
        if operation["project"] not in allowed:
            return _error(403, "not authorized")
        refused = self._refused(request)
        return refused if refused is not None else _sync(operation["record"])

    def _expanded(self, instance: FakeInstance) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
        config: dict[str, str] = {}
        devices: dict[str, dict[str, str]] = {}
        for name in instance.profiles:
            profile = self.profiles[instance.project, name]
            config.update(profile.config)
            devices.update({device: dict(keys) for device, keys in profile.devices.items()})
        return {**config, **instance.config}, {**devices, **instance.devices}

    def _root(self, instance: FakeInstance) -> tuple[str, dict[str, str]] | None:
        """The root disk device and its name: a disk at ``/`` without a source, as Incus has it."""
        for name, device in self._expanded(instance)[1].items():
            if (device.get("type"), device.get("path")) == ("disk", "/") and not device.get(
                "source"
            ):
                return name, device
        return None

    def _list(self, project: str, request: httpx.Request) -> httpx.Response:
        return _sync(
            [f"/1.0/instances/{name}" for owner, name in sorted(self.instances) if owner == project]
        )

    def _create(self, project: str, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        source = body.get("source") or {}
        if source.get("type") != "image":
            raise AssertionError("FakeIncus makes an instance from an image only")
        if "fingerprint" in source:
            image = self.images.get(source["fingerprint"])
        else:
            image = next(
                (i for i in self.images.values() if source.get("alias") in i.aliases), None
            )
        given = body.get("config") or {}
        if any(key.startswith(("image.", "volatile.")) for key in given):
            raise AssertionError("FakeIncus takes no image.* or volatile.* key in a creation")
        if (project, body.get("name")) in self.instances:
            return _error(500, f'Instance "{body.get("name")}" already exists')
        if image is None:
            return _error(404, "Image not found")
        # Without a type Incus asks for a container, whatever the image is.
        if (body.get("type") or "container") != image.kind:
            raise AssertionError("FakeIncus makes no instance of another kind than its image")
        profiles = body["profiles"] if body.get("profiles") is not None else image.profiles
        if any((project, profile) not in self.profiles for profile in profiles):
            return _error(404, "Profile not found")

        def apply(_: str) -> None:
            config = {
                **{f"image.{key}": value for key, value in image.properties.items()},
                **given,
                "volatile.base_image": image.fingerprint,
            }
            devices = {name: dict(keys) for name, keys in (body.get("devices") or {}).items()}
            made = FakeInstance(
                body["name"], project, image.kind, list(profiles), config, devices, Inside()
            )
            root = self._root(made)
            size = root[1].get("size") if root else None
            made.inside = Inside(parse_size(size) if size else None, self.driver, base=image.used)
            self.instances[project, made.name] = made

        return self._operation(project, "Creating instance", apply)

    def _record(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        config, devices = self._expanded(instance)
        status, code = RUNNING if instance.running else STOPPED
        return _sync(
            {
                "name": instance.name,
                "description": "",
                "status": status,
                "status_code": code,
                "type": instance.kind,
                "architecture": "x86_64",
                "ephemeral": False,
                "stateful": False,
                "project": instance.project,
                "location": "none",
                "created_at": CREATED,
                "last_used_at": CREATED,
                "profiles": instance.profiles,
                "config": instance.config,
                "devices": instance.devices,
                "expanded_config": config,
                "expanded_devices": devices,
            }
        )

    def _state(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        status, code = RUNNING if instance.running else STOPPED
        inside = instance.inside
        root = self._root(instance)
        return _sync(
            {
                "status": status,
                "status_code": code,
                "pid": 4242 if instance.running else 0,
                "processes": 12 if instance.running else 0,
                "cpu": {"usage": 0},
                "memory": {
                    "usage": 0,
                    "usage_peak": 0,
                    "total": 0,
                    "swap_usage": 0,
                    "swap_usage_peak": 0,
                },
                "network": None,
                "disk": {root[0]: {"usage": inside.usage, "total": inside.size or 0}}
                if root
                else None,
            }
        )

    def _change(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        action = json.loads(request.content).get("action")
        if action not in CHANGES:
            raise AssertionError(f"FakeIncus has no state change {action!r}: the client asks none")

        def apply(_: str) -> None:
            if instance.running == (action == "start"):
                state = "running" if instance.running else "stopped"
                raise _Failure(f"The instance is already {state}")
            # Incus reads this one property of the image when an instance starts.
            requires = instance.config.get("image.requirements.nesting", "").lower() in TRUE
            nesting = self._expanded(instance)[0].get("security.nesting", "").lower()
            if action == "start" and requires and nesting in FALSE_OR_EMPTY:
                raise _Failure(NEEDS_NESTING)
            instance.running = action == "start"

        return self._operation(instance.project, CHANGES[action], apply)

    def _delete(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        if instance.running:
            return _error(400, "Instance is running")

        def apply(_: str) -> None:
            del self.instances[instance.project, instance.name]

        return self._operation(instance.project, "Deleting instance", apply)

    def _exec(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        body = json.loads(request.content)
        recorded = (
            body.get("record-output"),
            body.get("interactive"),
            body.get("wait-for-websocket"),
        )
        if recorded != (True, False, False):
            raise AssertionError("FakeIncus runs a command only with recorded output")
        if not instance.running:
            return _error(400, "Instance is not running")

        def apply(identifier: str) -> dict[str, Any]:
            environment = tuple(sorted((body.get("environment") or {}).items()))
            user, group = body.get("user", 0), body.get("group", 0)
            asked = Execution(tuple(body["command"]), environment, body.get("cwd"), user, group)
            ended = instance.inside.run(asked)
            # The two records are named after the operation.
            base = f"exec_{identifier}"
            instance.logs.update({f"{base}.stdout": ended.stdout, f"{base}.stderr": ended.stderr})
            logs = f"/1.0/instances/{instance.name}/logs/exec-output/{base}"
            metadata = {
                "return": ended.exit_code,
                "output": {"1": f"{logs}.stdout", "2": f"{logs}.stderr"},
            }
            if ended.exit_code in NOT_RUN:
                # Incus fails the operation of such a command (its container driver's
                # rule, taken for every instance here); its record holds the rest.
                raise _Failure(NOT_RUN[ended.exit_code], metadata)
            return metadata

        return self._operation(instance.project, "Executing command", apply)

    def _log(self, instance: FakeInstance, request: httpx.Request, file: str) -> httpx.Response:
        if file not in instance.logs:
            return _error(404, "Log file not found")
        return httpx.Response(200, content=instance.logs[file])

    def _drop_log(
        self, instance: FakeInstance, request: httpx.Request, file: str
    ) -> httpx.Response:
        return (
            _sync()
            if instance.logs.pop(file, None) is not None
            else _error(404, "Log file not found")
        )

    def _problem(self, problem: Problem) -> httpx.Response:
        return _error(STATUSES.get(problem.error, 500), str(problem))

    @staticmethod
    def _path(request: httpx.Request) -> str:
        """The absolute path a files call names; the client sends no other."""
        path = str(request.url.params.get("path", ""))
        if not path.startswith("/"):
            raise AssertionError("FakeIncus reads and writes files by an absolute path only")
        return path

    def _read(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        inside = instance.inside
        asked = self._path(request)
        try:
            # The last part of the path is not followed: a link is answered as one.
            real, node = inside.lookup(asked)
            headers = {
                "X-Incus-type": node.kind,
                "X-Incus-uid": str(node.uid),
                "X-Incus-gid": str(node.gid),
                "X-Incus-mode": f"{node.mode & 0o777:04o}",
                "X-Incus-modified": MODIFIED,
            }
            if node.kind == "directory":
                listing = _sync(list(inside.listing(real))).json()
                return httpx.Response(200, json=listing, headers=headers)
            if node.kind == "symlink":
                # A link's content is where it leads. Incus joins a relative target with
                # the directory of the path it was asked for, as text, and resolves that.
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(asked), node.content.decode())
                )
                return httpx.Response(
                    200, content=inside.realpath(target).encode(), headers=headers
                )
        except Problem as problem:
            return self._problem(problem)
        return httpx.Response(200, content=node.content, headers=headers)

    def _write(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        headers = request.headers
        kind, how = headers.get("X-Incus-type", "file"), headers.get("X-Incus-write", "overwrite")
        if kind != "file" or how not in ("overwrite", "append"):
            raise AssertionError("FakeIncus writes regular files only, whole or appended")
        if not all(name in headers for name in ("X-Incus-uid", "X-Incus-gid", "X-Incus-mode")):
            raise AssertionError("FakeIncus writes a file with its owner and its mode only")
        # Incus reads the mode by its prefix (a leading zero is octal, else decimal) and
        # keeps the nine permission bits.
        mode = headers["X-Incus-mode"]
        try:
            instance.inside.write(
                self._path(request),
                request.content,
                uid=int(headers["X-Incus-uid"]),
                gid=int(headers["X-Incus-gid"]),
                mode=int(mode, 8 if mode.startswith("0") else 10) & 0o777,
                append=how == "append",
            )
        except Problem as problem:
            return self._problem(problem)
        return _sync()

    def _remove(self, instance: FakeInstance, request: httpx.Request, **_: Any) -> httpx.Response:
        try:
            instance.inside.delete(self._path(request))
        except Problem as problem:
            return self._problem(problem)
        return _sync()
