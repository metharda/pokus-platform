from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from potka_ci import incus_api

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "c" * 40
PROJECT = {"in": "query", "name": "project", "description": "Project name", "example": "default"}


def description() -> dict[str, Any]:
    """A small API description with every call of the client, written as Incus writes its own."""
    paths: dict[str, Any] = {}
    for method, route in incus_api.CALLS:
        paths.setdefault(route, {})[method.lower()] = {
            "description": "A call.",
            "parameters": [dict(PROJECT)],
            "responses": {
                "200": {
                    "description": "A thing",
                    "schema": {
                        "description": "Sync response",
                        "type": "object",
                        "properties": {
                            "metadata": {"$ref": "#/definitions/Thing"},
                            "status": {"type": "string", "example": "Success"},
                        },
                    },
                },
                "202": {"$ref": "#/responses/Operation"},
            },
        }
    paths["/1.0/instances"]["post"]["parameters"] += [
        {"in": "body", "name": "instance", "schema": {"$ref": "#/definitions/Thing"}},
        {"in": "body", "name": "raw_backup"},
    ]
    paths["/1.0/instances/{name}/state"]["put"]["parameters"] += [
        {
            "in": "body",
            "name": "state",
            "schema": {
                "type": "object",
                "description": "A state change",
                "properties": {"action": {"type": "string", "example": "start"}},
            },
        },
    ]
    paths["/1.0/instances/{name}"]["get"]["parameters"] += [
        {"in": "path", "name": "name", "type": "string", "example": "web"}
    ]
    files = paths["/1.0/instances/{name}/files"]
    files["post"]["parameters"] += [
        {"in": "body", "name": "raw_file"},
        {"in": "header", "name": "X-Incus-uid", "example": 1000},
    ]
    files["get"]["responses"]["200"] = {
        "description": "Raw file",
        "headers": {"X-Incus-uid": {"description": "File owner UID"}},
    }
    return {
        "swagger": "2.0",
        "info": {"contact": {"name": "upstream"}},
        "paths": paths,
        "responses": {
            "Operation": {
                "description": "Operation",
                "schema": {
                    "type": "object",
                    "properties": {"metadata": {"$ref": "#/definitions/Op"}},
                },
            }
        },
        "definitions": {
            "Thing": {
                "title": "A thing.",
                "type": "object",
                "x-go-package": "example",
                "properties": {
                    "description": {
                        "type": "string",
                        "example": "Mine",
                        "description": "Its text",
                        "x-go-name": "Description",
                    },
                    "parts": {"type": "array", "items": {"$ref": "#/definitions/Part"}},
                    "config": {"type": "object", "additionalProperties": {"type": "string"}},
                },
            },
            "Part": {
                "type": "object",
                "properties": {
                    "size": {"type": "integer", "format": "int64"},
                    "of": {"$ref": "#/definitions/Thing"},
                },
            },
            "Op": {"type": "object", "properties": {"metadata": {"additionalProperties": {}}}},
            "Unused": {"type": "object"},
        },
    }


def configuration() -> dict[str, Any]:
    return {
        "configs": {
            "instance": {
                "security": {"keys": [{"security.nesting": {"shortdesc": "Nesting"}}]},
                "volatile": {"keys": [{"volatile.<name>.hwaddr": {}}, {"volatile.base_image": {}}]},
            },
            "devices": {
                "disk": {"keys": [{"size": {"type": "string"}}, {"path": {}}]},
                "nic_bridged": {"keys": [{"network": {}}]},
                "proxy": {"keys": [{"listen": {}}]},
            },
        }
    }


def tree(
    tmp_path: Path, api: Any = None, config: Any = None
) -> tuple[Path, Path, dict[str, bytes]]:
    """A working tree with a source record, and a directory with the two files it names.

    API and CONFIG are the two files' contents (the stand-ins when None); bytes are
    written as they are.
    """
    one = api if isinstance(api, bytes) else yaml.safe_dump(api or description()).encode()
    two = config if isinstance(config, bytes) else json.dumps(config or configuration()).encode()
    contents = {incus_api.API: one, incus_api.CONFIGURATION: two}
    root, incus = tmp_path / "root", tmp_path / "incus"
    source = {
        "repository": "https://github.com/example-org/incus",
        "tag": "v9.9.9",
        "version": "9.9.9",
        "commit": COMMIT,
        "license": "Apache-2.0",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }
    (root / incus_api.DIRECTORY).mkdir(parents=True)
    (root / incus_api.SOURCE).write_text(json.dumps(source), encoding="utf-8")
    for name, data in contents.items():
        (incus / name).parent.mkdir(parents=True, exist_ok=True)
        (incus / name).write_bytes(data)
    return root, incus, contents


def nodes(schema: Any) -> list[dict[str, Any]]:
    """Every schema of a structure: the schema itself, its properties, items and map values."""
    if not isinstance(schema, dict):
        return []
    inner = [*schema.get("properties", {}).values(), schema.get("items")]
    inner.append(schema.get("additionalProperties"))
    return [schema, *(node for item in inner for node in nodes(item))]


def test_the_extract_keeps_the_structure_of_the_client_s_calls_and_nothing_else(
    tmp_path: Path,
) -> None:
    root, incus, _ = tree(tmp_path)
    text = incus_api.generate(root, incus)
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    extract = json.loads(text)
    assert sorted(extract) == ["calls", "config", "definitions", "source"]
    assert sorted(extract["calls"]) == sorted(f"{m} {r}" for m, r in incus_api.CALLS)
    assert extract["source"]["commit"] == COMMIT
    # The definitions the calls name, and those these name; no other.
    assert extract["definitions"] == {
        "Thing": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "parts": {"type": "array", "items": {"$ref": "#/definitions/Part"}},
                "config": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        },
        "Part": {
            "type": "object",
            "properties": {
                "size": {"type": "integer", "format": "int64"},
                "of": {"$ref": "#/definitions/Thing"},
            },
        },
        "Op": {"type": "object", "properties": {"metadata": {"additionalProperties": {}}}},
    }
    # A body written in place is read as one by reference is; a parameter in the path leaves
    # no trace (the route holds it).
    changed = extract["calls"]["PUT /1.0/instances/{name}/state"]
    assert changed["body"] == {"type": "object", "properties": {"action": {"type": "string"}}}
    read_one = extract["calls"]["GET /1.0/instances/{name}"]
    assert (read_one["query"], read_one["headers"], read_one["body"]) == (["project"], [], None)
    created = extract["calls"]["POST /1.0/instances"]
    assert (created["body"], created["query"], created["headers"]) == (
        {"$ref": "#/definitions/Thing"},
        ["project"],
        [],
    )
    assert created["responses"]["202"] == {
        "headers": [],
        "schema": {"type": "object", "properties": {"metadata": {"$ref": "#/definitions/Op"}}},
    }
    written = extract["calls"]["POST /1.0/instances/{name}/files"]
    assert (written["body"], written["headers"]) == ("raw", ["X-Incus-uid"])
    read = extract["calls"]["GET /1.0/instances/{name}/files"]
    assert (read["body"], read["responses"]["200"]) == (
        None,
        {"headers": ["X-Incus-uid"], "schema": None},
    )
    assert extract["config"] == {
        "instance": ["security.nesting", "volatile.<name>.hwaddr", "volatile.base_image"],
        "devices": {"disk": ["path", "size"], "nic_bridged": ["network"]},
    }
    for word in (
        "example",
        "Mine",
        "Its text",
        "A call.",
        "Sync response",
        "upstream",
        "Nesting",
        "x-go",
        "A state change",
        "start",
        "web",
    ):
        assert word not in text.replace("example-org", ""), word


def test_a_file_with_another_sha256_is_refused(tmp_path: Path) -> None:
    root, incus, _ = tree(tmp_path)
    (incus / incus_api.CONFIGURATION).write_bytes(b'{"configs": {}}')
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    recorded = json.loads((root / incus_api.SOURCE).read_text())["files"][incus_api.CONFIGURATION]
    found = hashlib.sha256(b'{"configs": {}}').hexdigest()
    assert refused.value.problems == [
        f"{incus_api.CONFIGURATION}: sha256 {found},"
        f" not the {recorded} of {incus_api.SOURCE.as_posix()}"
    ]
    (incus / incus_api.API).unlink()
    with pytest.raises(incus_api.ExtractError) as missing:
        incus_api.generate(root, incus)
    assert missing.value.problems == [f"{incus_api.API}: FileNotFoundError in {incus}"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda a, c: a["paths"].pop("/1.0/instances"), "has no GET /1.0/instances"),
        (lambda a, c: a["definitions"].pop("Part"), "has no definition Part"),
        (lambda a, c: c["configs"]["devices"].pop("disk"), "has no device type disk"),
        (lambda a, c: c["configs"].pop("instance"), "has no instance keys"),
    ],
)
def test_a_source_that_lacks_what_the_extract_needs_is_refused(
    tmp_path: Path, change: Any, problem: str
) -> None:
    api, config = description(), configuration()
    change(api, config)
    root, incus, _ = tree(tmp_path, api, config)
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert problem in refused.value.problems[0]


UNKNOWN = "a schema with a key the extract does not know"
NOT_KNOWN = "that the extract does not know"
INSTANCES = "/1.0/instances"


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"type": "string", "enum": ["a", "b"]}, f"{UNKNOWN}: ['enum']"),
        ({"allOf": [{"type": "object"}], "required": ["a"]}, f"{UNKNOWN}: ['allOf', 'required']"),
        (
            {"type": "array", "items": [{"type": "string"}]},
            "a schema that is no mapping: [{'type': 'string'}]",
        ),
        ({"type": "object", "properties": ["a"]}, "properties that are no mapping: ['a']"),
        ({"type": ["string", "null"]}, f"a schema's type {NOT_KNOWN}: ['string', 'null']"),
        ({"type": True}, f"a schema's type {NOT_KNOWN}: True"),
        ({"type": "the name, e.g. web"}, f"a schema's type {NOT_KNOWN}: 'the name, e.g. web'"),
        ({"type": "integer", "format": "a number"}, f"a schema's format {NOT_KNOWN}: 'a number'"),
        ({"$ref": "Thing"}, f"a schema's $ref {NOT_KNOWN}: 'Thing'"),
        ({"$ref": "#/definitions/"}, f"a schema's $ref {NOT_KNOWN}: '#/definitions/'"),
        ({"$ref": 7}, f"a schema's $ref {NOT_KNOWN}: 7"),
        ({"items": "x"}, "a schema that is no mapping: 'x'"),
        ({"type": "object", "additionalProperties": "yes"}, "a schema that is no mapping: 'yes'"),
    ],
)
def test_a_schema_of_a_form_the_extract_does_not_know_is_refused(
    tmp_path: Path, schema: dict[str, Any], problem: str
) -> None:
    api = description()
    api["definitions"]["Part"]["properties"]["size"] = schema
    root, incus, _ = tree(tmp_path, api)
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert refused.value.problems == [f"{incus_api.API}: the definition Part: {problem}"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            lambda a: a["paths"][INSTANCES]["get"]["parameters"].append(
                {"in": "formData", "name": "f"}
            ),
            f"GET {INSTANCES} has a parameter the extract does not know: ['formData']",
        ),
        (
            lambda a: a["paths"][INSTANCES]["post"]["parameters"].append(
                {"in": "body", "name": "other", "schema": {"type": "object"}}
            ),
            f"POST {INSTANCES} has more than one described body",
        ),
        (
            lambda a: a["paths"][INSTANCES]["get"]["responses"].update(
                {"403": {"$ref": "#/responses/Forbidden"}}
            ),
            f"GET {INSTANCES} names a response the file lacks: #/responses/Forbidden",
        ),
        (
            lambda a: a["paths"][INSTANCES]["get"]["responses"].update(
                {"403": {"$ref": "Operation"}}
            ),
            f"GET {INSTANCES} names a response the file lacks: Operation",
        ),
        # A body written in place goes through the same guard, on a call without another.
        (
            lambda a: a["paths"][INSTANCES]["get"]["parameters"].append(
                {"in": "body", "name": "in place", "schema": {"type": "object", "enum": [1]}}
            ),
            f"GET {INSTANCES}: {UNKNOWN}: ['enum']",
        ),
        (
            lambda a: a["paths"][INSTANCES]["get"]["responses"]["200"]["schema"].update(
                type="file"
            ),
            f"GET {INSTANCES}: a schema's type {NOT_KNOWN}: 'file'",
        ),
        # Parameters of a whole path would be those of each of its calls.
        (
            lambda a: a["paths"][INSTANCES].update(parameters=[{"in": "query", "name": "all"}]),
            f"{INSTANCES} holds what the extract does not read beside its methods: ['parameters']",
        ),
        (
            lambda a: a["definitions"]["Thing"]["properties"].update({7: {"type": "string"}}),
            "a name that is no text ('<' not supported between instances of 'int' and 'str')",
        ),
    ],
)
def test_a_call_of_a_form_the_extract_does_not_know_is_refused(
    tmp_path: Path, change: Any, problem: str
) -> None:
    api = description()
    change(api)
    root, incus, _ = tree(tmp_path, api)
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert refused.value.problems == [f"{incus_api.API}: {problem}"]


NOT_READ = f"{incus_api.API} or {incus_api.CONFIGURATION}: not in the form the extract reads"


@pytest.mark.parametrize(
    ("api", "config", "problem"),
    [
        (b"a: [", None, f"{incus_api.API}: not YAML ("),
        (None, b"{", f"{incus_api.CONFIGURATION}: not JSON (JSONDecodeError)"),
        (["a", "list"], None, f"{NOT_READ} (AttributeError: 'list' object has no attribute 'get')"),
        (None, ["a", "list"], f"{NOT_READ} (AttributeError: 'list' object has no attribute 'get')"),
    ],
)
def test_a_file_that_is_not_what_the_extract_reads_is_the_script_s_own_error(
    tmp_path: Path, api: Any, config: Any, problem: str
) -> None:
    root, incus, _ = tree(tmp_path, api, config)
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert len(refused.value.problems) == 1
    assert refused.value.problems[0].startswith(problem)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda a: a["paths"][INSTANCES]["get"].pop("responses"), "KeyError: 'responses'"),
        (lambda a: a["paths"][INSTANCES]["get"]["parameters"][0].pop("in"), "KeyError: 'in'"),
        (lambda a: a["paths"][INSTANCES]["get"]["parameters"][0].pop("name"), "KeyError: 'name'"),
        (lambda a: a["paths"][INSTANCES]["get"].update(parameters=7), "TypeError: "),
    ],
)
def test_a_call_with_a_part_missing_is_the_script_s_own_error_and_names_the_call(
    tmp_path: Path, change: Any, reason: str
) -> None:
    api = description()
    change(api)
    root, incus, _ = tree(tmp_path, api)
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert len(refused.value.problems) == 1
    assert refused.value.problems[0].startswith(
        f"{incus_api.API}: GET {INSTANCES}: not in the form the extract reads ({reason}"
    )


def test_a_status_written_as_a_number_is_the_same_status(tmp_path: Path) -> None:
    api = description()
    responses = api["paths"][INSTANCES]["get"]["responses"]
    responses[200] = responses.pop("200")
    root, incus, _ = tree(tmp_path, api)
    extract = json.loads(incus_api.generate(root, incus))
    assert sorted(extract["calls"][f"GET {INSTANCES}"]["responses"]) == ["200", "202"]


def test_a_map_of_any_value_is_kept_as_one(tmp_path: Path) -> None:
    api = description()
    api["definitions"]["Part"]["properties"]["size"] = {"additionalProperties": True}
    root, incus, _ = tree(tmp_path, api)
    extract = json.loads(incus_api.generate(root, incus))
    assert extract["definitions"]["Part"]["properties"]["size"] == {"additionalProperties": True}


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda s: s.pop("commit"), "TypeError: "),
        (lambda s: s.update(extra=1), "TypeError: "),
        (lambda s: s["files"].pop(incus_api.API), "files are not "),
        (lambda s: s.update(files=None), "a field is not of the record's type"),
        (lambda s: s.update(files=sorted(s["files"])), "a field is not of the record's type"),
        (lambda s: s.update(repository=1), "a field is not of the record's type"),
        (lambda s: s["files"].update({incus_api.API: "abc"}), "a sha256 is 64 hexadecimal digits"),
        (lambda s: s["files"].update({incus_api.API: 7}), "a sha256 is 64 hexadecimal digits"),
        (
            lambda s: s.update(repository="https://git.example.org/incus"),
            "the repository is not on github.com",
        ),
        (lambda s: s.update(commit="v9.9.9"), "the commit is 40 hexadecimal digits"),
        (lambda s: s.update(commit="C" * 40), "the commit is 40 hexadecimal digits"),
    ],
)
def test_a_source_record_that_is_not_one_is_refused(
    tmp_path: Path, change: Any, problem: str
) -> None:
    root, incus, contents = tree(tmp_path)
    source = json.loads((root / incus_api.SOURCE).read_text(encoding="utf-8"))
    change(source)
    (root / incus_api.SOURCE).write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(incus_api.ExtractError) as refused:
        incus_api.generate(root, incus)
    assert len(refused.value.problems) == 1
    assert refused.value.problems[0].startswith(f"{incus_api.SOURCE.as_posix()}: {problem}")
    # Nothing is fetched on such a record, even when the files themselves are the right ones.
    asked: list[str] = []

    def opener(url: str) -> bytes:
        asked.append(url)
        return next(data for name, data in contents.items() if url.endswith(name))

    outcome = incus_api.fetch(root, tmp_path / "fetched", opener)
    assert (outcome.status, outcome.lines, asked) == ("failed", refused.value.problems, [])
    assert not (tmp_path / "fetched").exists()


def test_a_source_record_that_is_missing_or_no_json_is_refused(tmp_path: Path) -> None:
    root, incus, _ = tree(tmp_path)
    (root / incus_api.SOURCE).write_text("{", encoding="utf-8")
    with pytest.raises(incus_api.ExtractError, match=r"source\.json: JSONDecodeError"):
        incus_api.generate(root, incus)
    (root / incus_api.SOURCE).write_text("[]", encoding="utf-8")
    with pytest.raises(incus_api.ExtractError, match=r"source\.json: TypeError"):
        incus_api.generate(root, incus)
    (root / incus_api.SOURCE).unlink()
    with pytest.raises(incus_api.ExtractError, match=r"source\.json: FileNotFoundError"):
        incus_api.read_source(root)


def test_the_two_files_are_fetched_from_the_recorded_commit(tmp_path: Path) -> None:
    root, _, contents = tree(tmp_path)
    base = f"https://raw.githubusercontent.com/example-org/incus/{COMMIT}/"
    asked: list[str] = []

    def opener(url: str) -> bytes:
        asked.append(url)
        return contents[url.removeprefix(base)]

    target = tmp_path / "fetched"
    outcome = incus_api.fetch(root, target, opener)
    assert (outcome.status, outcome.lines) == (
        "passed",
        [f"fetched {incus_api.API} and {incus_api.CONFIGURATION} of {COMMIT}"],
    )
    assert asked == [base + incus_api.API, base + incus_api.CONFIGURATION]
    assert incus_api.generate(root, target) == incus_api.generate(root, tmp_path / "incus")


def test_a_fetched_file_with_another_sha256_fails_at_once_and_is_not_written(
    tmp_path: Path,
) -> None:
    root, _, _ = tree(tmp_path)
    asked: list[str] = []

    def opener(url: str) -> bytes:
        asked.append(url)
        return b"another file"

    target = tmp_path / "fetched"
    outcome = incus_api.fetch(root, target, opener)
    assert outcome.status == "failed"
    assert len(asked) == 1
    assert outcome.lines[0].startswith(f"{asked[0]}: sha256 ")
    assert not target.exists()


def test_the_second_file_failing_writes_nothing_of_it_and_no_extract_is_made(
    tmp_path: Path,
) -> None:
    root, _, contents = tree(tmp_path)
    target = tmp_path / "fetched"
    (target / incus_api.CONFIGURATION).parent.mkdir(parents=True)
    (target / incus_api.CONFIGURATION).write_bytes(b"an older file")

    def opener(url: str) -> bytes:
        return contents[incus_api.API] if url.endswith(incus_api.API) else b"another file"

    outcome = incus_api.fetch(root, target, opener)
    assert outcome.status == "failed"
    assert outcome.lines[0].endswith(f" of {incus_api.SOURCE.as_posix()}")
    # The first file is the checked one; of the second nothing is written, the older one
    # stays, and the extract is not made from it.
    assert (target / incus_api.API).read_bytes() == contents[incus_api.API]
    assert (target / incus_api.CONFIGURATION).read_bytes() == b"an older file"
    with pytest.raises(incus_api.ExtractError, match=f"{incus_api.CONFIGURATION}: sha256 "):
        incus_api.generate(root, target)


@pytest.mark.parametrize(
    ("failure", "text"),
    [
        (OSError("no route to host"), "OSError: no route to host"),
        (http.client.IncompleteRead(b"par"), "IncompleteRead: IncompleteRead(3 bytes read)"),
    ],
)
def test_a_download_that_fails_names_its_address(
    tmp_path: Path, failure: Exception, text: str
) -> None:
    root, _, _ = tree(tmp_path)

    def opener(url: str) -> bytes:
        raise failure

    outcome = incus_api.fetch(root, tmp_path / "fetched", opener)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"https://raw.githubusercontent.com/example-org/incus/{COMMIT}/{incus_api.API}: {text}"
    ]
    assert not (tmp_path / "fetched").exists()


def test_a_file_that_cannot_be_written_fails_the_fetch(tmp_path: Path) -> None:
    root, _, contents = tree(tmp_path)
    target = tmp_path / "fetched"
    target.write_text("a file where the directory should be", encoding="utf-8")
    outcome = incus_api.fetch(root, target, lambda url: contents[url.split(COMMIT + "/")[1]])
    assert outcome.status == "failed"
    assert outcome.lines == [f"{target / incus_api.API}: NotADirectoryError: Not a directory"]


def test_only_the_raw_file_address_of_github_is_opened() -> None:
    with pytest.raises(ValueError, match="http://"):
        incus_api._download("http://raw.githubusercontent.com/example-org/incus/x")


def test_the_committed_extract_says_where_it_comes_from() -> None:
    source = incus_api.read_source(ROOT)
    extract = json.loads((ROOT / incus_api.EXTRACT).read_text(encoding="utf-8"))
    assert extract["source"] == source.__dict__
    assert sorted(extract["calls"]) == sorted(f"{m} {r}" for m, r in incus_api.CALLS)
    assert sorted(extract["config"]["devices"]) == sorted(incus_api.DEVICES)
    assert source.tag == "v" + source.version
    readme = (ROOT / incus_api.README).read_text(encoding="utf-8")
    for fact in (
        source.repository,
        source.tag,
        source.commit,
        *source.files,
        *source.files.values(),
    ):
        assert f"`{fact}`" in readme, fact
    assert "Apache License, Version 2.0" in readme
    # Structure only: no schema of the extract holds an example or a description.
    schemas = [call["body"] for call in extract["calls"].values()]
    schemas += [
        r["schema"] for call in extract["calls"].values() for r in call["responses"].values()
    ]
    found = [
        node for schema in [*schemas, *extract["definitions"].values()] for node in nodes(schema)
    ]
    assert {key for node in found for key in node} <= set(incus_api.STRUCTURE)
    # And its values are types, formats and references to definitions the extract holds.
    assert {node["type"] for node in found if "type" in node} <= set(incus_api.TYPES)
    assert all(incus_api.FORMAT_RE.fullmatch(node["format"]) for node in found if "format" in node)
    # Every definition is reached from a call, and no other is held.
    reached: set[str] = set()
    todo = [node for schema in schemas for node in nodes(schema)]
    while todo:
        name = todo.pop().get("$ref", "").removeprefix(incus_api.DEFINITIONS)
        if name and name not in reached:
            reached.add(name)
            todo += nodes(extract["definitions"][name])
    assert reached == set(extract["definitions"])
    assert len(extract["definitions"]) == 18
