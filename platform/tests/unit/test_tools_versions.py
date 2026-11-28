"""Every tool of the registry is pinned at its version: what a model sees of it, and its entry.

The registry's rule is that a change to what a model sees of a tool (its
name, its description, the schema of its arguments) or to what the tool
does raises the tool's version and the registry's version. This test holds
the registry's entries to that rule: ``tests/fixtures/tools-versions.json``
keeps, for every tool, its version and two hashes, and for the registry its
version and its hash. A tool whose hashes differ from the kept ones at the
kept version fails here.

Two things it cannot hold. What a tool does lies also in code that its
entry only names: a handler, the rendering of a command, the checks of an
argument. The hashes are of the entry, so a change there, with the entry as
it was, passes. And the kept file is compared with the registry as it is,
not with what the file held before: written anew from a changed registry,
it hides the change. In the kept file's own diff that mistake is a hash
that changed beside a version that did not.

When a tool is changed on purpose: raise its ``version`` in its file and
the number in ``tools/registry/VERSION``, then replace the tool's entry
and the registry's entry in the kept file with the ones the failing test
prints. Never replace a hash alone.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from potka_core.canonical import canonical_json
from potka_tools.arguments import META, META_SCHEMA, argument_schema
from potka_tools.compile import model_tools
from potka_tools.registry import FORBIDDEN, Exec, Registry, Tool, load_registry

PLATFORM = Path(__file__).resolve().parents[2]
KEPT_PATH = PLATFORM / "tests" / "fixtures" / "tools-versions.json"
REGISTRY = load_registry(PLATFORM / "tools" / "registry")
KEPT: dict[str, Any] = json.loads(KEPT_PATH.read_text(encoding="utf-8"))
# What a model sees of a tool; everything else of an entry but its version is what it does.
SEEN = ("name", "description")


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def seen(tool: Tool) -> str:
    """The hash of what a model sees of TOOL: its name, its description, its arguments' schema.

    A forbidden tool is given to no model; its name, its description and
    the schema its arguments would have are hashed all the same, since the
    documentation shows them.
    """
    parameters = argument_schema(tool)
    if tool.mutating and tool.risk != FORBIDDEN:
        parameters["properties"][META] = copy.deepcopy(dict(META_SCHEMA))
        parameters["required"] = [META, *parameters["required"]]
    return _hash({"name": tool.name, "description": tool.description, "parameters": parameters})


def does(tool: Tool) -> str:
    """The hash of what TOOL does: its entry without its version and without what `seen` covers.

    The rules of its arguments, its risk, whether it mutates, the
    diagnoses it fits, its command or handler with their limits, and the
    command that checks it afterwards.
    """
    entry = tool.model_dump(mode="json", by_alias=True)
    return _hash({key: value for key, value in entry.items() if key not in (*SEEN, "version")})


def pin(tool: Tool) -> dict[str, Any]:
    """TOOL's entry of the kept file."""
    return {"version": tool.version, "seen": seen(tool), "does": does(tool)}


def pins(registry: Registry) -> dict[str, Any]:
    """The kept file's content for REGISTRY."""
    return {
        "registry": {"version": registry.version, "hash": registry.hash},
        "tools": {tool.name: pin(tool) for tool in registry.tools},
    }


def _entry(name: str, value: dict[str, Any]) -> str:
    return f'"{name}": {json.dumps(value, sort_keys=True)}'


def test_the_kept_file_names_exactly_the_registrys_tools() -> None:
    assert sorted(KEPT["tools"]) == [tool.name for tool in REGISTRY.tools]


@pytest.mark.parametrize("tool", REGISTRY.tools, ids=lambda tool: tool.name)
def test_a_tool_is_what_it_was_at_its_version(tool: Tool) -> None:
    kept = KEPT["tools"][tool.name]
    now = pin(tool)
    if now == kept:
        return
    changed = [key for key in ("seen", "does") if now[key] != kept[key]]
    if not changed:
        pytest.fail(
            f"{tool.name}: its version went from {kept['version']} to {now['version']} and "
            "nothing a model sees of it or that it does has changed"
        )
    what = " and ".join(
        {"seen": "what a model sees of it", "does": "what it does"}[k] for k in changed
    )
    if now["version"] <= kept["version"]:
        pytest.fail(
            f"{tool.name}: {what} changed and its version is still {now['version']}: raise the "
            "tool's version and the registry's (tools/registry/VERSION), then keep the new entries"
        )
    pytest.fail(
        f"{tool.name}: {what} changed with its version ({kept['version']} to {now['version']}); "
        f"keep the new entry in {KEPT_PATH.name}:\n  {_entry(tool.name, now)}"
    )


def test_the_registry_is_what_it_was_at_its_version() -> None:
    kept = KEPT["registry"]
    now = {"version": REGISTRY.version, "hash": REGISTRY.hash}
    if now == kept:
        return
    if now["hash"] != kept["hash"] and now["version"] <= kept["version"]:
        pytest.fail(
            f"the registry changed and its version is still {now['version']}: raise "
            "tools/registry/VERSION with the version of every tool that changed"
        )
    if now["hash"] == kept["hash"]:
        pytest.fail(
            f"the registry's version went from {kept['version']} to {now['version']}"
            " and nothing of it has changed"
        )
    pytest.fail(f"the registry changed with its version; keep:\n  {_entry('registry', now)}")


def test_no_tool_changed_under_an_unchanged_registry() -> None:
    changed = [tool.name for tool in REGISTRY.tools if pin(tool) != KEPT["tools"].get(tool.name)]
    assert not changed or REGISTRY.version > KEPT["registry"]["version"], changed


def _tool(name: str) -> Tool:
    tool = REGISTRY.tool(name)
    assert tool is not None
    return tool


def _with(tool: Tool, **changes: Any) -> Tool:
    """TOOL with these fields changed: an unchecked copy, since the models are frozen."""
    return tool.model_copy(update=changes)


def _with_arg(tool: Tool, name: str, **changes: Any) -> Tool:
    return _with(tool, args={**tool.args, name: tool.args[name].model_copy(update=changes)})


def _with_command(tool: Tool, **changes: Any) -> Tool:
    assert isinstance(tool.command, Exec)
    return _with(tool, command=tool.command.model_copy(update=changes))


def test_a_description_is_seen_and_changes_nothing_a_tool_does() -> None:
    tool = _tool("restart_service")
    other = _with(tool, description="Restart a service.")
    assert seen(other) != seen(tool)
    assert does(other) == does(tool)


def test_an_arguments_description_and_its_pattern_are_seen() -> None:
    tool = _tool("restart_service")
    told = _with_arg(tool, "unit", description="Another word for the unit.")
    bound = _with_arg(tool, "unit", pattern=r"^[a-z]+\.service$")
    assert seen(told) != seen(tool)
    assert seen(bound) != seen(tool)
    # A pattern binds what reaches the command too.
    assert does(bound) != does(tool)


@pytest.mark.parametrize(
    "change",
    [
        {"risk": "high"},
        {"applicable_categories": ("service.stopped",)},
        {"post_check": None},
    ],
    ids=["risk", "categories", "post_check"],
)
def test_what_a_tool_does_is_more_than_its_command(change: dict[str, Any]) -> None:
    tool = _tool("restart_service")
    other = _with(tool, **change)
    assert does(other) != does(tool)
    assert seen(other) == seen(tool)


@pytest.mark.parametrize("field", ["timeout_s", "kill_after_s", "max_output_bytes"])
def test_a_commands_limits_are_what_a_tool_does(field: str) -> None:
    tool = _tool("restart_service")
    assert isinstance(tool.command, Exec)
    other = _with_command(tool, **{field: getattr(tool.command, field) + 1})
    assert does(other) != does(tool)
    assert seen(other) == seen(tool)


def test_a_commands_words_and_where_it_runs_are_what_a_tool_does() -> None:
    tool = _tool("http_probe")
    assert isinstance(tool.command, Exec)
    words = _with_command(tool, argv=(*tool.command.argv[:-1], "--head", tool.command.argv[-1]))
    where = _with_command(tool, on="node")
    assert does(words) != does(tool)
    assert does(where) != does(tool)


def test_a_version_alone_changes_neither_hash() -> None:
    tool = _tool("restart_service")
    other = _with(tool, version=tool.version + 1)
    assert (seen(other), does(other)) == (seen(tool), does(tool))


def test_a_mutating_tools_statement_is_part_of_what_a_model_sees() -> None:
    tool = _tool("restart_service")
    given = model_tools(REGISTRY, [tool.name])[0]["function"]
    assert seen(tool) == _hash(
        {
            "name": given["name"],
            "description": given["description"],
            "parameters": given["parameters"],
        }
    )


def test_every_tool_a_model_is_given_is_hashed_as_it_is_given() -> None:
    for given in model_tools(REGISTRY):
        function = given["function"]
        assert seen(_tool(function["name"])) == _hash(
            {
                "name": function["name"],
                "description": function["description"],
                "parameters": function["parameters"],
            }
        )


def test_the_kept_file_is_what_this_module_writes() -> None:
    assert KEPT_PATH.read_text(encoding="utf-8") == written(pins(REGISTRY))


def written(content: dict[str, Any]) -> str:
    """The kept file's text: one line for the registry and one for each tool, by name."""
    tools = [_entry(name, value) for name, value in sorted(content["tools"].items())]
    return (
        "{\n"
        f"  {_entry('registry', content['registry'])},\n"
        '  "tools": {\n' + ",\n".join(f"    {line}" for line in tools) + "\n  }\n}\n"
    )
