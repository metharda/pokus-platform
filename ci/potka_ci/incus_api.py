"""The Incus API extract: the form of the calls the platform's Incus client makes.

The tests of the Incus client and of the in-memory Incus compare every
request and response with what Incus itself says of its API. Incus says it
in two files of its repository: ``doc/rest-api.yaml``, the description of
its REST API, and ``internal/server/metadata/configuration.json``, its
configuration keys. Both hold example values and prose that do not belong
in this repository, so the tests read an extract
(``platform/tests/fixtures/incus-api/api.json``): for each call of
:data:`CALLS` the names of its query parameters and headers (the names
only: not their types, not whether one is required, not what a call
consumes or produces), its response codes with the names of their headers
(not the headers' types), and the structure (types, properties, items,
references) of its
bodies and of every definition they name; and the names of the
configuration keys of an instance and of the device types of
:data:`DEVICES`. No example, no description. Where a call takes a
described body or raw bytes, the described one is kept.

What the extract cannot read as that, it refuses instead of leaving out: a
schema with a key that is neither structure nor prose (:data:`STRUCTURE`,
:data:`PROSE`), a type, format or reference of another form, a parameter
of another kind than :data:`KINDS`, a second described body, anything a
path holds beside its methods (parameters of the whole path among it).

``source.json`` beside the extract names the repository, the commit and the
sha256 of both files. :func:`fetch` downloads them from that commit and
:func:`generate` builds the extract from them; each refuses a file whose
sha256 is not the recorded one. ``make incus-sources`` and ``make
incus-api`` run them, and where the two files are at hand the checks
compare the committed extract with a fresh one.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from potka_ci.outcome import Outcome

DIRECTORY = Path("platform") / "tests" / "fixtures" / "incus-api"
SOURCE = DIRECTORY / "source.json"
EXTRACT = DIRECTORY / "api.json"
README = DIRECTORY / "README.md"
API = "doc/rest-api.yaml"
CONFIGURATION = "internal/server/metadata/configuration.json"
GITHUB = "https://github.com/"
RAW = "https://raw.githubusercontent.com/"
# Every call of the client (packages/potka-incus), as the description spells it.
CALLS = (
    ("GET", "/1.0/instances"),
    ("POST", "/1.0/instances"),
    ("GET", "/1.0/instances/{name}"),
    ("DELETE", "/1.0/instances/{name}"),
    ("GET", "/1.0/instances/{name}/state"),
    ("PUT", "/1.0/instances/{name}/state"),
    ("POST", "/1.0/instances/{name}/exec"),
    ("GET", "/1.0/instances/{name}/logs/exec-output/{filename}"),
    ("DELETE", "/1.0/instances/{name}/logs/exec-output/{filename}"),
    ("GET", "/1.0/instances/{name}/files"),
    ("POST", "/1.0/instances/{name}/files"),
    ("DELETE", "/1.0/instances/{name}/files"),
    ("GET", "/1.0/operations/{id}/wait"),
)
# The device types the platform gives an instance: a root disk, and a NIC on a managed network.
DEVICES = ("disk", "nic_bridged")
# What a schema's structure is, and what it says beside it (left out of the extract).
STRUCTURE = ("$ref", "additionalProperties", "format", "items", "properties", "type")
PROSE = ("description", "example", "title", "x-go-name", "x-go-package")
# The values a schema's type, format and reference may have.
TYPES = ("array", "boolean", "integer", "number", "object", "string")
FORMAT_RE = re.compile(r"[a-z0-9-]+")
# Where a call's parameter may be; one in the path is in the route already.
KINDS = ("body", "header", "path", "query")
# What a path of the description may hold: its methods and nothing beside them.
METHODS = ("delete", "get", "head", "options", "patch", "post", "put")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DEFINITIONS = "#/definitions/"
RESPONSES = "#/responses/"

Opener = Callable[[str], bytes]


class ExtractError(Exception):
    """The extract cannot be made.

    Attributes:
        problems: One line for each reason.
    """

    def __init__(self, problems: list[str]) -> None:
        """Keep the reasons."""
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Source:
    """Where the two files come from.

    Attributes:
        repository: The repository's URL on github.com.
        tag: The release's tag.
        version: The release's version.
        commit: The tag's commit.
        files: Each file's path in the repository and its sha256.
        license: The SPDX name of the repository's licence.
    """

    repository: str
    tag: str
    version: str
    commit: str
    files: dict[str, str]
    license: str


def read_source(root: Path) -> Source:
    """Read ``source.json``.

    Args:
        root: The working tree.

    Returns:
        The source.

    Raises:
        ExtractError: The file is missing or is not the record of a source.
    """
    try:
        source = Source(**json.loads((root / SOURCE).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError) as exc:
        raise ExtractError([f"{SOURCE.as_posix()}: {type(exc).__name__}: {exc}"]) from exc
    texts = (source.repository, source.tag, source.version, source.commit, source.license)
    if not all(isinstance(text, str) for text in texts) or not isinstance(source.files, dict):
        raise ExtractError([f"{SOURCE.as_posix()}: a field is not of the record's type"])
    if sorted(source.files) != sorted((API, CONFIGURATION)):
        raise ExtractError([f"{SOURCE.as_posix()}: files are not {API} and {CONFIGURATION}"])
    if not all(isinstance(v, str) and SHA256_RE.fullmatch(v) for v in source.files.values()):
        raise ExtractError([f"{SOURCE.as_posix()}: a sha256 is 64 hexadecimal digits"])
    if not source.repository.startswith(GITHUB):
        raise ExtractError([f"{SOURCE.as_posix()}: the repository is not on github.com"])
    if COMMIT_RE.fullmatch(source.commit) is None:
        raise ExtractError([f"{SOURCE.as_posix()}: the commit is 40 hexadecimal digits"])
    return source


def _verified(source: Source, name: str, data: bytes, where: str) -> bytes:
    found = hashlib.sha256(data).hexdigest()
    if found != source.files[name]:
        raise ExtractError(
            [f"{where}: sha256 {found}, not the {source.files[name]} of {SOURCE.as_posix()}"]
        )
    return data


def _download(url: str) -> bytes:
    if not url.startswith(RAW):
        raise ValueError(url)
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (https, checked above)
        data: bytes = response.read()
    return data


def fetch(root: Path, destination: Path, opener: Opener | None = None) -> Outcome:
    """Download the two files at the recorded commit into DESTINATION.

    Args:
        root: The working tree.
        destination: The directory to write them into, at their repository paths.
        opener: What downloads a URL (the tests give their own); https by default.

    Returns:
        The outcome: failed when a file cannot be downloaded or written or
        its sha256 is not the recorded one. Nothing of a file whose
        download or sha256 failed is written; a write that fails may leave
        a part of its file; a file fetched before stays, and so does an
        older file of the same name. The extract is made only from files
        whose sha256 is checked again, so none of these is ever read.
    """
    try:
        source = read_source(root)
        base = RAW + source.repository.removeprefix(GITHUB) + "/" + source.commit + "/"
        for name in (API, CONFIGURATION):
            try:
                data = (opener or _download)(base + name)
            except (OSError, http.client.HTTPException) as exc:
                raise ExtractError([f"{base + name}: {type(exc).__name__}: {exc}"]) from exc
            _verified(source, name, data, base + name)
            try:
                (destination / name).parent.mkdir(parents=True, exist_ok=True)
                (destination / name).write_bytes(data)
            except OSError as exc:
                reason = f"{type(exc).__name__}: {exc.strerror}"
                raise ExtractError([f"{destination / name}: {reason}"]) from exc
    except ExtractError as exc:
        return Outcome("failed", exc.problems)
    return Outcome("passed", [f"fetched {API} and {CONFIGURATION} of {source.commit}"])


def _structure(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ExtractError([f"{API}: a schema that is no mapping: {schema!r}"])
    unknown = sorted(set(schema).difference(STRUCTURE, PROSE))
    if unknown:
        raise ExtractError([f"{API}: a schema with a key the extract does not know: {unknown}"])
    kept: dict[str, Any] = {}
    for key in STRUCTURE:
        if key not in schema:
            continue
        value = schema[key]
        if key == "properties":
            if not isinstance(value, dict):
                raise ExtractError([f"{API}: properties that are no mapping: {value!r}"])
            kept[key] = {name: _structure(item) for name, item in value.items()}
        elif key == "items" or (key == "additionalProperties" and not isinstance(value, bool)):
            kept[key] = _structure(value)
        elif key == "additionalProperties" or _known(key, value):
            kept[key] = value
        else:
            raise ExtractError(
                [f"{API}: a schema's {key} that the extract does not know: {value!r}"]
            )
    return kept


def _placed(place: str, schema: Any) -> dict[str, Any]:
    """SCHEMA's structure; what is refused of it is named with its PLACE in the file."""
    try:
        return _structure(schema)
    except ExtractError as exc:
        start = f"{API}: "
        problems = [f"{start}{place}: {problem.removeprefix(start)}" for problem in exc.problems]
        raise ExtractError(problems) from exc


def _known(key: str, value: Any) -> bool:
    """Whether VALUE is what a schema's type, format or reference may be."""
    if not isinstance(value, str):
        return False
    if key == "type":
        return value in TYPES
    if key == "format":
        return FORMAT_RE.fullmatch(value) is not None
    return value.startswith(DEFINITIONS) and value != DEFINITIONS


def _references(node: Any) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(_references(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_references(item) for item in node))
    return set()


def _call(api: dict[str, Any], method: str, route: str) -> dict[str, Any]:
    call = f"{method} {route}"
    beside = sorted(str(key) for key in set(api["paths"][route]).difference(METHODS))
    if beside:
        raise ExtractError(
            [f"{API}: {route} holds what the extract does not read beside its methods: {beside}"]
        )
    operation = api["paths"][route][method.lower()]
    parameters = operation.get("parameters", [])
    unknown = sorted({str(p["in"]) for p in parameters}.difference(KINDS))
    if unknown:
        raise ExtractError([f"{API}: {call} has a parameter the extract does not know: {unknown}"])
    bodies = [p for p in parameters if p["in"] == "body"]
    # A body with a schema is described, in place or by reference; one without is raw bytes.
    described = [_placed(call, p["schema"]) for p in bodies if "schema" in p]
    if len(described) > 1:
        raise ExtractError([f"{API}: {call} has more than one described body"])
    responses = {}
    for status, response in operation["responses"].items():
        if "$ref" in response:
            name = str(response["$ref"])
            shared = api.get("responses", {})
            if not name.startswith(RESPONSES) or name.removeprefix(RESPONSES) not in shared:
                raise ExtractError([f"{API}: {call} names a response the file lacks: {name}"])
            response = shared[name.removeprefix(RESPONSES)]
        # A status the file writes as a number is the same status.
        responses[str(status)] = {
            "headers": sorted(response.get("headers", {})),
            "schema": _placed(call, response["schema"]) if "schema" in response else None,
        }
    return {
        "body": described[0] if described else ("raw" if bodies else None),
        "headers": sorted(p["name"] for p in parameters if p["in"] == "header"),
        "query": sorted(p["name"] for p in parameters if p["in"] == "query"),
        "responses": responses,
    }


def _keys(group: dict[str, Any]) -> list[str]:
    return sorted({key for entry in group["keys"] for key in entry})


def extract(source: Source, api: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    """Build the extract from the two files' contents.

    Args:
        source: Where the files come from.
        api: ``doc/rest-api.yaml``, parsed.
        configuration: ``configuration.json``, parsed.

    Returns:
        The extract.

    Raises:
        ExtractError: The description lacks a call, a definition or a device
            type, or a part of it has a form the extract does not know.
    """
    try:
        return _extract(source, api, configuration)
    except (KeyError, TypeError, AttributeError) as exc:
        reason = f"{type(exc).__name__}: {exc}"
        raise ExtractError(
            [f"{API} or {CONFIGURATION}: not in the form the extract reads ({reason})"]
        ) from exc


def _extract(source: Source, api: dict[str, Any], configuration: dict[str, Any]) -> dict[str, Any]:
    problems = [
        f"{API} has no {method} {route}"
        for method, route in CALLS
        if method.lower() not in api.get("paths", {}).get(route, {})
    ]
    configs = configuration.get("configs", {})
    problems += [
        f"{CONFIGURATION} has no device type {device}"
        for device in DEVICES
        if device not in configs.get("devices", {})
    ]
    if problems or "instance" not in configs:
        raise ExtractError(problems or [f"{CONFIGURATION} has no instance keys"])
    calls = {}
    for method, route in CALLS:
        try:
            calls[f"{method} {route}"] = _call(api, method, route)
        except (KeyError, TypeError, AttributeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            raise ExtractError(
                [f"{API}: {method} {route}: not in the form the extract reads ({reason})"]
            ) from exc
    definitions: dict[str, Any] = {}
    todo = _references(calls)
    while todo:
        name = todo.pop().removeprefix(DEFINITIONS)
        if name in definitions:
            continue
        if name not in api.get("definitions", {}):
            raise ExtractError([f"{API} has no definition {name}"])
        definitions[name] = _placed(f"the definition {name}", api["definitions"][name])
        todo |= _references(definitions[name])
    instance = sorted({key for group in configs["instance"].values() for key in _keys(group)})
    return {
        "calls": calls,
        "config": {
            "devices": {device: _keys(configs["devices"][device]) for device in DEVICES},
            "instance": instance,
        },
        "definitions": definitions,
        "source": source.__dict__,
    }


def generate(root: Path, incus: Path) -> str:
    """The extract's text, from the two files below INCUS.

    Args:
        root: The working tree.
        incus: A directory that holds the two files at their repository paths.

    Returns:
        JSON with sorted keys, indented by two spaces, with a final newline.

    Raises:
        ExtractError: A file is missing, its sha256 is not the recorded
            one, or the extract cannot be made from it.
    """
    source = read_source(root)
    contents = {}
    for name in (API, CONFIGURATION):
        try:
            data = (incus / name).read_bytes()
        except OSError as exc:
            raise ExtractError([f"{name}: {type(exc).__name__} in {incus}"]) from exc
        contents[name] = _verified(source, name, data, name)
    try:
        api = yaml.safe_load(contents[API])
    except yaml.YAMLError as exc:
        raise ExtractError([f"{API}: not YAML ({type(exc).__name__})"]) from exc
    try:
        configuration = json.loads(contents[CONFIGURATION])
    except ValueError as exc:
        raise ExtractError([f"{CONFIGURATION}: not JSON ({type(exc).__name__})"]) from exc
    built = extract(source, api, configuration)
    try:
        return json.dumps(built, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    except TypeError as exc:
        raise ExtractError([f"{API}: a name that is no text ({exc})"]) from exc
