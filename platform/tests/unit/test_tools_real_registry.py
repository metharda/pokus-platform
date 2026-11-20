"""The registry the release ships: its tools, and what every call of one becomes."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from potka_core.catalog.loader import load_catalog
from potka_testing.tool_commands import shape
from potka_tools import values
from potka_tools.arguments import ArgumentError, check
from potka_tools.context import Context
from potka_tools.registry import HANDLERS, Arg, Tool, load_registry
from potka_tools.render import render
from potka_tools.template import SHELLS, elements

PLATFORM = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(PLATFORM / "tools" / "registry")
READ_ONLY = {
    "list_nodes",
    "get_service_status",
    "list_failed_units",
    "tail_journal",
    "read_file",
    "list_dir",
    "check_disk_usage",
    "check_inodes",
    "check_memory",
    "list_processes",
    "network_interfaces",
    "list_listening_ports",
    "dns_lookup",
    "http_probe",
    "nginx_test_config",
    "docker_ps",
    "docker_logs",
    "docker_inspect",
    "pg_isready",
    "pg_activity_summary",
    "redis_ping",
    "redis_info",
    "query_logs",
    "query_metrics",
}
MUTATING = {
    "start_service": "low",
    "restart_service": "medium",
    "nginx_reload": "low",
    "docker_restart_container": "low",
    "docker_start_container": "low",
    "apply_config_patch": "medium",
    "vacuum_journal": "medium",
    "rotate_logs": "medium",
    "remove_file": "medium",
    "kill_process": "high",
    "stop_service": "high",
    "docker_remove_container": "high",
}
FORBIDDEN = {"remove_package", "manage_user", "flush_firewall", "remove_path", "reboot"}
# A run in which every tool has something to name.
CONTEXT = Context.model_validate_json(
    json.dumps(
        {
            "nodes": [
                {
                    "alias": "web",
                    "services": ["nginx.service", "docker.service"],
                    "readable_paths": ["/etc/nginx", "/var/log/nginx"],
                    "patchable_paths": [{"path": "/etc/nginx/nginx.conf", "validator": None}],
                    "endpoints": [{"name": "site", "scheme": "http", "port": 80, "path": "/"}],
                },
                {
                    "alias": "db",
                    "services": ["postgresql.service"],
                    "readable_paths": ["/etc/postgresql"],
                    "patchable_paths": [],
                    "endpoints": [],
                },
            ]
        }
    ),
    strict=True,
)
COMMANDS = [tool for tool in REGISTRY.tools if tool.command is not None]


def test_the_registry_holds_the_tools_a_run_needs_each_with_its_risk() -> None:
    assert REGISTRY.version == 1
    assert len(REGISTRY.hash) == 64
    by_name = {tool.name: tool for tool in REGISTRY.tools}
    assert len(by_name) == len(REGISTRY.tools) == 41
    assert {name for name, tool in by_name.items() if tool.risk == "read"} == READ_ONLY
    assert {
        name: tool.risk for name, tool in by_name.items() if tool.risk in ("low", "medium", "high")
    } == MUTATING
    assert {name for name, tool in by_name.items() if tool.risk == "forbidden"} == FORBIDDEN
    assert (len(READ_ONLY), len(MUTATING), len(FORBIDDEN)) == (24, 12, 5)
    assert {tool.name: tool.handler for tool in REGISTRY.tools if tool.handler} == {
        name: name for name in HANDLERS
    }
    # One tool runs on the probe client; every other command runs in the node it names.
    assert [tool.name for tool in COMMANDS if tool.command and tool.command.on != "node"] == [
        "http_probe"
    ]
    assert all(tool.version == 1 for tool in REGISTRY.tools)


def test_every_tool_and_every_argument_says_to_a_model_what_it_is() -> None:
    for tool in REGISTRY.tools:
        assert tool.description.endswith(".") and len(tool.description) >= 20, tool.name
        assert values.printable(tool.description), tool.name
        for name, arg in tool.args.items():
            assert arg.description and arg.description.endswith("."), (tool.name, name)
            assert values.printable(arg.description), (tool.name, name)


def test_every_category_is_a_label_of_the_fixture_taxonomy() -> None:
    labels = set(load_catalog(PLATFORM / "tests" / "fixtures" / "catalog").taxonomy.names())
    named = {label for tool in REGISTRY.tools for label in tool.applicable_categories}
    assert len(named) == 19 and named <= labels
    assert not named & {"none", "unknown"}
    # A tool that changes a node fits at least the diagnosis its name speaks of.
    fits = {tool.name: set(tool.applicable_categories) for tool in REGISTRY.tools}
    assert {"service.stopped", "database.unavailable", "resource.disk_full"} <= fits[
        "start_service"
    ]
    assert fits["start_service"] <= fits["restart_service"]
    assert {"config.syntax_error", "config.wrong_value"} <= fits["apply_config_patch"]
    # A remedy in two calls fits its diagnosis in both: a patched file is read again only
    # by a restart, and a container that keeps crashing is mended in a file it reads.
    assert "database.access_denied" in fits["apply_config_patch"] & fits["restart_service"]
    assert "container.crash_loop" in fits["apply_config_patch"] & fits["restart_service"]
    assert fits["nginx_reload"] == {"config.syntax_error", "config.wrong_value"}
    assert (
        "resource.disk_full" in fits["remove_file"] & fits["vacuum_journal"] & fits["rotate_logs"]
    )
    assert "service.stopped" not in fits["stop_service"] | fits["kill_process"]


def test_no_command_is_a_shell_s_and_every_program_is_named_without_a_directory() -> None:
    programs = set()
    for tool in COMMANDS:
        for said in (tool.command, tool.post_check):
            if said is None:
                continue
            written = elements(said.argv)
            programs.add(written[0].literal)
            assert all(one.literal not in SHELLS for one in written), tool.name
            # A path that is a whole element comes after "--": its program reads it as a
            # name, whatever it begins with.
            for at, one in enumerate(written):
                if one.alone and tool.args[str(one.argument)].type == "path":
                    assert "--" in [before.literal for before in written[:at]], tool.name
    assert programs == {
        "curl",
        "df",
        "dig",
        "docker",
        "free",
        "ip",
        "journalctl",
        "kill",
        "logrotate",
        "ls",
        "nginx",
        "pg_isready",
        "ps",
        "redis-cli",
        "rm",
        "runuser",
        "ss",
        "systemctl",
    }
    assert all("/" not in program for program in programs)


def test_a_program_reads_nothing_but_what_its_command_gives_it() -> None:
    def argv(name: str) -> tuple[Any, ...]:
        tool = REGISTRY.tool(name)
        assert tool is not None and tool.command is not None
        return tool.command.argv

    # No file of the asking user's own is read, and no part of a URL is taken as a pattern.
    assert argv("http_probe")[:3] == ("curl", "--disable", "--globoff")
    # The name asked for is given as the name, whatever word it is.
    assert argv("dns_lookup") == ("dig", "-r", "+timeout=2", "+tries=1", "-q", "{name}")
    # A container is asked for as a container: no image, network or volume of that name.
    inspected = [
        said.argv
        for tool in COMMANDS
        for said in (tool.command, tool.post_check)
        if said is not None and said.argv[:2] == ("docker", "inspect")
    ]
    assert len(inspected) == 3
    assert all(written[2] == "--type=container" for written in inspected)
    # A full container ID has 64 characters.
    containers = [
        arg for tool in REGISTRY.tools for name, arg in tool.args.items() if name == "container"
    ]
    assert len(containers) == 5
    assert all(arg.problem("a" * 64) is None and arg.problem("a" * 65) for arg in containers)
    # A service may take its whole stop or start time; a restart may take both.
    limits = {tool.name: tool.command.timeout_s for tool in COMMANDS if tool.command}
    assert (limits["start_service"], limits["stop_service"], limits["restart_service"]) == (
        120,
        120,
        210,
    )
    # The journal's and the logs' times are written one way.
    since = {
        tool.name: arg.pattern
        for tool in REGISTRY.tools
        for name, arg in tool.args.items()
        if name in ("since", "age")
    }
    assert since == {
        "tail_journal": "^[0-9]{1,4}(s|m|h|d)$",
        "query_logs": "^[0-9]{1,4}(s|m|h|d)$",
        "query_metrics": "^[0-9]{1,4}(s|m|h|d)$",
        "vacuum_journal": "^[0-9]{1,3}(m|h|d|w|y)$",
    }


def test_a_path_a_tool_removes_lies_below_fixed_roots() -> None:
    within = {
        (tool.name, name): arg.within
        for tool in REGISTRY.tools
        for name, arg in tool.args.items()
        if arg.within is not None
    }
    assert within == {("remove_file", "path"): ("/tmp", "/var/tmp", "/var/cache")}  # noqa: S108
    remove = REGISTRY.tool("remove_file")
    assert remove is not None and remove.command is not None
    assert remove.command.argv == ("rm", "--", "{path}")
    # What a model is told of the roots is what binds: the description names each root
    # and no other path.
    assert set(re.findall(r"/[a-z/]+", remove.description)) == set(within["remove_file", "path"])
    # Logs are rotated by the node's own configuration, whole: a call names no file.
    rotate = REGISTRY.tool("rotate_logs")
    assert rotate is not None and rotate.command is not None
    assert (set(rotate.args), rotate.command.argv) == (
        {"node"},
        ("logrotate", "--force", "/etc/logrotate.conf"),
    )
    for path in ("/tmp", "/var/cache", "/etc/passwd", "/tmp/../etc/passwd", "/var/tmp/../../root"):  # noqa: S108
        with pytest.raises(ArgumentError) as raised:
            check(remove, {"node": "web", "path": path}, CONTEXT)
        assert raised.value.code == "path_not_allowed"
    kill = REGISTRY.tool("kill_process")
    assert kill is not None and (kill.args["pid"].minimum, kill.args["signal"].enum) == (
        2,
        ("TERM", "KILL"),
    )


def sample(tool: Tool, name: str, arg: Arg) -> st.SearchStrategy[Any]:
    """Values ARG takes in the run of CONTEXT, and a few it does not."""
    if arg.type == "integer":
        assert arg.minimum is not None and arg.maximum is not None
        return st.integers(arg.minimum - 1, min(arg.maximum + 1, arg.minimum + 600))
    if arg.enum is not None:
        return st.sampled_from([*arg.enum, "other"])
    if arg.source == "context.nodes":
        return st.sampled_from(["web", "db", "cache"])
    if arg.source == "context.node.services":
        return st.sampled_from(
            ["nginx.service", "docker.service", "postgresql.service", "x.service"]
        )
    if arg.source == "context.node.http_endpoints":
        return st.sampled_from(["site", "admin"])
    if arg.source == "context.node.patchable_paths":
        return st.sampled_from(
            ["/etc/nginx/nginx.conf", "/etc/nginx/./nginx.conf", "/etc/nginx/other.conf"]
        )
    if arg.type == "path":
        roots = arg.within or ("/etc/nginx", "/var/log/nginx", "/etc/postgresql", "/srv")
        tails = st.sampled_from(["", "/a", "/a/../b", "/../x", "/a/./b.conf", "//c/"])
        return st.builds(lambda root, tail: root + tail, st.sampled_from(roots), tails)
    if arg.type == "text":
        return st.sampled_from(["--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y\n", "x"])
    assert arg.pattern is not None, (tool.name, name)
    return st.from_regex(arg.pattern.removeprefix("^").removesuffix("$"), fullmatch=True)


@st.composite
def calls(draw: st.DrawFn) -> tuple[Tool, dict[str, Any]]:
    tool = draw(st.sampled_from([tool for tool in REGISTRY.tools if tool.risk != "forbidden"]))
    args = {}
    for name, arg in tool.args.items():
        if arg.default is None or draw(st.booleans()):
            args[name] = draw(sample(tool, name, arg))
    return tool, args


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(call=calls())
def test_every_call_of_the_registry_is_refused_or_becomes_its_template_s_command(
    call: tuple[Tool, dict[str, Any]],
) -> None:
    tool, args = call
    try:
        checked = check(tool, args, CONTEXT)
    except ArgumentError as error:
        assert error.code in (
            "out_of_range",
            "not_allowed",
            "bad_form",
            "unknown_node",
            "not_in_context",
            "path_not_allowed",
            "not_renderable",
        )
        return
    made = render(checked)
    if tool.command is None:
        assert made is None
        return
    assert made is not None and made.on == tool.command.on
    shape(tool, tool.command.argv, made.argv, checked.values)
    if tool.post_check is not None:
        assert made.post_check is not None
        shape(tool, tool.post_check.argv, made.post_check, checked.values)
    for value in checked.args.values():
        assert isinstance(value, int) or values.is_text(value)


@pytest.mark.parametrize(
    "tool", [t for t in REGISTRY.tools if t.risk != "forbidden"], ids=lambda t: t.name
)
def test_a_call_that_leaves_out_what_it_may_passes_with_the_defaults(tool: Tool) -> None:
    given_ = {
        "node": "web",
        "unit": "nginx.service",
        "service": "nginx.service",
        "container": "web1",
        "name": "example.org",
        "endpoint": "site",
        "path": "/etc/nginx/nginx.conf",
        "diff": "--- a\n+++ b\n",
        "metric": "cpu_usage",
        "pid": 4242,
    }
    if tool.name == "remove_file":
        given_["path"] = "/var/cache/a.tmp"
    args = {name: given_[name] for name, arg in tool.args.items() if arg.default is None}
    checked = check(tool, args, CONTEXT)
    assert set(checked.args) == set(tool.args)
    # With its defaults the call becomes its template's command, element for element.
    made = render(checked)
    if tool.command is None:
        assert made is None
        return
    assert made is not None
    shape(tool, tool.command.argv, made.argv, checked.values)
    if tool.post_check is not None:
        assert made.post_check is not None
        shape(tool, tool.post_check.argv, made.post_check, checked.values)


def test_no_pattern_of_the_registry_is_slow_on_a_long_value() -> None:
    # A value is matched whole against its pattern, up to its argument's length: a pattern
    # that tries the same characters again and again would make one call wait.
    patterns = 0
    for tool in REGISTRY.tools:
        for name, arg in tool.args.items():
            if arg.pattern is None:
                continue
            patterns += 1
            for filler in ("a", "0", "-", ".", "a.", "a-0"):
                value = (filler * arg.length)[: arg.length - 1] + "!"
                started = time.perf_counter()
                assert arg.problem(value) == "bad_form", (tool.name, name)
                assert time.perf_counter() - started < 1.0, (tool.name, name)
    assert patterns >= 10
