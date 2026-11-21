"""The client of the Incus API: one project, one certificate, typed calls.

A service reaches Incus over HTTPS with its own restricted client
certificate (``<cert_dir>/<account>/client.crt``, ``client.key``) and
trusts exactly the server certificate beside them (``server.crt``): that
file is the only trust anchor, and the names in it are not compared with
the address. On a lab the address is on loopback; the client itself only
asks for ``https``. Every call that takes one names the client's project.
The client reads nothing from the environment (no proxy, no TLS setting).

An instance is created from an image's fingerprint and an explicit list of
profiles; there is no way to name an alias here, because an image's record
(its aliases, properties and profiles) can be rewritten by any client of
the project and so decides nothing the client asks for. (Incus itself
reads some of an image's properties when an instance starts: one that
says it requires nesting does not start without it. The caller sees that
as a failed operation.) An instance's own configuration and devices win
over its profiles', so the client passes on only what the platform gives
an instance (:data:`CONFIG_KEYS`, :data:`DEVICE_KEYS`) and refuses
anything else: every disk too. The one device it passes on is a NIC under
one fixed name, so it can take the place of no other device of a profile.

Incus answers at once (``sync``), with an operation to wait for
(``async``), or with an error. The client waits for an operation's end and
turns a failed one into an exception, so a call that returns has been
carried out. A wait that ends before its operation is an exception too,
and Incus goes on with the operation. An answer that is not in the form of
Incus's answers is an error, never an empty result.

A command is run from a list of arguments, never from a string: Incus
executes the list as it is, without a shell, and the client refuses
anything else before it sends a request. The command has no input and no
terminal; Incus records its output, and when the command has ended the
client fetches both streams and removes the records. A command that
outlives its wait cannot be stopped through Incus's API, and its records
stay on the Incus host. A file is read and written through Incus's files
API by an absolute path in its normal form.
"""

from __future__ import annotations

import posixpath
import re
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
from potka_core.endpoints import Incus as Endpoints

from potka_incus.units import parse_size

TIMEOUT = 30.0
OPERATION_TIMEOUT = 120
# Incus's status codes of an operation that has ended (shared/api/status_code.go).
SUCCESS = 200
# An instance's name is a host name: letters, digits and hyphens, no hyphen at
# either end, not a number, at most 63 characters (Incus's own rule).
NAME_RE = re.compile(r"(?!-)(?![0-9]+$)[A-Za-z0-9-]{1,63}(?<!-)")
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
OPERATION_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
KINDS = ("container", "virtual-machine")
# A service's account, which names its certificate directory.
ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
# What the platform gives an instance beside its profiles. An instance's own configuration
# and devices win over its profiles', so only these are passed on: whether it nests, its
# CPUs and its memory, the one variable of its environment and its NIC on its run's
# network. Each replaces what a profile says of the same key or device, and nothing else
# can. Both lists are whole: a key that only begins like one of them is refused, and so
# is every device of another type, a disk and the root disk among them.
CONFIG_KEYS = (
    "security.nesting",
    "environment.OTEL_RESOURCE_ATTRIBUTES",
    "limits.cpu",
    "limits.memory",
)
DEVICE_KEYS = {"nic": ("type", "name", "network")}
# The name of the NIC's device, and of its interface in the instance when that is said. A
# device replaces a profile's device of the same name whole, whatever the two are: under
# a name of its caller's choice a NIC could take the place of a profile's disk.
NIC_NAME = "eth0"
# A run's network, as the infrastructure names one.
RUN_NETWORK_RE = re.compile(r"r-[a-z0-9]{12}")
FORM = "an answer that is not in Incus's form"
EXEC_TIMEOUT = 60
# Incus fails the operation of a command that ended with one of these statuses
# ("Command not executable", "Command not found"); the status is in its record all the same.
NOT_RUN = (126, 127)
# The two streams of a command, as the operation's record numbers them.
STREAMS = {"1": "stdout", "2": "stderr"}
# The files API sets and reports the nine permission bits and no other.
MODE_MASK = 0o777
OWNER_RE = re.compile(r"[0-9]+")
MODE_RE = re.compile(r"[0-7]{4}")
# The largest user or group ID; the number above it means "leave as it is" to chown.
MAX_ID = 2**32 - 2


class IncusError(Exception):
    """Incus refused a call or could not carry it out.

    Attributes:
        status: The HTTP status, or None when there was no answer.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        """Keep the message and the status."""
        super().__init__(message)
        self.status = status


class NotFound(IncusError):
    """What the call names does not exist (or is not the certificate's to see)."""


class Forbidden(IncusError):
    """The certificate may not make the call."""


class Unreachable(IncusError):
    """No answer: the connection failed or timed out."""


class WrongKind(IncusError):
    """A path names another kind of thing than the call asked for.

    Attributes:
        found: What Incus says is there: ``file``, ``directory`` or
            ``symlink``; empty when its answer named no kind.
    """

    def __init__(self, message: str, found: str) -> None:
        """Keep the message and the kind found."""
        super().__init__(message)
        self.found = found


class OperationFailed(IncusError):
    """An operation ended in failure or was cancelled.

    Attributes:
        operation: The operation's record at its end, as Incus gave it.
    """

    def __init__(self, message: str, operation: Mapping[str, Any]) -> None:
        """Keep the message and the operation's record."""
        super().__init__(message)
        self.operation = operation


class OutputLost(IncusError):
    """A command ended, and a record of its output could not be fetched or removed.

    Attributes:
        exit_code: The command's exit status: it ran, whatever became of its output.
    """

    def __init__(self, message: str, status: int | None, exit_code: int) -> None:
        """Keep the message, the status of the first failure and the exit status."""
        super().__init__(message, status)
        self.exit_code = exit_code


class OperationTimeout(IncusError):
    """An operation had not ended when the wait for it did; Incus goes on with it.

    Attributes:
        operation: The operation's record when the wait ended; its ``id`` names it.
    """

    def __init__(self, message: str, operation: Mapping[str, Any]) -> None:
        """Keep the message and the operation's record."""
        super().__init__(message)
        self.operation = operation


@dataclass(frozen=True)
class Certificate:
    """A service's client certificate and the server certificate it trusts.

    Attributes:
        client: The client certificate.
        key: Its private key.
        server: Incus's own certificate.
    """

    client: Path
    key: Path
    server: Path

    @classmethod
    def of(cls, directory: Path) -> Certificate:
        """The three files of a service's certificate directory."""
        return cls(directory / "client.crt", directory / "client.key", directory / "server.crt")

    def context(self) -> ssl.SSLContext:
        """The TLS settings: this client certificate, and only that server certificate.

        Returns:
            A context that requires the server's certificate to be (or to be
            signed by) the pinned one and does not compare host names.
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cafile=self.server)
        context.load_cert_chain(self.client, self.key)
        return context


@dataclass(frozen=True)
class Instance:
    """An instance's record, as Incus gives it.

    Attributes:
        name: Its name.
        status: Its status in words (``Running``).
        status_code: Its status as Incus's code.
        kind: ``container`` or ``virtual-machine``.
        profiles: Its profiles, in order.
        config: Its own configuration.
        devices: Its own devices.
        expanded_config: Its configuration with its profiles' applied.
        expanded_devices: Its devices with its profiles' applied.
    """

    name: str
    status: str
    status_code: int
    kind: str
    profiles: tuple[str, ...]
    config: Mapping[str, str]
    devices: Mapping[str, Mapping[str, str]]
    expanded_config: Mapping[str, str]
    expanded_devices: Mapping[str, Mapping[str, str]]

    @property
    def base_image(self) -> str | None:
        """The fingerprint of the image the instance was created from."""
        return self.expanded_config.get("volatile.base_image")

    @property
    def root_device(self) -> str | None:
        """The name of the root disk device, or None without one.

        Incus's own rule, whatever the device is called: a disk at ``/``
        that has no source.
        """
        for name, device in self.expanded_devices.items():
            if (device.get("type"), device.get("path")) == ("disk", "/") and not device.get(
                "source"
            ):
                return name
        return None

    @property
    def root_size(self) -> int | None:
        """The configured size of the root disk in bytes, or None when it has none.

        Raises:
            ValueError: The size is not in a form Incus writes.
        """
        name = self.root_device
        size = self.expanded_devices[name].get("size") if name is not None else None
        return parse_size(size) if size else None


@dataclass(frozen=True)
class Disk:
    """A disk's use, in bytes.

    Attributes:
        usage: What is used.
        total: What Incus reports as the whole; 0 when it reports none.
    """

    usage: int
    total: int


@dataclass(frozen=True)
class State:
    """An instance's state.

    Attributes:
        status: Its status in words.
        status_code: Its status as Incus's code.
        pid: The process ID of its first process, 0 when it does not run.
        disk: The use of each of its disk devices that Incus measured, by
            device name (the root disk's under :attr:`Instance.root_device`).
    """

    status: str
    status_code: int
    pid: int
    disk: Mapping[str, Disk]


@dataclass(frozen=True)
class Result:
    """How a command ended.

    Attributes:
        exit_code: Its exit status.
        stdout: What it wrote to its standard output.
        stderr: What it wrote to its standard error.
    """

    exit_code: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True)
class File:
    """A file's content and what Incus says of it.

    Attributes:
        content: Its bytes.
        uid: Its owner.
        gid: Its group.
        mode: Its nine permission bits.
    """

    content: bytes
    uid: int
    gid: int
    mode: int


def _sendable(text: str) -> bool:
    """Whether TEXT has a UTF-8 form; a lone surrogate has none."""
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


def checked_argv(argv: Sequence[str]) -> list[str]:
    """ARGV as a list, when it is a command: at least one argument, each a string.

    Raises:
        TypeError: It is not a list or a tuple (one string is a command
            line, not a command) or holds what is no string.
        ValueError: It is empty, its program is empty, or an argument holds
            a NUL or a lone surrogate.
    """
    if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv):
        raise TypeError("a command is a list of strings, never a command line")
    if not argv or not argv[0] or any("\0" in a or not _sendable(a) for a in argv):
        raise ValueError(
            "a command needs a program, and no argument holds a NUL or a lone surrogate"
        )
    return list(argv)


def checked_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    """ENVIRONMENT as a dict, when it is variables for a command (none for None).

    Raises:
        TypeError: It is no mapping of strings to strings.
        ValueError: A name is empty or holds ``=``, or a name or a value
            holds a NUL or a lone surrogate.
    """
    given = environment if environment is not None else {}
    if not isinstance(given, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in given.items()
    ):
        raise TypeError("an environment maps strings to strings")
    for name, value in given.items():
        if not name or "=" in name or "\0" in name + value:
            raise ValueError(
                "an environment variable has a name that is not empty and holds no '=', and no NUL"
            )
        if not _sendable(name + value):
            raise ValueError("an environment holds no lone surrogate")
    return dict(given)


def checked_id(value: int, what: str) -> int:
    """VALUE, when it is a user or group ID: a whole number that fits 32 bits.

    Raises:
        ValueError: It is not (a boolean is no number here).
    """
    if type(value) is not int or not 0 <= value <= MAX_ID:
        raise ValueError(f"not a {what} ID: {value!r}")
    return value


def checked_path(path: str) -> str:
    """PATH, when it is absolute and in its normal form (``/`` itself is).

    Raises:
        ValueError: It is no string, is relative, or holds ``..``, ``.``,
            ``//``, a NUL, a lone surrogate or a final ``/``; nothing of it
            reaches a request.
    """
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        raise ValueError(f"not an absolute path: {path!r}")
    if not _sendable(path):
        raise ValueError(f"not a path with a UTF-8 form: {path!r}")
    if posixpath.normpath(path) != path or path.startswith("//"):
        raise ValueError(f"not a path in its normal form: {path!r}")
    return path


def _exit_status(operation: Mapping[str, Any]) -> int | None:
    """The exit status in a command's operation record, or None without one."""
    metadata = operation.get("metadata")
    status = metadata.get("return") if isinstance(metadata, dict) else None
    # A boolean is an integer in Python and is no exit status.
    return status if type(status) is int else None


def checked_name(name: str) -> str:
    """NAME, when it is an instance name Incus accepts.

    Raises:
        ValueError: It is not; nothing of it reaches a URL.
    """
    if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
        raise ValueError(f"not an instance name: {name!r}")
    return name


def checked_config(config: Mapping[str, str] | None) -> dict[str, str]:
    """CONFIG as a dict, when it holds only what the platform gives an instance.

    Raises:
        TypeError: It does not map strings to strings.
        ValueError: A key is none of :data:`CONFIG_KEYS`.
    """
    given = config if config is not None else {}
    if not isinstance(given, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in given.items()
    ):
        raise TypeError("an instance's configuration maps strings to strings")
    for key in given:
        if key not in CONFIG_KEYS:
            raise ValueError(f"not a configuration key the platform gives an instance: {key!r}")
    return dict(given)


def checked_devices(
    devices: Mapping[str, Mapping[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """DEVICES as a dict, when each is a device the platform gives an instance.

    A device replaces a profile's device of the same name whole, so each
    must say all that matters of it: a NIC its network, which is a run's.
    No disk is given, and the NIC is named :data:`NIC_NAME`, so that it
    replaces nothing else of a profile; its interface's name may be left
    to Incus.

    Raises:
        TypeError: It does not map names to maps of strings.
        ValueError: A device is of another type than :data:`DEVICE_KEYS`
            has (a disk is), holds another key, is a NIC on another
            network than a run's, or has another name than
            :data:`NIC_NAME`, itself or its interface.
    """
    given: dict[str, dict[str, str]] = {}
    listed = devices if devices is not None else {}
    if not isinstance(listed, Mapping):
        raise TypeError("an instance's devices map names to maps of strings")
    for name, device in listed.items():
        if not (isinstance(name, str) and isinstance(device, Mapping)) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in device.items()
        ):
            raise TypeError("an instance's devices map names to maps of strings")
        allowed = DEVICE_KEYS.get(device.get("type", ""))
        on_a_run = RUN_NETWORK_RE.fullmatch(device.get("network", "")) is not None
        named = name == NIC_NAME and device.get("name", NIC_NAME) == NIC_NAME
        if allowed is None or not set(device) <= set(allowed) or not on_a_run or not named:
            raise ValueError(f"not a device the platform gives an instance: {name!r}")
        given[name] = dict(device)
    return given


def checked_wait(wait: int) -> int:
    """WAIT, when it is a number of seconds Incus waits: a whole number, 0 or more.

    Raises:
        ValueError: It is not; to Incus a negative number would mean no end.
    """
    if type(wait) is not int or wait < 0:
        raise ValueError(f"not a number of seconds to wait: {wait!r}")
    return wait


def _field(record: Mapping[str, Any], key: str, kind: type) -> Any:
    """RECORD's KEY, an empty KIND when Incus sent none or null.

    Raises:
        IncusError: It is of another type than Incus's description gives it.
    """
    value = record.get(key)
    if value is None:
        return kind()
    if not isinstance(value, kind) or isinstance(value, bool) != (kind is bool):
        raise IncusError(FORM, SUCCESS)
    return value


def _texts(record: Mapping[str, Any], key: str) -> dict[str, str]:
    """RECORD's KEY, a map of texts (a configuration, a device)."""
    found = _field(record, key, dict)
    if not all(isinstance(item, str) for item in found.values()):
        raise IncusError(FORM, SUCCESS)
    return dict(found)


def _maps(record: Mapping[str, Any], key: str) -> dict[str, dict[str, Any]]:
    """RECORD's KEY, a map of maps (devices, the use of disks)."""
    found = _field(record, key, dict)
    if not all(isinstance(item, dict) for item in found.values()):
        raise IncusError(FORM, SUCCESS)
    return dict(found)


class Incus:
    """The Incus API for one project."""

    def __init__(
        self,
        url: str,
        project: str,
        *,
        certificate: Certificate | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT,
    ) -> None:
        """Connect with a certificate, or through a transport that stands for Incus.

        Args:
            url: The address of the API (``https://127.0.0.1:8443``).
            project: The project every call is made in.
            certificate: The client certificate and the pinned server certificate.
            transport: Instead of a certificate: what answers the requests (a test's Incus).
            timeout: The seconds to wait for an answer.

        Raises:
            ValueError: Both or neither of CERTIFICATE and TRANSPORT are given,
                or CERTIFICATE is given and URL is not an ``https`` address:
                over plain HTTP no certificate would be shown or checked.
        """
        if (certificate is None) == (transport is None):
            raise ValueError("a certificate or a transport, and not both")
        if certificate is not None and not url.startswith("https://"):
            raise ValueError("Incus is reached over https, where its certificate is checked")
        self.project = project
        self._timeout = timeout
        self._http = httpx.Client(
            base_url=url,
            verify=certificate.context() if certificate else True,
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )

    @classmethod
    def for_service(cls, endpoints: Endpoints, account: str, project: str) -> Self:
        """The client of a service, from the lab's endpoints file.

        Args:
            endpoints: The ``[incus]`` table of ``endpoints.toml``.
            account: The service's account, which names its certificate directory.
            project: The project to work in.

        Raises:
            ValueError: PROJECT is not one of the platform's projects, or
                ACCOUNT is not an account's name (it would name a directory
                outside the certificates').
        """
        if project not in endpoints.projects:
            raise ValueError(f"not a project of the platform: {project!r}")
        if not isinstance(account, str) or ACCOUNT_RE.fullmatch(account) is None:
            raise ValueError(f"not an account name: {account!r}")
        certificate = Certificate.of(Path(endpoints.cert_dir) / account)
        return cls(endpoints.url, project, certificate=certificate)

    def close(self) -> None:
        """Close the connections."""
        self._http.close()

    def __enter__(self) -> Self:
        """Use the client in a ``with`` block."""
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """Close the connections at the block's end."""
        self.close()

    def _call(
        self,
        method: str,
        path: str,
        *,
        scoped: bool = True,
        params: Mapping[str, str | int] | None = None,
        timeout: float | None = None,
        **content: Any,
    ) -> httpx.Response:
        # The project comes last: nothing a caller passes can name another.
        query = {**(params or {}), "project": self.project} if scoped else dict(params or {})
        try:
            response = self._http.request(
                method, path, params=query, timeout=timeout or self._timeout, **content
            )
        except httpx.HTTPError as exc:
            raise Unreachable(f"{method} {path}: {type(exc).__name__}") from exc
        if response.is_success:
            return response
        try:
            refusal = response.json()
        except ValueError:
            refusal = None
        reason = str(refusal.get("error") or "") if isinstance(refusal, dict) else ""
        kind = {403: Forbidden, 404: NotFound}.get(response.status_code, IncusError)
        raise kind(
            f"{method} {path}: {response.status_code} {reason}".rstrip(), response.status_code
        )

    def _metadata(self, response: httpx.Response, kind: type) -> Any:
        """An answer's metadata, of the KIND its call gives it.

        A list that Incus sent as null is an empty list (it sends a list
        that was never set so); a record is never null, and an answer
        without the key is none of Incus's.

        Raises:
            IncusError: The answer is no JSON object or its metadata is of
                another kind: it is not in the form of Incus's answers.
        """
        try:
            body = response.json()
        except ValueError:
            body = None
        metadata = body.get("metadata") if isinstance(body, dict) else None
        if isinstance(body, dict) and "metadata" in body and metadata is None and kind is list:
            return []
        if not isinstance(metadata, kind):
            raise IncusError(FORM, response.status_code)
        return metadata

    def _done(self, response: httpx.Response, wait: int = OPERATION_TIMEOUT) -> dict[str, Any]:
        """Wait for the operation RESPONSE announces; its record when it succeeded."""
        started = self._metadata(response, dict)
        identifier = str(started.get("id", ""))
        if OPERATION_RE.fullmatch(identifier) is None:
            raise IncusError("an operation without an identifier")
        ended = self._call(
            "GET",
            f"/1.0/operations/{identifier}/wait",
            scoped=False,
            params={"timeout": checked_wait(wait)},
            timeout=wait + self._timeout,
        )
        operation: dict[str, Any] = self._metadata(ended, dict)
        code = operation.get("status_code")
        what = operation.get("description") or "operation"
        if operation.get("id") != identifier:
            raise IncusError(f"{what}: not the operation that was waited for")
        # A boolean is an integer in Python and is no status.
        if type(code) is not int:
            raise IncusError(f"{what}: an operation without a status")
        if code == SUCCESS:
            return operation
        if code > SUCCESS:
            reason = operation.get("err") or operation.get("status")
            raise OperationFailed(f"{what}: {reason}", operation)
        raise OperationTimeout(f"{what}: not ended after {wait} s", operation)

    def instances(self) -> tuple[str, ...]:
        """The names of the project's instances."""
        listed = self._metadata(self._call("GET", "/1.0/instances"), list)
        start = "/1.0/instances/"
        names = []
        for url in listed:
            path, _, query = url.partition("?") if isinstance(url, str) else ("", "", "")
            name = path.removeprefix(start)
            # An instance of the client's project, named by its address.
            if (
                path != start + name
                or query not in ("", f"project={self.project}")
                or NAME_RE.fullmatch(name) is None
            ):
                raise IncusError(FORM, SUCCESS)
            names.append(name)
        return tuple(names)

    def instance(self, name: str) -> Instance:
        """An instance's record.

        Raises:
            NotFound: The project has no such instance.
        """
        data = self._metadata(self._call("GET", f"/1.0/instances/{checked_name(name)}"), dict)
        profiles = _field(data, "profiles", list)
        if not all(isinstance(profile, str) for profile in profiles):
            raise IncusError(FORM, SUCCESS)
        devices, expanded = _maps(data, "devices"), _maps(data, "expanded_devices")
        return Instance(
            name=_field(data, "name", str) or name,
            status=_field(data, "status", str),
            status_code=_field(data, "status_code", int),
            kind=_field(data, "type", str),
            profiles=tuple(profiles),
            config=_texts(data, "config"),
            devices={name: _texts(devices, name) for name in devices},
            expanded_config=_texts(data, "expanded_config"),
            expanded_devices={name: _texts(expanded, name) for name in expanded},
        )

    def state(self, name: str) -> State:
        """An instance's state, with the use of its disks.

        A disk whose use Incus did not give is left out: a use that is not
        known is never taken for 0.
        """
        answer = self._call("GET", f"/1.0/instances/{checked_name(name)}/state")
        data = self._metadata(answer, dict)
        disks = {
            device: Disk(usage=_field(use, "usage", int), total=_field(use, "total", int))
            for device, use in _maps(data, "disk").items()
            if use.get("usage") is not None
        }
        return State(
            status=_field(data, "status", str),
            status_code=_field(data, "status_code", int),
            pid=_field(data, "pid", int),
            disk=disks,
        )

    def create(
        self,
        name: str,
        *,
        fingerprint: str,
        profiles: Sequence[str],
        config: Mapping[str, str] | None = None,
        devices: Mapping[str, Mapping[str, str]] | None = None,
        kind: str = "container",
    ) -> None:
        """Create an instance from an image's fingerprint, with exactly these profiles.

        Args:
            name: The instance's name.
            fingerprint: The image's fingerprint: 64 lower-case hexadecimal digits.
            profiles: The profiles to apply, in order; at least one.
            config: The instance's own configuration: only keys of
                :data:`CONFIG_KEYS`.
            devices: The instance's own devices: only the NIC
                :data:`NIC_NAME` on a run's network, with the keys of
                :data:`DEVICE_KEYS`; no disk.
            kind: ``container`` or ``virtual-machine``.

        Raises:
            TypeError: A profile list, a configuration or devices of
                another form; no request is sent.
            ValueError: A name, a fingerprint, a profile list or a kind that
                is not one, or a configuration key or a device the platform
                does not give an instance; no request is sent.
            OperationFailed: Incus could not create it.
            OperationTimeout: Incus had not created it when the wait ended;
                it goes on creating it.
        """
        if not isinstance(fingerprint, str) or FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise ValueError("not an image fingerprint")
        if not isinstance(profiles, (list, tuple)):
            raise TypeError("an explicit list of profiles is needed")
        if not profiles or not all(isinstance(p, str) and p for p in profiles):
            raise ValueError("an explicit list of at least one profile is needed")
        if kind not in KINDS:
            raise ValueError(f"not a kind of instance: {kind!r}")
        body = {
            "name": checked_name(name),
            "type": kind,
            "profiles": list(profiles),
            "config": checked_config(config),
            "devices": checked_devices(devices),
            "source": {"type": "image", "fingerprint": fingerprint},
        }
        self._done(self._call("POST", "/1.0/instances", json=body))

    def _change(self, name: str, action: str, force: bool, wait: int) -> None:
        body = {"action": action, "timeout": checked_wait(wait), "force": force}
        path = f"/1.0/instances/{checked_name(name)}/state"
        self._done(self._call("PUT", path, json=body), wait + int(self._timeout))

    def start(self, name: str, *, wait: int = OPERATION_TIMEOUT) -> None:
        """Start an instance and wait until it runs.

        Raises:
            OperationFailed: Incus could not start it.
            OperationTimeout: It did not run when the wait ended; Incus goes on.
        """
        self._change(name, "start", False, wait)

    def stop(self, name: str, *, force: bool = False, wait: int = OPERATION_TIMEOUT) -> None:
        """Stop an instance and wait until it has stopped.

        Args:
            name: The instance.
            force: Stop it at once instead of asking it to shut down.
            wait: The seconds Incus waits for the shutdown.

        Raises:
            OperationFailed: Incus could not stop it.
            OperationTimeout: It had not stopped when the wait ended; Incus goes on.
        """
        self._change(name, "stop", force, wait)

    def delete(self, name: str) -> None:
        """Delete a stopped instance.

        Raises:
            OperationFailed: Incus could not delete it.
            OperationTimeout: It was not deleted when the wait ended; Incus goes on.
        """
        self._done(self._call("DELETE", f"/1.0/instances/{checked_name(name)}"))

    def exec(
        self,
        name: str,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        cwd: str | None = None,
        user: int = 0,
        group: int = 0,
        wait: int = EXEC_TIMEOUT,
    ) -> Result:
        """Run a command in an instance and wait for its end.

        Args:
            name: The instance.
            argv: The program and its arguments; no shell reads them.
            environment: Variables to add to the command's environment.
            cwd: The directory to run it in.
            user: The user ID to run it as.
            group: The group ID to run it as.
            wait: The seconds to wait for its end.

        Returns:
            Its exit status and its output. A status other than 0 is a
            result, not an exception: 127 and 126 too, with which Incus ends
            a program that does not exist or cannot be run.

        Raises:
            TypeError: ARGV is not a list of strings, or ENVIRONMENT does
                not map strings to strings; no request is sent.
            ValueError: ARGV, the name, ENVIRONMENT, CWD, USER, GROUP or
                WAIT is not what it must be; no request is sent.
            OperationFailed: Incus could not run the command, or the
                command failed in another way than by its exit status;
                what records there are of its output are removed.
            OperationTimeout: The command had not ended after WAIT seconds.
                Incus does not stop it and has no call that would; its
                output records stay on the Incus host.
            OutputLost: The command ended, and a record of its output could
                not be fetched or removed (each removal is tried all the
                same); the exception has its exit status.
            IncusError: The operation's record is not a command's, or no
                answer came (:class:`Unreachable`); records may stay then.
        """
        body: dict[str, Any] = {
            "command": checked_argv(argv),
            "environment": checked_environment(environment),
            "interactive": False,
            "wait-for-websocket": False,
            "record-output": True,
            "user": checked_id(user, "user"),
            "group": checked_id(group, "group"),
        }
        if cwd is not None:
            body["cwd"] = checked_path(cwd)
        path = f"/1.0/instances/{checked_name(name)}/exec"
        seconds = checked_wait(wait)
        announced = self._call("POST", path, json=body)
        try:
            ended: Mapping[str, Any] = self._done(announced, seconds)
        except OperationFailed as failed:
            # A command that ended with 126 or 127 is a result like any other.
            if _exit_status(failed.operation) not in NOT_RUN:
                # The operation has ended and nothing else removes what it recorded.
                self._remove(self._logs(name, failed.operation))
                raise
            ended = failed.operation
        code = _exit_status(ended)
        logs = self._logs(name, ended)
        metadata = ended.get("metadata")
        if code is None or not isinstance(metadata, dict) or metadata.get("output") != logs:
            raise IncusError("a command's operation without its exit status and output records")
        fetched: list[bytes] = []
        failures: list[IncusError] = []
        for log in logs.values():
            try:
                fetched.append(self._call("GET", log).content)
            except IncusError as exc:
                failures.append(exc)
        failures += self._remove(logs)
        if failures:
            lost = "; ".join(str(failure) for failure in failures)
            raise OutputLost(
                f"{lost} (the command ended with {code})", failures[0].status, code
            ) from failures[0]
        return Result(code, fetched[0], fetched[1])

    @staticmethod
    def _logs(name: str, operation: Mapping[str, Any]) -> dict[str, str]:
        """The two records of a command's output, as Incus names them after its operation."""
        return {
            stream: f"/1.0/instances/{name}/logs/exec-output/exec_{operation['id']}.{suffix}"
            for stream, suffix in STREAMS.items()
        }

    def _remove(self, logs: Mapping[str, str]) -> list[IncusError]:
        """Remove a command's output records, each whatever happened to another; the failures."""
        failures: list[IncusError] = []
        for log in logs.values():
            try:
                self._call("DELETE", log)
            except IncusError as exc:
                failures.append(exc)
        return failures

    def _file(self, name: str, path: str, kind: str) -> httpx.Response:
        query = {"path": checked_path(path)}
        response = self._call("GET", f"/1.0/instances/{checked_name(name)}/files", params=query)
        found = response.headers.get("X-Incus-type", "")
        if found != kind:
            raise WrongKind(f"{path}: a {found or 'thing of no type'}, not a {kind}", found)
        return response

    def read_file(self, name: str, path: str) -> File:
        """A file's content, owner and mode.

        To Incus a file is whatever is no directory and no symbolic link: a
        device or a pipe too, whose read may never end. Which paths are
        read is the caller's to decide.

        Raises:
            ValueError: The name or PATH is not one; no request is sent.
            NotFound: The instance has no such file.
            WrongKind: PATH names a directory or a symbolic link (the last
                part of a path is not followed).
            IncusError: The answer lacks the file's owner or mode.
        """
        response = self._file(name, path, "file")
        uid, gid, mode = (
            response.headers.get(f"X-Incus-{key}", "") for key in ("uid", "gid", "mode")
        )
        if not (OWNER_RE.fullmatch(uid) and OWNER_RE.fullmatch(gid) and MODE_RE.fullmatch(mode)):
            raise IncusError(f"{path}: a file without its owner and mode")
        return File(response.content, int(uid), int(gid), int(mode, 8) & MODE_MASK)

    def list_directory(self, name: str, path: str) -> tuple[str, ...]:
        """The names in a directory.

        Raises:
            WrongKind: PATH names what is no directory.
            IncusError: The answer is no list of names.
        """
        names = self._metadata(self._file(name, path, "directory"), list)
        if not all(isinstance(entry, str) for entry in names):
            raise IncusError(FORM, SUCCESS)
        return tuple(names)

    def write_file(
        self,
        name: str,
        path: str,
        content: bytes,
        *,
        uid: int = 0,
        gid: int = 0,
        mode: int = 0o644,
        append: bool = False,
    ) -> None:
        """Write a regular file, replacing it or appending to it.

        Args:
            name: The instance.
            path: The file's absolute path; its directory must exist.
            content: The bytes to write.
            uid: The owner of a new file; a file that exists keeps its own.
            gid: The group of a new file; a file that exists keeps its own.
            mode: The permission bits of a new file, at most ``0o777`` (the
                files API sets no other bit); a file that exists keeps its own.
            append: Add to the end of a file that exists (Incus creates
                none for an append) instead of replacing it.

        Raises:
            TypeError: CONTENT is not bytes; no request is sent.
            ValueError: The name, PATH, UID, GID or MODE is not what it must
                be; no request is sent.
            NotFound: The directory does not exist, or the file of an append.
        """
        if not isinstance(content, bytes):
            raise TypeError("a file's content is bytes")
        if type(mode) is not int or not 0 <= mode <= MODE_MASK:
            raise ValueError(f"not a file mode: {mode!r}")
        headers = {
            "X-Incus-type": "file",
            "X-Incus-write": "append" if append else "overwrite",
            "X-Incus-uid": str(checked_id(uid, "user")),
            "X-Incus-gid": str(checked_id(gid, "group")),
            # Incus reads the mode by its prefix: the leading zero makes it octal.
            "X-Incus-mode": f"{mode:04o}",
        }
        query = {"path": checked_path(path)}
        files = f"/1.0/instances/{checked_name(name)}/files"
        self._call("POST", files, params=query, content=content, headers=headers)

    def delete_file(self, name: str, path: str) -> None:
        """Remove a file, a symbolic link itself, or an empty directory.

        Raises:
            NotFound: The instance has no such file.
        """
        query = {"path": checked_path(path)}
        self._call("DELETE", f"/1.0/instances/{checked_name(name)}/files", params=query)
