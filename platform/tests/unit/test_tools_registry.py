"""The tool registry: what a registry file may say, and how a registry is loaded."""

from __future__ import annotations

import json
import sys
from pathlib import Path
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
    write_registry,
)
from potka_tools import template
from potka_tools.registry import (
    HANDLERS,
    Arg,
    Registry,
    RegistryError,
    Tool,
    format_schema,
    load_registry,
)
from potka_tools.template import Element, Marked


def arg(**said: Any) -> Arg:
    return Arg.model_validate_json(json.dumps(said), strict=True)


def problems(directory: Path) -> list[tuple[str, str]]:
    with pytest.raises(RegistryError) as raised:
        load_registry(directory)
    assert str(raised.value) == "; ".join(f"{path}: {why}" for path, why in raised.value.problems)
    return list(raised.value.problems)


def test_without_a_directory_the_registry_is_the_release_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A release holds its registry beside its environment.
    release = tmp_path / "release"
    write_registry(release / "tools" / "registry", [tail_journal()], version="4\n")
    monkeypatch.setattr(sys, "prefix", str(release / ".venv"))
    registry = load_registry()
    assert (registry.version, [one.name for one in registry.tools]) == (4, ["tail_journal"])
    assert registry == load_registry(release / "tools" / "registry")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "other" / ".venv"))
    with pytest.raises(RegistryError) as raised:
        load_registry()
    assert raised.value.problems == ((".", "cannot be read (FileNotFoundError)"),)


def test_a_registry_is_its_version_and_its_tools_by_name(tmp_path: Path) -> None:
    files = ([tail_journal(), restart_service()], [remove_file(), forbidden()])
    registry = load_registry(write_registry(tmp_path, *files, version="7\n"))
    assert registry.version == 7
    assert [tool.name for tool in registry.tools] == [
        "reboot",
        "remove_file",
        "restart_service",
        "tail_journal",
    ]
    restart = registry.tool("restart_service")
    assert restart is not None and registry.tool("restart") is None
    assert (restart.version, restart.risk, restart.mutating) == (3, "medium", True)
    assert restart.applicable_categories == ("service.stopped", "config.wrong_value")
    assert restart.command is not None and restart.post_check is not None
    assert restart.command.argv == ("systemctl", "restart", "{unit}")
    assert (restart.command.on, restart.command.timeout_s, restart.command.kill_after_s) == (
        "node",
        30,
        5,
    )
    assert restart.post_check.argv == ("systemctl", "is-active", "{unit}")
    unit = restart.args["unit"]
    assert (unit.type, unit.source, unit.length) == ("string", "context.node.services", 1024)
    lines = registry.tool("tail_journal")
    assert lines is not None and lines.args["lines"].default == 100
    reboot = registry.tool("reboot")
    assert reboot is not None
    assert (reboot.args, reboot.command, reboot.handler, reboot.max_output_bytes) == (
        {},
        None,
        None,
        None,
    )


def test_the_hash_counts_the_version_and_the_entries_and_nothing_else(tmp_path: Path) -> None:
    one = load_registry(write_registry(tmp_path / "one", [restart_service(), tail_journal()]))
    assert len(one.hash) == 64 and set(one.hash) <= set("0123456789abcdef")
    # Other files, another order of entries and keys, a comment, a default written out.
    explicit = changed(tail_journal(), "exec.on", "node")
    reordered = dict(reversed(list(restart_service().items())))
    two = write_registry(tmp_path / "two", [explicit], [reordered])
    (two / "tools-1.yaml").write_text("# a comment\n" + json.dumps([explicit]), encoding="utf-8")
    assert load_registry(two).hash == one.hash
    assert Registry.of(1, tuple(reversed(one.tools))) == one
    # The version counts, and so does every word of an entry.
    assert Registry.of(2, one.tools).hash != one.hash
    worded = changed(restart_service(), "description", "Restart a service.")
    assert load_registry(write_registry(tmp_path / "three", [worded, tail_journal()])).hash != (
        one.hash
    )
    # A marked literal is a word of its own: neither the plain one nor none.
    marked = changed(tail_journal(), "exec.argv", ["journalctl", {"literal": "--output={x}"}])
    plain = changed(tail_journal(), "exec.argv", ["journalctl", "--output=x"])
    hashes = {
        load_registry(write_registry(tmp_path / name, [entry])).hash
        for name, entry in (("four", marked), ("five", plain), ("six", tail_journal()))
    }
    assert len(hashes) == 3


@pytest.mark.parametrize("name", sorted(HANDLERS))
def test_a_handler_s_tool_has_exactly_its_handler_s_arguments(tmp_path: Path, name: str) -> None:
    tool = load_registry(write_registry(tmp_path, [handler(name)])).tools[0]
    assert (tool.handler, tool.command, tool.max_output_bytes) == (name, None, 65536)
    assert set(tool.args) == set(HANDLERS[name])


REFUSED: list[tuple[dict[str, Any], str]] = [
    # Names, and keys the format does not have.
    (changed(restart_service(), "name", "Restart"), "name: String should match"),
    (changed(restart_service(), "name", "a" * 64), "name: String should match"),
    (changed(restart_service(), "args._meta", {"type": "text", "max_length": 9}), "args._meta"),
    (changed(restart_service(), "shell", "sh"), "shell: Extra inputs are not permitted"),
    (changed(restart_service(), "exec.env", {}), "exec.env: Extra inputs are not permitted"),
    (changed(restart_service(), "version", "3"), "version: Input should be a valid integer"),
    (changed(restart_service(), "version", 0), "version: Input should be greater than or equal"),
    (changed(restart_service(), "description", " "), "a description is one line of printing"),
    (changed(restart_service(), "description", "a\nb"), "a description is one line of printing"),
    (changed(restart_service(), "description", "a\tb"), "a description is one line of printing"),
    (changed(restart_service(), "description", "a\u0007b"), "a description is one line of"),
    (changed(restart_service(), "description", "a\u202eb"), "a description is one line of"),
    (changed(restart_service(), "description", "x" * 1001), "description: String should have"),
    (changed(tail_journal(), "args.unit.description", "a\nb"), "a description is one line of"),
    # A key without a value is no key left out.
    (changed(tail_journal(), "args.lines.default", None), "default has no value"),
    (changed(tail_journal(), "args.unit.from", None), "from has no value"),
    (changed(restart_service(), "post_check", None), "post_check has no value"),
    (changed(restart_service(), "risk", "critical"), "risk: Input should be"),
    # Risk, mutating and the categories.
    (changed(restart_service(), "mutating", False), "risk is read exactly when mutating is false"),
    (changed(tail_journal(), "mutating", True), "risk is read exactly when mutating is false"),
    (changed(restart_service(), "applicable_categories", []), "a mutating tool lists at least"),
    (changed(tail_journal(), "applicable_categories", ["service.stopped"]), "a read-only tool"),
    (
        changed(restart_service(), "applicable_categories", ["a.b", "a.b"]),
        "applicable_categories gives a label twice",
    ),
    (changed(restart_service(), "applicable_categories", ["Service"]), "String should match"),
    # A forbidden tool is only named.
    (
        changed(forbidden(), "args", {"node": {"type": "string", "from": "context.nodes"}}),
        "a forbidden tool has no args",
    ),
    (changed(forbidden(), "applicable_categories", ["service.stopped"]), "a forbidden tool has"),
    (changed(forbidden(), "handler", "list_nodes"), "a forbidden tool has no args"),
    (changed(forbidden(), "exec", restart_service()["exec"]), "a forbidden tool has no args"),
    (changed(forbidden(), "post_check", {"argv": ["true"]}), "a forbidden tool has no args"),
    (changed(forbidden(), "max_output_bytes", 9), "a forbidden tool has no args"),
    (changed(forbidden(), "mutating", False), "risk is read exactly when mutating is false"),
    # A command or a handler, and what stands beside each.
    (changed(restart_service(), "exec", drop=True), "exactly one of exec and handler"),
    (changed(restart_service(), "handler", "list_nodes"), "exactly one of exec and handler"),
    (changed(handler("read_file"), "post_check", {"argv": ["true"]}), "post_check stands only"),
    (changed(restart_service(), "max_output_bytes", 9), "max_output_bytes stands exactly beside"),
    (changed(handler("read_file"), "max_output_bytes", drop=True), "stands exactly beside handler"),
    (changed(restart_service(), "handler", "run_script"), "handler: Input should be"),
    (changed(handler("read_file"), "max_output_bytes", 1048577), "max_output_bytes: Input"),
    # The handler that changes its target has a mutating tool, and no other has one.
    (
        {**handler("apply_config_patch"), "risk": "read", "mutating": False}
        | {"applicable_categories": []},
        "the handler apply_config_patch changes its target: its tool is mutating",
    ),
    (
        {**handler("read_file"), "risk": "low", "mutating": True}
        | {"applicable_categories": ["config.wrong_value"]},
        "the handler read_file changes nothing: its tool's risk is read",
    ),
    (changed(restart_service(), "exec.argv", []), "exec.argv: Value should have at least 1"),
    (changed(restart_service(), "exec.argv", ["systemctl", 7]), "exec.argv.1"),
    # A mapping that is no marked literal: YAML reads an unquoted {unit} as one.
    (changed(restart_service(), "exec.argv", ["a", {"literal": "x", "more": 1}]), "is quoted"),
    (changed(restart_service(), "exec.argv", ["systemctl", {"unit": None}]), "is quoted"),
    (changed(restart_service(), "post_check.argv", ["a", {"unit": None}]), "is quoted"),
    (changed(restart_service(), "exec.kill_after_s", 0), "exec.kill_after_s: Input should be"),
    (changed(restart_service(), "exec.on", "host"), "exec.on: Input should be"),
    (changed(restart_service(), "exec.timeout_s", 301), "exec.timeout_s: Input should be less"),
    (changed(restart_service(), "exec.timeout_s", 0), "exec.timeout_s: Input should be greater"),
    (changed(restart_service(), "exec.kill_after_s", 31), "exec.kill_after_s: Input should be"),
    (changed(restart_service(), "exec.max_output_bytes", 1048577), "exec.max_output_bytes"),
    (changed(restart_service(), "exec.max_output_bytes", drop=True), "exec.max_output_bytes"),
    # A command's template.
    (changed(restart_service(), "exec.argv", ["{unit}"]), "its program, is a literal"),
    (
        changed(restart_service(), "exec.argv", [{"literal": "systemctl"}, "{unit}"]),
        "its program, is a literal",
    ),
    (changed(restart_service(), "exec.argv", ["systemctl", "x{unit}"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", "{unit}.d"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", "{unit}{unit}"]), "an element is a"),
    (changed(restart_service(), "exec.argv", ["systemctl", "--a={unit} {unit}"]), "an element is"),
    (changed(restart_service(), "exec.argv", ["systemctl", "{Unit}"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", "{unit"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", "unit}"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", "{}"]), "an element is a literal"),
    (changed(restart_service(), "exec.argv", ["systemctl", ""]), "printable ASCII and not empty"),
    (changed(restart_service(), "exec.argv", ["systemctl", "a\tb"]), "printable ASCII and not"),
    (
        changed(restart_service(), "exec.argv", ["systemctl", {"literal": "caf\u00e9"}]),
        "printable ASCII and not empty",
    ),
    (changed(restart_service(), "exec.argv", ["systemctl", {"literal": ""}]), "exec.argv.1"),
    (changed(restart_service(), "exec.argv", ["sh", "-c", "{unit}"]), "never a shell's"),
    (changed(restart_service(), "exec.argv", ["/bin/bash", "{unit}"]), "never a shell's"),
    (changed(restart_service(), "exec.argv", ["runuser", "--", "dash"]), "never a shell's"),
    (changed(restart_service(), "exec.argv", ["rbash", "-c", "{unit}"]), "never a shell's"),
    (changed(restart_service(), "exec.argv", ["/usr/bin/yash", "{unit}"]), "never a shell's"),
    (
        changed(restart_service(), "exec.argv", ["systemctl", {"literal": "sh"}]),
        "never a shell's",
    ),
    (
        changed(restart_service(), "exec.argv", ["systemctl", "{name}"]),
        "a placeholder names an argument of the tool, and not node: name",
    ),
    (
        changed(restart_service(), "exec.argv", ["systemctl", "--host={node}"]),
        "a placeholder names an argument of the tool, and not node: node",
    ),
    (changed(restart_service(), "post_check.argv", ["{unit}"]), "its program, is a literal"),
    (changed(restart_service(), "post_check.argv", ["a", "{b}"]), "and not node: b"),
    (changed(restart_service(), "post_check.argv", ["zsh"]), "never a shell's"),
    # A value that is a whole element of a command.
    (
        changed(
            changed(tail_journal(), "exec.argv", ["journalctl", "{lines}"]),
            "args.lines.minimum",
            -5,
        ),
        "an integer that stands alone is not negative: lines",
    ),
    (
        changed(
            changed(restart_service(), "post_check.argv", ["kill", "{signal}"]),
            "args.signal",
            {"type": "string", "enum": ["TERM", "-9"]},
        ),
        "a value that stands alone does not begin with -: signal",
    ),
    (
        changed(
            changed(tail_journal(), "exec.argv", ["journalctl", "{since}"]),
            "args.since",
            {"type": "string", "pattern": "^-?[0-9]+[smh]$", "default": "-1h"},
        ),
        "a value that stands alone does not begin with -: since",
    ),
    # The argument node.
    (changed(restart_service(), "args.node", drop=True), "the argument node, a string from"),
    (
        changed(restart_service(), "args.node", {"type": "string", "pattern": "^[a-z]+$"}),
        "the argument node, a string from context.nodes",
    ),
    # A string.
    (changed(restart_service(), "args.unit", {"type": "string"}), "a pattern, an enum or a from"),
    (
        changed(
            restart_service(), "args.unit", {"type": "string", "enum": ["a"], "pattern": "^a$"}
        ),
        "an enum stands without a pattern",
    ),
    (
        changed(restart_service(), "args.unit.from", "context.node.readable_paths"),
        "a string's from is one of",
    ),
    (changed(restart_service(), "args.unit.from", "context.node"), "args.unit.from: Input should"),
    (changed(restart_service(), "args.unit.within", ["/srv"]), "a string has no within"),
    (changed(restart_service(), "args.unit.minimum", 1), "a string has no minimum"),
    (changed(restart_service(), "args.unit.max_length", 1025), "max_length is at most 1024"),
    (changed(restart_service(), "args.unit.max_length", 0), "args.unit.max_length"),
    (changed(restart_service(), "args.unit.pattern", "[a-z]+$"), "begins with ^ and ends with $"),
    (changed(restart_service(), "args.unit.pattern", "^[a-z]+"), "begins with ^ and ends with $"),
    (changed(restart_service(), "args.unit.pattern", r"^[a-z]+\$"), "begins with ^ and ends"),
    (changed(restart_service(), "args.unit.pattern", "^[a-z+$"), "Python compiles"),
    (
        changed(restart_service(), "args.unit", {"type": "string", "enum": ["a", "a"]}),
        "enum gives a value twice",
    ),
    (
        changed(restart_service(), "args.unit", {"type": "string", "enum": ["a", "b\n"]}),
        "an enum's value is printable ASCII",
    ),
    (changed(restart_service(), "args.unit", {"type": "string", "enum": []}), "args.unit.enum"),
    (changed(restart_service(), "args.unit", {"type": "boolean"}), "args.unit.type: Input should"),
    (changed(restart_service(), "args.unit.description", ""), "args.unit.description"),
    # An integer.
    (changed(tail_journal(), "args.lines.maximum", drop=True), "a minimum and a maximum"),
    (changed(tail_journal(), "args.lines.minimum", 501), "bounds are in order"),
    (changed(tail_journal(), "args.lines.maximum", 2**31), "bounds are in order and within"),
    (changed(tail_journal(), "args.lines.minimum", -(2**31)), "bounds are in order and within"),
    (changed(tail_journal(), "args.lines.pattern", "^1$"), "an integer has no pattern"),
    (changed(tail_journal(), "args.lines.from", "context.nodes"), "an integer has no from"),
    (changed(tail_journal(), "args.lines.maximum", 500.0), "args.lines.maximum"),
    # A default.
    (changed(tail_journal(), "args.lines.default", 0), "the default does not fit"),
    (changed(tail_journal(), "args.lines.default", True), "args.lines.default"),
    (changed(tail_journal(), "args.lines.default", "100"), "the default does not fit"),
    (changed(tail_journal(), "args.lines.default", 1.5), "args.lines.default"),
    (changed(restart_service(), "args.unit.default", "nginx.service"), "has no default"),
    (changed(remove_file(), "args.path.default", "/tmp"), "below a root of within"),  # noqa: S108
    (changed(remove_file(), "args.path.default", "/tmp/a/../b"), "below a root"),  # noqa: S108
    (changed(remove_file(), "args.path.default", "/var/cachex/a"), "below a root of within"),
    (changed(remove_file(), "args.path.default", "tmp/a"), "the default does not fit"),
    # A path.
    (changed(remove_file(), "args.path.within", drop=True), "exactly one of from and within"),
    (
        changed(remove_file(), "args.path.from", "context.node.readable_paths"),
        "exactly one of from and within",
    ),
    (
        changed(remove_file(), "args.path", {"type": "path", "from": "context.node.services"}),
        "a path's from is one of",
    ),
    (changed(remove_file(), "args.path.pattern", "^/tmp/.*$"), "a path has no pattern"),
    (changed(remove_file(), "args.path.max_length", 4097), "max_length is at most 4096"),
    (changed(remove_file(), "args.path.within", []), "args.path.within"),
    (changed(remove_file(), "args.path.within", ["/tmp", "/tmp"]), "within gives a root twice"),  # noqa: S108
    (changed(remove_file(), "args.path.within", ["/"]), "a root of within is"),
    (changed(remove_file(), "args.path.within", ["/tmp/"]), "a root of within is"),  # noqa: S108
    (changed(remove_file(), "args.path.within", ["tmp"]), "a root of within is"),
    (changed(remove_file(), "args.path.within", ["/var/../etc"]), "a root of within is"),
    (changed(remove_file(), "args.path.within", ["/proc"]), "a root of within is"),
    (changed(remove_file(), "args.path.within", ["/dev/shm"]), "a root of within is"),  # noqa: S108
    (changed(remove_file(), "args.path.within", ["/sys/fs"]), "a root of within is"),
    # A text.
    (
        changed(restart_service(), "args.note", {"type": "text", "max_length": 100}),
        "a text is an argument of a handler's tool only",
    ),
    (
        changed(handler("apply_config_patch"), "args.diff", {"type": "text"}),
        "a text has a max_length",
    ),
    (
        changed(handler("apply_config_patch"), "args.diff.max_length", 262145),
        "max_length is at most 262144",
    ),
    (changed(handler("apply_config_patch"), "args.diff.pattern", "^a$"), "a text has no pattern"),
    (
        changed(handler("apply_config_patch"), "args.diff.default", "x"),
        "and a text, has no default",
    ),
    # A handler's arguments.
    (
        changed(
            handler("list_nodes"), "args", {"node": {"type": "string", "from": "context.nodes"}}
        ),
        "has exactly the arguments: none",
    ),
    (changed(handler("read_file"), "args.path", drop=True), "exactly the arguments: node, path"),
    (
        changed(
            handler("read_file"), "args.lines", {"type": "integer", "minimum": 1, "maximum": 2}
        ),
        "exactly the arguments: node, path",
    ),
    (
        changed(handler("read_file"), "args.path", {"type": "path", "within": ["/etc"]}),
        "reads path as a path from context.node.readable_paths",
    ),
    (
        changed(handler("read_file"), "args.path.from", "context.node.patchable_paths"),
        "reads path as a path from context.node.readable_paths",
    ),
    (
        changed(handler("apply_config_patch"), "args.path.from", "context.node.readable_paths"),
        "reads path as a path from context.node.patchable_paths",
    ),
    (
        changed(handler("apply_config_patch"), "args.diff", {"type": "string", "pattern": "^.+$"}),
        "reads diff as a text",
    ),
    (
        changed(handler("query_logs"), "args.level", {"type": "string", "pattern": "^[a-z]+$"}),
        "reads level as a string with an enum",
    ),
    (
        changed(handler("query_logs"), "args.since", {"type": "string", "enum": ["1h"]}),
        "reads since as a string with a pattern",
    ),
    (
        changed(handler("query_logs"), "args.service", {"type": "string", "pattern": "^[a-z]+$"}),
        "reads service as a string from context.node.services",
    ),
    (
        changed(handler("query_metrics"), "args.metric", {"type": "string", "pattern": "^.+$"}),
        "reads metric as a string with an enum",
    ),
    (
        changed(handler("query_metrics"), "args.node", {"type": "string", "enum": ["web"]}),
        "reads node as a string from context.nodes",
    ),
]


@pytest.mark.parametrize(("entry", "reason"), REFUSED, ids=[reason for _, reason in REFUSED])
def test_an_entry_that_breaks_a_form_rule_is_refused(
    tmp_path: Path, entry: dict[str, Any], reason: str
) -> None:
    found = problems(write_registry(tmp_path, [entry, forbidden("flush_firewall")]))
    # One problem; two where a value may have one of two forms and has neither.
    assert len(found) in (1, 2)
    for path, why in found:
        assert path == "tools-1.yaml"
        assert why.startswith(f"{entry['name']}: ") and reason in why


def test_what_an_argument_may_say_beside_its_type_is_held_to_its_type() -> None:
    # Each of these loads; the rule beside it is the one a neighbour above breaks.
    assert arg(type="string", pattern="^a\\\\$").problem("a\\") is None
    assert arg(type="string", pattern="^a\\\\$").problem("a") == "bad_form"
    assert arg(type="string", enum=["a", "b"], default="b").default == "b"
    assert arg(type="path", within=["/var/cache"], default="/var/cache/" + "a" * 255).within
    for said, reason in (
        ({"type": "string", "enum": ["a"], "default": "b"}, "the default does not fit"),
        ({"type": "string", "pattern": "^[a-z]+$", "default": "A"}, "the default does not fit"),
        (
            {"type": "string", "pattern": "^[a-z]+$", "max_length": 3, "default": "abcd"},
            "the default does not fit",
        ),
        ({"type": "string", "enum": ["a"], "from": "context.node.services"}, "an enum stands"),
        ({"type": "integer", "minimum": 1, "maximum": 2, "max_length": 5}, "has no max_length"),
        ({"type": "path", "within": ["/var/caf\u00e9"]}, "a root of within is"),
        ({"type": "path", "within": ["/var/" + "a" * 256]}, "a root of within is"),
        (
            {"type": "path", "within": ["/var/cache"], "default": "/var/cache/" + "a" * 256},
            "a path's default is in its normal form",
        ),
    ):
        with pytest.raises(ValueError, match=reason):
            arg(**said)


def test_a_handler_s_argument_has_what_its_handler_reads_and_nothing_of_another_kind() -> None:
    since = {"type": "string", "pattern": "^[0-9]+h$", "from": "context.nodes"}
    with pytest.raises(ValueError, match="reads since as a string with a pattern"):
        tool(changed(handler("query_logs"), "args.since", since))
    # A pattern beside the source a handler asks for is no other kind.
    service = {"type": "string", "pattern": "^[a-z.]+$", "from": "context.node.services"}
    assert tool(changed(handler("query_logs"), "args.service", service)).args["service"].pattern


def test_a_command_is_written_as_literals_placeholders_and_options() -> None:
    written = (
        "journalctl",
        "--no-pager",
        "--lines={lines}",
        "--sort=-%{by}",
        "-{signal}",
        "--",
        "{unit}",
        Marked(literal="--output={{json .State}}"),
        "{unit}",
    )
    assert template.elements(written) == (
        Element("journalctl", None),
        Element("--no-pager", None),
        Element("--lines=", "lines"),
        Element("--sort=-%", "by"),
        Element("-", "signal"),
        Element("--", None),
        Element("", "unit"),
        Element("--output={{json .State}}", None),
        Element("", "unit"),
    )
    assert template.placeholders(written) == ("lines", "by", "signal", "unit", "unit")
    assert template.standalone(written) == {"unit"}
    assert [one.alone for one in template.elements(written)].count(True) == 2
    # In an option a value may be negative or begin with a dash: it is no element of its own.
    negative = changed(tail_journal(), "args.lines.minimum", -5)
    assert tool(negative).args["lines"].minimum == -5
    since = {"type": "string", "pattern": "^-?[0-9]+[smh]$", "default": "-1h"}
    dashed = changed(
        changed(tail_journal(), "args.since", since), "exec.argv", ["a", "--s={since}"]
    )
    assert tool(dashed).args["since"].default == "-1h"


def test_every_problem_of_a_registry_is_reported_with_its_file_and_entry(tmp_path: Path) -> None:
    bad = changed(restart_service(), "args.unit", {"type": "string"})
    bad["exec"]["timeout_s"] = 0
    nameless = changed(tail_journal(), "name", drop=True)
    entries: list[Any] = [bad, nameless, 7]
    write_registry(tmp_path, entries, [restart_service(), tail_journal()])
    assert problems(tmp_path) == [
        (
            "tools-1.yaml",
            "restart_service: args.unit: Value error, a string has a pattern, an enum or a from",
        ),
        (
            "tools-1.yaml",
            "restart_service: exec.timeout_s: Input should be greater than or equal to 1",
        ),
        ("tools-1.yaml", "entry 2: name: Field required"),
        ("tools-1.yaml", "entry 3: Input should be an object"),
    ]
    # No message shows what an entry holds: the place and the rule only.
    secret = changed(restart_service(), "exec.argv", ["systemctl", {"literal": 7, "key": "s3cret"}])
    assert "s3cret" not in str(problems(write_registry(tmp_path / "other", [secret])))
    # A key an entry should not have is shown only when it has the form of a name.
    keyed = changed(restart_service(), "odd_key", 1)
    keyed["args"]["x\u001b[2J; VERSION: y"] = {"type": "string", "pattern": "^a$"}
    keyed["bad key"] = 1
    assert problems(write_registry(tmp_path / "keys", [keyed])) == [
        (
            "tools-1.yaml",
            "restart_service: args.?.[key]: String should match pattern '^[a-z][a-z0-9_]{0,62}$'",
        ),
        ("tools-1.yaml", "restart_service: odd_key: Extra inputs are not permitted"),
        ("tools-1.yaml", "restart_service: ?: Extra inputs are not permitted"),
    ]
    # A file's name that is not printable ASCII is not shown either.
    named = write_registry(tmp_path / "names", [restart_service()])
    (named / "caf\u00e9.txt").write_text("", encoding="utf-8")
    (named / "caf\u00e9.yaml").write_text("[]", encoding="utf-8")
    assert problems(named) == [
        ("(a name that is not printable ASCII)", "neither VERSION nor a .yaml file"),
        ("(a name that is not printable ASCII)", "not a list of tool entries"),
    ]


def test_a_name_occurs_once_in_the_whole_registry(tmp_path: Path) -> None:
    write_registry(tmp_path, [restart_service(), restart_service()], [tail_journal()])
    assert problems(tmp_path) == [("tools-1.yaml", "restart_service: also in tools-1.yaml")]
    write_registry(tmp_path, [restart_service()], [tail_journal(), restart_service()])
    assert problems(tmp_path) == [("tools-2.yaml", "restart_service: also in tools-1.yaml")]


@pytest.mark.parametrize(
    ("version", "problem"),
    [
        ("", "not one positive integer"),
        ("0\n", "not one positive integer"),
        ("01\n", "not one positive integer"),
        ("1.0\n", "not one positive integer"),
        (" 1\n", "not one positive integer"),
        ("1\n\n", "not one positive integer"),
        ("1\r\n", "not one positive integer"),
        ("1\r", "not one positive integer"),
        ("1\n2\n", "not one positive integer"),
        ("-1\n", "not one positive integer"),
        ("1" * 10, "not one positive integer"),
        ("\u0661\n", "not one positive integer"),
    ],
)
def test_the_version_is_one_positive_integer(tmp_path: Path, version: str, problem: str) -> None:
    write_registry(tmp_path, [restart_service()], version=version)
    assert problems(tmp_path) == [("VERSION", problem)]


def test_a_version_may_stand_without_a_final_newline(tmp_path: Path) -> None:
    assert load_registry(write_registry(tmp_path, [restart_service()], version="12")).version == 12
    assert load_registry(write_registry(tmp_path / "none", version="3\n")).tools == ()


def test_the_directory_holds_the_version_and_yaml_files_and_nothing_else(tmp_path: Path) -> None:
    assert problems(tmp_path / "missing") == [(".", "cannot be read (FileNotFoundError)")]
    (tmp_path / "file").write_text("1\n", encoding="utf-8")
    assert problems(tmp_path / "file") == [(".", "cannot be read (NotADirectoryError)")]
    directory = write_registry(tmp_path / "registry", [restart_service()])
    (directory / "VERSION").unlink()
    (directory / "README.md").write_text("words\n", encoding="utf-8")
    (directory / "tools.yml").write_text("[]\n", encoding="utf-8")
    (directory / "more.yaml").mkdir()
    (directory / "linked.yaml").symlink_to(directory / "tools-1.yaml")
    assert problems(directory) == [
        ("README.md", "neither VERSION nor a .yaml file"),
        ("linked.yaml", "not a file"),
        ("more.yaml", "not a file"),
        ("tools.yml", "neither VERSION nor a .yaml file"),
        ("VERSION", "missing"),
    ]


def test_a_file_that_cannot_be_read_is_a_problem_with_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = write_registry(tmp_path, [restart_service()])
    read = Path.read_text

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.parent == directory:
            raise PermissionError(13, "Permission denied")
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert problems(directory) == [
        ("VERSION", "cannot be read (PermissionError)"),
        ("tools-1.yaml", "cannot be read (PermissionError)"),
    ]


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"\xff\xfe", "not UTF-8"),
        (b"- name: [\n", "not YAML of the platform's subset: a syntax error (line 2, column 1)"),
        (
            b"- &a {name: x}\n- *a\n",
            "not YAML of the platform's subset: an alias (line 2, column 3)",
        ),
        (b"{}\n", "not a list of tool entries"),
        (b"[]\n", "not a list of tool entries"),
        (b"", "not a list of tool entries"),
        (b"restart_service\n", "not a list of tool entries"),
    ],
)
def test_a_file_that_is_no_list_of_entries_is_refused(
    tmp_path: Path, content: bytes, problem: str
) -> None:
    directory = write_registry(tmp_path)
    (directory / "tools.yaml").write_bytes(content)
    assert problems(directory) == [("tools.yaml", problem)]


@pytest.mark.parametrize(
    ("said", "value", "code"),
    [
        ({"type": "string", "pattern": r"^[a-z]+\.service$"}, "nginx.service", None),
        ({"type": "string", "pattern": r"^[a-z]+\.service$"}, "nginx.service\n", "bad_form"),
        ({"type": "string", "pattern": r"^[a-z]+\.service$"}, "x nginx.service", "bad_form"),
        ({"type": "string", "pattern": r"^\w+$"}, "caf\u00e9", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "a\tb", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "a\x00b", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "a\u202eb", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "\ud800", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, "a" * 1024, None),
        ({"type": "string", "pattern": "^.*$"}, "a" * 1025, "bad_form"),
        ({"type": "string", "pattern": "^.*$", "max_length": 3}, "abcd", "bad_form"),
        ({"type": "string", "pattern": "^.*$"}, 7, "wrong_type"),
        ({"type": "string", "pattern": "^.*$"}, None, "wrong_type"),
        ({"type": "string", "pattern": "^.*$"}, ["a"], "wrong_type"),
        ({"type": "string", "from": "context.nodes"}, "web", None),
        ({"type": "string", "enum": ["cpu", "mem"]}, "mem", None),
        ({"type": "string", "enum": ["cpu", "mem"]}, "disk", "not_allowed"),
        ({"type": "string", "enum": ["cpu", "mem"]}, "", "not_allowed"),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 1, None),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 500, None),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 0, "out_of_range"),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 501, "out_of_range"),
        ({"type": "integer", "minimum": 0, "maximum": 1}, True, "wrong_type"),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 1.0, "wrong_type"),
        ({"type": "integer", "minimum": 1, "maximum": 500}, 1e3, "wrong_type"),
        ({"type": "integer", "minimum": 1, "maximum": 500}, "1", "wrong_type"),
        ({"type": "path", "within": ["/srv"]}, "/srv/a", None),
        ({"type": "path", "within": ["/srv"]}, "/etc/../srv/a/", None),
        ({"type": "path", "within": ["/srv"]}, "srv/a", "bad_form"),
        ({"type": "path", "within": ["/srv"]}, "/srv/caf\u00e9", "bad_form"),
        ({"type": "path", "within": ["/srv"]}, "/" + "a" * 4096, "bad_form"),
        ({"type": "path", "within": ["/srv"]}, "/" + "a" * 4095, None),
        ({"type": "path", "within": ["/srv"]}, 7, "wrong_type"),
        ({"type": "text", "max_length": 20}, "--- a\n+++ b\n\tcaf\u00e9", None),
        ({"type": "text", "max_length": 20}, "", "bad_form"),
        ({"type": "text", "max_length": 20}, "a" * 21, "bad_form"),
        ({"type": "text", "max_length": 20}, "a\rb", "bad_form"),
        ({"type": "text", "max_length": 20}, "a\x1bb", "bad_form"),
        ({"type": "text", "max_length": 20}, "a\u202eb", "bad_form"),
        ({"type": "text", "max_length": 20}, "a\u2028b", "bad_form"),
        ({"type": "text", "max_length": 20}, "a\ud800b", "bad_form"),
        ({"type": "text", "max_length": 20}, "a\ue000b", "bad_form"),
        ({"type": "text", "max_length": 20}, b"a", "wrong_type"),
    ],
)
def test_a_value_is_held_to_what_its_argument_says_without_a_context(
    said: dict[str, Any], value: object, code: str | None
) -> None:
    assert arg(**said).problem(value) == code


def test_an_integer_too_long_to_print_is_out_of_range_and_no_other_error() -> None:
    # Python refuses to print an integer of more than 4,300 digits; nothing here prints one.
    assert arg(type="integer", minimum=1, maximum=500).problem(10**5000) == "out_of_range"
    assert arg(type="integer", minimum=1, maximum=500).problem(-(10**5000)) == "out_of_range"


def test_the_exported_schema_takes_what_the_models_take_and_says_what_a_schema_can() -> None:
    schema = format_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    entries = [restart_service(), tail_journal(), remove_file(), forbidden()]
    entries += [handler(name) for name in sorted(HANDLERS)]
    validator.validate(entries)
    for entry in entries:
        assert Tool.model_validate_json(json.dumps(entry), strict=True).name == entry["name"]
    # Unknown keys, types and the closed lists are the schema's to say...
    for broken in (
        changed(restart_service(), "shell", "sh"),
        changed(restart_service(), "risk", "critical"),
        changed(restart_service(), "args.unit.from", "context.node"),
        changed(restart_service(), "exec.timeout_s", 301),
        changed(restart_service(), "version", "3"),
    ):
        assert not validator.is_valid([broken])
    # ...what the fields of one entry say about each other is the models'.
    assert validator.is_valid([changed(restart_service(), "mutating", False)])
    properties = schema["$defs"]["Arg"]["properties"]
    assert "from" in properties and "source" not in properties
    assert all("description" in said for said in properties.values())
    assert "exec" in schema["$defs"]["Tool"]["properties"]
