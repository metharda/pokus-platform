"""What a registry is compiled to: a model's tools, the policy engine's data, the page."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest
from potka_testing.tools import (
    changed,
    forbidden,
    handler,
    remove_file,
    restart_service,
    tail_journal,
    tool,
)
from potka_tools.arguments import META_SCHEMA
from potka_tools.compile import documentation, model_tools, policy_registry, policy_tools
from potka_tools.registry import Registry, load_registry

REAL = load_registry()
ENTRIES = (
    restart_service(),
    tail_journal(),
    forbidden(),
    handler("apply_config_patch"),
    remove_file(),
)
SMALL = Registry.of(7, tuple(tool(entry) for entry in ENTRIES))
# Every keyword a model's schema may hold.
KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "pattern",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "default",
    "description",
}
META = {"action_confidence": 0.9, "rationale": "It is down.", "expected_effect": "It runs."}


def names(tools: list[dict[str, Any]]) -> list[str]:
    return [one["function"]["name"] for one in tools]


def parameters(registry: Registry, name: str) -> dict[str, Any]:
    (one,) = model_tools(registry, [name])
    found: dict[str, Any] = one["function"]["parameters"]
    return found


def test_a_model_s_tool_is_its_name_its_description_and_the_schema_of_its_arguments() -> None:
    tools = model_tools(SMALL)
    assert names(tools) == ["apply_config_patch", "remove_file", "restart_service", "tail_journal"]
    assert tools[3] == {
        "type": "function",
        "function": {
            "name": "tail_journal",
            "description": "The last lines of a unit's journal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
                    "node": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "unit": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                        "pattern": r"^[a-z0-9@._-]+\.service$",
                    },
                },
                "required": ["node", "unit"],
                "additionalProperties": False,
            },
        },
    }
    # The same registry gives the same tools, and they are plain JSON.
    assert model_tools(SMALL) == tools == json.loads(json.dumps(tools))


def test_a_mutating_tool_s_arguments_gain_a_required_meta_and_a_read_only_tool_s_none() -> None:
    restart = parameters(SMALL, "restart_service")
    assert restart["required"] == ["_meta", "node", "unit"]
    assert restart["properties"]["_meta"] == META_SCHEMA
    assert list(restart["properties"]) == ["node", "unit", "_meta"]
    for one in model_tools(SMALL):
        said = one["function"]
        mutating = said["name"] != "tail_journal"
        assert ("_meta" in said["parameters"]["properties"]) == mutating
        assert ("_meta" in said["parameters"]["required"]) == mutating
    # What a model is told is what a call is held to.
    validator = jsonschema.Draft202012Validator(restart)
    call = {"node": "web", "unit": "nginx.service", "_meta": META}
    assert validator.is_valid(call)
    assert not validator.is_valid({"node": "web", "unit": "nginx.service"})
    assert not validator.is_valid({**call, "_meta": {**META, "action_confidence": 1.5}})
    assert not validator.is_valid({**call, "_meta": {**META, "more": 1}})
    # Each tool has its own copy: a change to one reaches no other and not the next call.
    restart["properties"]["_meta"]["properties"].clear()
    assert parameters(SMALL, "restart_service")["properties"]["_meta"] == META_SCHEMA
    assert parameters(SMALL, "remove_file")["properties"]["_meta"]["properties"]
    assert META_SCHEMA["properties"]


def test_a_forbidden_tool_and_a_name_the_registry_has_not_are_never_given() -> None:
    assert "reboot" not in names(model_tools(SMALL))
    assert SMALL.tool("reboot") is not None
    for asked in (["reboot"], ["tail_journal", "no_such_tool"], ["Tail_Journal"], [""]):
        with pytest.raises(ValueError, match=r"^not a tool a model may be given: "):
            model_tools(SMALL, asked)
    # A subset comes in the registry's order, whatever order it was asked in.
    assert names(model_tools(SMALL, ["tail_journal", "remove_file"])) == [
        "remove_file",
        "tail_journal",
    ]
    assert names(model_tools(SMALL, {"tail_journal"})) == ["tail_journal"]
    assert model_tools(SMALL, []) == []


def keywords(schema: dict[str, Any]) -> set[str]:
    """Every keyword of SCHEMA and of the schemas of its properties, at any depth."""
    found = set(schema)
    for below in schema.get("properties", {}).values():
        found |= keywords(below)
    return found


def strings(value: object) -> list[str]:
    """Every string of VALUE: keys and values, at any depth."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (key, *strings(item))]
    if isinstance(value, list):
        return [text for item in value for text in strings(item)]
    return []


def test_the_release_s_tools_say_nothing_but_what_a_model_is_given() -> None:
    tools = model_tools(REAL)
    assert len(tools) == 36
    assert names(tools) == [one.name for one in REAL.tools if one.risk != "forbidden"]
    for one in tools:
        assert set(one) == {"type", "function"} and one["type"] == "function"
        said = one["function"]
        assert set(said) == {"name", "description", "parameters"}
        schema = said["parameters"]
        jsonschema.Draft202012Validator.check_schema(schema)
        assert keywords(schema) <= KEYWORDS
        assert schema["additionalProperties"] is False
        # Nothing of a source of a run's context, of a command or of a handler.
        written = REAL.tool(said["name"])
        assert written is not None
        assert not any(text.startswith("context.") for text in strings(schema))
        assert not {"from", "within", "exec", "argv", "handler", "post_check"} & set(
            strings(schema)
        )
        mutating = "_meta" in schema["properties"]
        assert mutating == written.mutating
        assert set(schema["properties"]) == set(written.args) | ({"_meta"} if mutating else set())


def test_the_policy_engine_is_told_of_every_tool_and_of_the_registry() -> None:
    assert policy_tools(SMALL) == {
        "apply_config_patch": {
            "version": 1,
            "risk": "medium",
            "mutating": True,
            "applicable_categories": ["config.syntax_error"],
        },
        "reboot": {
            "version": 1,
            "risk": "forbidden",
            "mutating": True,
            "applicable_categories": [],
        },
        "remove_file": {
            "version": 1,
            "risk": "medium",
            "mutating": True,
            "applicable_categories": ["resource.disk_full"],
        },
        "restart_service": {
            "version": 3,
            "risk": "medium",
            "mutating": True,
            "applicable_categories": ["service.stopped", "config.wrong_value"],
        },
        "tail_journal": {
            "version": 1,
            "risk": "read",
            "mutating": False,
            "applicable_categories": [],
        },
    }
    assert policy_registry(SMALL) == {"version": 7, "hash": SMALL.hash}
    told = policy_tools(REAL)
    assert list(told) == [one.name for one in REAL.tools] and len(told) == 41
    assert sum(1 for one in told.values() if one["risk"] == "forbidden") == 5
    assert told == json.loads(json.dumps(told))
    assert policy_registry(REAL) == {"version": REAL.version, "hash": REAL.hash}


RESTART = """### `restart_service`

Restart a systemd service on a node.

- Risk: medium. Version of the entry: 3.
- Fits the diagnoses: `service.stopped`, `config.wrong_value`.
- Runs in the node: `["systemctl", "restart", "{unit}"]`. Ended after 30 s and killed 5 s \
later; at most 65536 bytes of each output stream are given back.
- Checked afterwards by: `["systemctl", "is-active", "{unit}"]`.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `node` | string | yes | one of the run's nodes |  |
| `unit` | string | yes | a service of the node; matches `^[a-z0-9@._-]+\\.service$` |  |
"""
PATCH = """### `apply_config_patch`

What apply_config_patch does.

- Risk: medium. Version of the entry: 1.
- Fits the diagnoses: `config.syntax_error`.
- Carried out by the handler `apply_config_patch`; at most 65536 bytes are given back.

| Argument | Type | Required | Rule | Description |
| --- | --- | --- | --- | --- |
| `diff` | text | yes | at most 65536 characters |  |
| `node` | string | yes | one of the run's nodes |  |
| `path` | path | yes | a path of the node that may be patched |  |
"""


def test_the_page_lists_every_tool_once_with_what_stands_behind_it() -> None:
    page = documentation(SMALL)
    assert page == documentation(SMALL)
    assert page.startswith(
        "---\n"
        "title: The tools a model may call\n"
        "type: reference\n"
        "audience: [researcher, scenario-author, developer, reader]\n"
        'applies_to: {platform: ">=0.0"}\n'
        "last_verified: {date: null, rc: null, by: null}\n"
        "---\n\n"
        "<!-- Written by `make tools` from tools/registry/. Do not edit it by hand. -->\n\n"
        "# The tools a model may call\n\n"
    )
    assert page.endswith("|\n") and "\n\n\n" not in page
    assert f"\n\nVersion 7 of the tool registry, content hash `{SMALL.hash}`.\n\n" in page
    assert RESTART in page and PATCH in page
    assert "| `lines` | integer | no; default `100` | from 1 to 500 |  |" in page
    assert "| `path` | path | yes | a path below `/tmp` or `/var/tmp` or `/var/cache` |  |" in page
    assert "| `reboot` | Restart the machine. |" in page
    places = [
        page.index(heading)
        for heading in (
            "## Read-only tools",
            "### `tail_journal`",
            "## Mutating tools",
            "### `apply_config_patch`",
            "### `remove_file`",
            "### `restart_service`",
            "## Forbidden tools",
            "| `reboot` |",
        )
    ]
    assert places == sorted(places)
    assert page.count("### `") == 4 and "### `reboot`" not in page
    # The release's page: every tool once, a forbidden one in the last table only.
    real = documentation(REAL)
    for one in REAL.tools:
        heading, row = f"### `{one.name}`\n", f"\n| `{one.name}` | "
        assert (real.count(heading), real.count(row)) == (
            (0, 1) if one.risk == "forbidden" else (1, 0)
        )
        if one.risk != "forbidden":
            assert real.index(heading) < real.index("## Forbidden tools") < real.index("| Tool |")


def test_text_of_an_entry_cannot_break_the_page_s_tables() -> None:
    entry = changed(tail_journal(), "args.unit.pattern", "^(a|b)`c$")
    entry["args"]["unit"]["description"] = "One | of   two  units."
    entry["args"]["lines"]["description"] = "How many."
    entry["description"] = "The last   lines."
    entry["exec"]["argv"] = ["journalctl", {"literal": "--output-fields={MESSAGE|UNIT}"}]
    page = documentation(Registry.of(1, (tool(entry),)))
    assert "\n\nThe last lines.\n\n" in page
    assert (
        "| `unit` | string | yes | a service of the node; matches `` ^(a\\|b)`c$ `` |"
        " One \\| of two units. |\n"
    ) in page
    assert "| `lines` | integer | no; default `100` | from 1 to 500 | How many. |\n" in page
    assert (
        '- Runs in the node: `["journalctl", {"literal": "--output-fields={MESSAGE|UNIT}"}]`.'
    ) in page
    # Every row of every table has its five cells.
    rows = [line for line in page.splitlines() if line.startswith("| `")]
    assert len(rows) == 3
    assert all(line.replace("\\|", "").count("|") == 6 for line in rows)
    # What stands in a code span stands there as it is: doubled spaces, and two backticks
    # in a row behind a longer fence.
    spaced = changed(tail_journal(), "args.unit", {"type": "string", "enum": ["a  b", "c``d`e"]})
    spaced["exec"]["argv"] = ["journalctl", "--unit={unit}"]
    page = documentation(Registry.of(1, (tool(spaced),)))
    assert "| `unit` | string | yes | one of `a  b`, ``` c``d`e ``` |  |\n" in page
