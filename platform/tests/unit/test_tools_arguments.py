"""Checking a call: what passes, what is refused and with which code, and what an error says."""

from __future__ import annotations

import json
from typing import Any, get_args

import jsonschema
import pytest
from hypothesis import given
from hypothesis import strategies as st
from potka_testing.tool_context import context
from potka_testing.tools import (
    changed,
    forbidden,
    handler,
    http_probe,
    remove_file,
    restart_service,
    tail_journal,
    tool,
)
from potka_tools import values
from potka_tools.arguments import (
    META_SCHEMA,
    ArgumentError,
    Call,
    ErrorCode,
    Meta,
    argument_schema,
    check,
    check_meta,
)
from potka_tools.context import Context

CONTEXT = context()
RESTART = tool(restart_service())
TAIL = tool(tail_journal())
REMOVE = tool(remove_file())
PROBE = tool(http_probe())
READ = tool(handler("read_file"))
PATCH = tool(handler("apply_config_patch"))
LOGS = tool(handler("query_logs"))
NODES = tool(handler("list_nodes"))
REBOOT = tool(forbidden())
META = {"action_confidence": 0.7, "rationale": "It is down.", "expected_effect": "It runs."}


def refusal(used: Any, args: object, run: Context = CONTEXT) -> ArgumentError:
    with pytest.raises(ArgumentError) as raised:
        check(used, args, run)
    return raised.value


def test_a_checked_call_holds_its_checked_form_and_what_each_argument_is_in_a_command() -> None:
    call = check(RESTART, {"unit": "nginx.service", "node": "web"}, CONTEXT)
    assert call == Call(
        RESTART, "web", {"node": "web", "unit": "nginx.service"}, {"unit": "nginx.service"}
    )
    # A default is filled in, and an integer is its decimal digits.
    call = check(TAIL, {"node": "web", "unit": "webapp.service"}, CONTEXT)
    assert call.args == {"lines": 100, "node": "web", "unit": "webapp.service"}
    assert call.values == {"lines": "100", "unit": "webapp.service"}
    assert (
        check(TAIL, {"node": "web", "unit": "nginx.service", "lines": 7}, CONTEXT).values["lines"]
        == "7"
    )
    # A path is made normal, and only its normal form goes on.
    call = check(REMOVE, {"node": "db", "path": "/var//cache/./a/../b/"}, CONTEXT)
    assert (call.args["path"], call.values["path"]) == ("/var/cache/b", "/var/cache/b")
    # An endpoint's name is its URL in a command, and the URL's host is the alias.
    call = check(PROBE, {"node": "web", "endpoint": "admin", "via": "db"}, CONTEXT)
    assert call.args == {"endpoint": "admin", "node": "web", "via": "db"}
    assert call.values == {"endpoint": "https://web:8443/health", "via": "db"}
    assert (
        check(PROBE, {"node": "web", "endpoint": "site", "via": "web"}, CONTEXT).values["endpoint"]
        == "http://web:80/"
    )
    # A text is checked and never part of a command; a tool without a node has none.
    diff = "--- a\n+++ b\n@@ -1 +1 @@\n-x\n+\tcaf\u00e9\n"
    patch = {"node": "web", "path": "/etc/nginx/sites-available/default", "diff": diff}
    call = check(PATCH, patch, CONTEXT)
    assert (call.args["diff"], call.values) == (diff, {"path": patch["path"]})
    assert check(NODES, {}, CONTEXT) == Call(NODES, None, {}, {})


def test_a_value_may_begin_with_a_dash_only_inside_an_option() -> None:
    args = {"node": "web", "unit": "nginx.service", "since": "-1h"}
    assert check(OPTION, args, CONTEXT).values["since"] == "-1h"
    assert check(DASHED, {**args, "since": "15m"}, CONTEXT).values["since"] == "15m"
    assert check(DASHED, {"node": "web", "unit": "nginx.service"}, CONTEXT).args["since"] == "1h"


def test_a_checked_call_cannot_be_changed() -> None:
    call = check(READ, {"node": "web", "path": "/etc/nginx/a"}, CONTEXT)
    assert check(ALONE, {"node": "web", "unit": "nginx.service"}, CONTEXT).values["since"] == "1h"
    with pytest.raises(TypeError):
        call.args["path"] = "/etc/shadow"  # type: ignore[index]
    with pytest.raises(TypeError):
        call.values["path"] = "/etc/shadow"  # type: ignore[index]
    with pytest.raises(AttributeError):
        call.node = "db"  # type: ignore[misc]
    assert (call.args["path"], call.values["path"]) == ("/etc/nginx/a", "/etc/nginx/a")


def test_what_can_be_patched_can_be_read_and_an_allowed_path_itself_is_allowed() -> None:
    for path in ("/etc/nginx", "/etc/nginx/conf.d/a.conf", "/srv/conf", "/srv/conf/app.ini"):
        assert check(READ, {"node": "web", "path": path}, CONTEXT).args["path"] == path
    assert check(PATCH, {"node": "web", "path": "/srv/conf/a", "diff": "x"}, CONTEXT).node == "web"


# The context with the refused roots among what may be read.
_PROC = json.loads(CONTEXT.model_dump_json())
_PROC["nodes"][0]["readable_paths"] += ["/proc", "/dev/shm", "/sys/fs/cgroup", "/dev"]  # noqa: S108
WIDE = Context.model_validate_json(json.dumps(_PROC), strict=True)
# A tool whose pattern lets a value begin with a dash: alone in the command that checks
# the tool afterwards, and in an option of the tool's own.
SINCE = {"type": "string", "pattern": "^-?[0-9]+[smh]$", "default": "1h"}
DASHED = tool(
    changed(
        changed(
            changed(restart_service(), "args.since", SINCE),
            "exec.argv",
            ["journalctl", "--since={since}", "{unit}"],
        ),
        "post_check.argv",
        ["journalctl", "--since", "{since}"],
    )
)
OPTION = tool(
    changed(
        changed(tail_journal(), "args.since", SINCE),
        "exec.argv",
        ["journalctl", "--since={since}", "--unit={unit}", "--lines={lines}"],
    )
)
# The same value alone in the tool's own command, which has no command that checks it.
ALONE = tool(
    changed(
        changed(
            changed(restart_service(), "args.since", SINCE),
            "exec.argv",
            ["journalctl", "--since", "{since}", "--unit={unit}"],
        ),
        "post_check",
        drop=True,
    )
)
LONG = "a" * 256
REFUSALS: list[tuple[Any, object, str, str | None]] = [
    (RESTART, None, "not_an_object", None),
    (RESTART, {1: "web", "unit": "nginx.service"}, "not_an_object", None),
    (RESTART, [], "not_an_object", None),
    (RESTART, "node=web", "not_an_object", None),
    (RESTART, 7, "not_an_object", None),
    (REBOOT, {}, "forbidden", None),
    (REBOOT, {"node": "web"}, "forbidden", None),
    (REBOOT, None, "forbidden", None),
    # An argument the tool does not have; its name is given back only when it is one.
    (RESTART, {"node": "web", "unit": "nginx.service", "force": True}, "unknown_argument", "force"),
    (RESTART, {"node": "web", "unit": "nginx.service", "_meta": META}, "unknown_argument", "_meta"),
    (RESTART, {"node": "web", "unit": "nginx.service", "x\ny": 1}, "unknown_argument", None),
    (RESTART, {"node": "web", "unit": "nginx.service", "X" * 80: 1}, "unknown_argument", None),
    (NODES, {"node": "web"}, "unknown_argument", "node"),
    # Unknown before missing, missing before a value, and the first argument in name order.
    (RESTART, {"node": 7, "zz": 1}, "unknown_argument", "zz"),
    (RESTART, {"node": 7}, "missing_argument", "unit"),
    (RESTART, {}, "missing_argument", "node"),
    (RESTART, {"node": 7, "unit": 7}, "wrong_type", "node"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": "5"}, "wrong_type", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": True}, "wrong_type", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": 5.0}, "wrong_type", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": 1e2}, "wrong_type", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": None}, "wrong_type", "lines"),
    (RESTART, {"node": None, "unit": "nginx.service"}, "wrong_type", "node"),
    (RESTART, {"node": ["web"], "unit": "nginx.service"}, "wrong_type", "node"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": 0}, "out_of_range", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": 501}, "out_of_range", "lines"),
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": -1}, "out_of_range", "lines"),
    (
        LOGS,
        {"node": "web", "service": "nginx.service", "since": "1h", "level": "debug"},
        "not_allowed",
        "level",
    ),
    (
        LOGS,
        {"node": "web", "service": "nginx.service", "since": "1 hour", "level": "info"},
        "bad_form",
        "since",
    ),
    # A pattern is matched whole; a string is printable ASCII, not empty, not too long.
    (RESTART, {"node": "web", "unit": "nginx"}, "bad_form", "unit"),
    (RESTART, {"node": "web", "unit": "nginx.service\n"}, "bad_form", "unit"),
    (RESTART, {"node": "web", "unit": "--now nginx.service"}, "bad_form", "unit"),
    (RESTART, {"node": "web", "unit": "ngin\u0445.service"}, "bad_form", "unit"),
    (RESTART, {"node": "web", "unit": ""}, "bad_form", "unit"),
    (RESTART, {"node": "", "unit": "nginx.service"}, "bad_form", "node"),
    (RESTART, {"node": "web\x00", "unit": "nginx.service"}, "bad_form", "node"),
    (RESTART, {"node": "w" * 1025, "unit": "nginx.service"}, "bad_form", "node"),
    (PATCH, {"node": "web", "path": "/srv/conf/a", "diff": ""}, "bad_form", "diff"),
    (PATCH, {"node": "web", "path": "/srv/conf/a", "diff": "a\x1b[2Jb"}, "bad_form", "diff"),
    (PATCH, {"node": "web", "path": "/srv/conf/a", "diff": "x" * 65537}, "bad_form", "diff"),
    # The node, then what the node has.
    (RESTART, {"node": "cache", "unit": "nginx.service"}, "unknown_node", "node"),
    (RESTART, {"node": "Web", "unit": "nginx.service"}, "unknown_node", "node"),
    (RESTART, {"node": "cache", "unit": "none.service"}, "unknown_node", "node"),
    (RESTART, {"node": "web", "unit": "postgresql.service"}, "not_in_context", "unit"),
    (RESTART, {"node": "db", "unit": "nginx.service"}, "not_in_context", "unit"),
    (PROBE, {"node": "web", "endpoint": "raw", "via": "db"}, "not_in_context", "endpoint"),
    (PROBE, {"node": "web", "endpoint": "none", "via": "db"}, "not_in_context", "endpoint"),
    (PROBE, {"node": "db", "endpoint": "site", "via": "db"}, "not_in_context", "endpoint"),
    (PROBE, {"node": "web", "endpoint": "site", "via": "cache"}, "not_in_context", "via"),
    # A path: one that has no normal form, then where it lies.
    (READ, {"node": "web", "path": "etc/nginx"}, "bad_form", "path"),
    (READ, {"node": "web", "path": "/../etc/nginx"}, "bad_form", "path"),
    (READ, {"node": "web", "path": "/etc/nginx/../../../etc/nginx"}, "bad_form", "path"),
    (READ, {"node": "web", "path": f"/etc/nginx/{LONG}"}, "bad_form", "path"),
    (READ, {"node": "web", "path": "/etc/passwd"}, "path_not_allowed", "path"),
    (READ, {"node": "web", "path": "/etc"}, "path_not_allowed", "path"),
    (READ, {"node": "web", "path": "/"}, "path_not_allowed", "path"),
    (READ, {"node": "web", "path": "/etc/nginx2/a"}, "path_not_allowed", "path"),
    (READ, {"node": "web", "path": "/etc/nginx/../passwd"}, "path_not_allowed", "path"),
    (
        READ,
        {"node": "web", "path": "/etc/nginx/./../../srv/webapp/../../root/.ssh"},
        "path_not_allowed",
        "path",
    ),
    (READ, {"node": "db", "path": "/etc/nginx/nginx.conf"}, "path_not_allowed", "path"),
    (
        PATCH,
        {"node": "web", "path": "/etc/nginx/nginx.conf", "diff": "x"},
        "path_not_allowed",
        "path",
    ),
    (PATCH, {"node": "db", "path": "/srv/conf/a", "diff": "x"}, "path_not_allowed", "path"),
    # A value that is a whole element of a command, the checking command's too.
    (DASHED, {"node": "web", "unit": "nginx.service", "since": "-1h"}, "not_renderable", "since"),
    (ALONE, {"node": "web", "unit": "nginx.service", "since": "-1h"}, "not_renderable", "since"),
    # Of two faults of one argument the first of the order is the error: the type before
    # the bounds, the allowed values before the form.
    (TAIL, {"node": "web", "unit": "nginx.service", "lines": 1000.0}, "wrong_type", "lines"),
    (
        LOGS,
        {"node": "web", "service": "nginx.service", "since": "1h", "level": "x" * 2000},
        "not_allowed",
        "level",
    ),
    # Below a root of within, never the root itself.
    (REMOVE, {"node": "web", "path": "/tmp"}, "path_not_allowed", "path"),  # noqa: S108
    (REMOVE, {"node": "web", "path": "/tmp/"}, "path_not_allowed", "path"),  # noqa: S108
    (REMOVE, {"node": "web", "path": "/tmp/a/.."}, "path_not_allowed", "path"),  # noqa: S108
    (REMOVE, {"node": "web", "path": "/var/cachex/a"}, "path_not_allowed", "path"),
    (REMOVE, {"node": "web", "path": "/var/tmp/../../etc/passwd"}, "path_not_allowed", "path"),  # noqa: S108
    (REMOVE, {"node": "web", "path": "/etc/nginx/nginx.conf"}, "path_not_allowed", "path"),
]


@pytest.mark.parametrize(("used", "args", "code", "argument"), REFUSALS)
def test_a_call_that_does_not_fit_is_refused_with_its_code_and_argument(
    used: Any, args: object, code: str, argument: str | None
) -> None:
    error = refusal(used, args)
    assert (error.code, error.argument) == (code, argument)
    assert str(error).startswith(f"{argument}: " if argument else "the ")
    # The same call is refused the same way every time.
    again = refusal(used, args)
    assert (again.code, again.argument, str(again)) == (code, argument, str(error))


def test_an_error_names_the_argument_and_the_rule_and_no_value() -> None:
    hidden = ["nginx.service", "webapp.service", "postgresql.service", "/etc/nginx", "/srv/webapp"]
    hidden += ["/srv/conf", "sites-available", "8443", "site", "admin", "raw", "db"]
    for used, args, _, _ in REFUSALS:
        if not isinstance(args, dict):
            continue
        given = [value for value in args.values() if isinstance(value, str) and len(value) > 3]
        said = str(refusal(used, {**args, "node": args.get("node", "web")}))
        for word in (*hidden, *given):
            assert word not in said.split(": ", 1)[-1], (said, word)
    # A refused root and a path outside the allowed ones are one refusal, in one wording.
    wide = WIDE
    outside = refusal(READ, {"node": "web", "path": "/etc/shadow"}, wide)
    for path in ("/proc", "/proc/1/environ", "/dev/shm/a", "/sys/fs/cgroup/x", "/dev/../proc/1"):  # noqa: S108
        error = refusal(READ, {"node": "web", "path": path}, wide)
        assert (error.code, str(error)) == ("path_not_allowed", str(outside))
        # Without the context's word for it the refusal is the same one.
        assert str(refusal(READ, {"node": "web", "path": path})) == str(outside)
    assert check(READ, {"node": "web", "path": "/etc/nginx/a"}, wide).node == "web"


def test_the_schema_a_model_is_given_says_the_values_and_nothing_of_a_run() -> None:
    assert argument_schema(TAIL) == {
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
    }
    described = changed(remove_file(), "args.path.description", "The file to remove.")
    assert argument_schema(tool(described))["properties"]["path"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "description": "The file to remove.",
    }
    assert argument_schema(LOGS)["properties"]["level"]["enum"] == ["error", "warning", "info"]
    assert argument_schema(NODES) == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    for used in (RESTART, TAIL, REMOVE, PROBE, READ, PATCH, LOGS, NODES):
        schema = argument_schema(used)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert "context" not in json.dumps(schema) and "/tmp" not in json.dumps(schema)  # noqa: S108
    jsonschema.Draft202012Validator.check_schema(dict(META_SCHEMA))


@pytest.mark.parametrize(
    "meta",
    [
        META,
        {**META, "action_confidence": 0},
        {**META, "action_confidence": 1},
        {**META, "rationale": "two\nlines\tand caf\u00e9", "expected_effect": "x" * 2000},
    ],
)
def test_a_mutating_call_says_its_confidence_its_reason_and_its_expected_effect(
    meta: dict[str, Any],
) -> None:
    said = check_meta(RESTART, meta)
    assert said == Meta(
        float(meta["action_confidence"]), meta["rationale"], meta["expected_effect"]
    )
    assert type(said.action_confidence) is float if said else False
    jsonschema.Draft202012Validator(dict(META_SCHEMA)).validate(meta)


@pytest.mark.parametrize(
    "meta",
    [
        None,
        [],
        "sure",
        {},
        {"action_confidence": 0.7, "rationale": "x"},
        {**META, "note": "x"},
        {**META, "action_confidence": True},
        {**META, "action_confidence": "0.7"},
        {**META, "action_confidence": 1.01},
        {**META, "action_confidence": -0.1},
        {**META, "action_confidence": float("nan")},
        {**META, "action_confidence": float("inf")},
        {**META, "action_confidence": 10**400},
        {**META, "action_confidence": -(10**400)},
        {**META, "action_confidence": 2},
        {**META, "action_confidence": None},
        {**META, "rationale": ""},
        {**META, "rationale": 7},
        {**META, "rationale": "x" * 2001},
        {**META, "expected_effect": "a\x1bb"},
        {**META, "expected_effect": "a\u202eb"},
        {**META, "expected_effect": None},
    ],
)
def test_a_mutating_call_without_a_whole_statement_of_itself_is_refused(meta: object) -> None:
    with pytest.raises(ArgumentError) as raised:
        check_meta(RESTART, meta)
    assert (raised.value.code, raised.value.argument) == ("bad_meta", "_meta")
    assert str(raised.value).startswith("_meta: an object with action_confidence")
    # A read-only call's statement is not looked at; a forbidden tool is forbidden.
    assert check_meta(TAIL, meta) is None
    with pytest.raises(ArgumentError) as raised:
        check_meta(REBOOT, meta)
    assert raised.value.code == "forbidden"


JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=True)
    | st.text(st.characters(exclude_categories=()), max_size=40),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
TOOLS = st.sampled_from([RESTART, TAIL, REMOVE, PROBE, READ, PATCH, LOGS, NODES, REBOOT])
NAMES = st.sampled_from(
    ["node", "unit", "lines", "path", "endpoint", "via", "diff", "service", "since", "level"]
)
LIKELY = st.sampled_from(
    [
        "web",
        "db",
        "nginx.service",
        "site",
        "/etc/nginx/a",
        "/srv/conf/app.ini",
        "/var/cache/a",
        5,
        100,
        "1h",
        "info",
        "x",
    ]
)


@given(used=TOOLS, args=JSON | st.dictionaries(NAMES, JSON | LIKELY, max_size=5))
def test_whatever_is_given_a_call_is_checked_or_refused_and_nothing_else(
    used: Any, args: object
) -> None:
    try:
        call = check(used, args, CONTEXT)
    except ArgumentError as error:
        with pytest.raises(ArgumentError) as raised:
            check(used, args, CONTEXT)
        assert (raised.value.code, raised.value.argument) == (error.code, error.argument)
        assert error.code in get_args(ErrorCode)
        return
    # What passed is what the schema takes, of exact types, from the context.
    assert isinstance(args, dict)
    jsonschema.Draft202012Validator(argument_schema(used)).validate(args)
    assert set(call.args) == set(used.args) and call.node in (None, "web", "db")
    node = CONTEXT.node(call.node) if call.node is not None else None
    for name, value in call.args.items():
        said = used.args[name]
        assert type(value) is (int if said.type == "integer" else str)
        if said.type != "text":
            assert isinstance(value, int) or values.printable(value)
        # A value with a source is one the run has: for the node, or among the nodes.
        if said.source == "context.nodes":
            assert CONTEXT.node(str(value)) is not None
        elif said.source == "context.node.services":
            assert node is not None and value in node.services
        elif said.source == "context.node.http_endpoints":
            assert node is not None and node.url(str(value)) == call.values[name]
    assert all(isinstance(value, str) and value for value in call.values.values())


PARTS = st.lists(
    st.sampled_from(
        ["", ".", "..", "etc", "nginx", "tmp", "var", "cache", "a", "proc", "dev", "x" * 256]
    ),
    max_size=7,
)


@given(parts=PARTS, final=st.booleans(), used=st.sampled_from([READ, REMOVE]))
def test_a_path_that_passes_is_normal_allowed_and_outside_the_refused_roots(
    parts: list[str], final: bool, used: Any
) -> None:
    path = "/" + "/".join(parts) + ("/" if final else "")
    # A context that allows the refused roots too: they stay refused whatever it says.
    try:
        normal = str(check(used, {"node": "web", "path": path}, WIDE).args["path"])
    except ArgumentError as error:
        assert (error.code, error.argument) in (("bad_form", "path"), ("path_not_allowed", "path"))
        return
    assert normal.startswith("/") and "//" not in normal and not normal.endswith("/")
    assert not {".", ".."} & set(normal.split("/")) and max(map(len, normal.split("/"))) <= 255
    assert not values.refused(normal) and normal.split("/")[1] not in ("dev", "proc", "sys")
    node = WIDE.nodes[0]
    if used is READ:
        assert values.within(normal, node.readable)
    else:
        assert values.below(normal, ("/tmp", "/var/tmp", "/var/cache"))  # noqa: S108


@pytest.mark.parametrize(
    ("place", "value"),
    [
        ("nodes.0.alias", "Web"),
        ("nodes.0.alias", "a-very-long-alias"),
        ("nodes.0.alias", "localhost"),
        ("nodes.1.alias", "web"),
        ("nodes.0.services", ["nginx"]),
        ("nodes.0.services", ["nginx.service", "nginx.service"]),
        ("nodes.0.readable_paths", ["etc/nginx"]),
        ("nodes.0.readable_paths", ["/etc/nginx/"]),
        ("nodes.0.readable_paths", ["/etc/../etc"]),
        ("nodes.0.readable_paths", ["/"]),
        ("nodes.0.readable_paths", ["/etc/nginx", "/etc/nginx"]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": []}]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": ["nginx", "-t\n"]}]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": ["sh", "-c", "nginx -t"]}]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": ["/bin/bash", "check"]}]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": ["env", "dash", "x"]}]),
        ("nodes.0.patchable_paths", [{"path": "/a"}]),
        ("nodes.0.patchable_paths", [{"path": "/a", "validator": None}] * 2),
        ("nodes.0.endpoints", [{"name": "raw", "scheme": "tcp", "port": 9000, "path": "/"}]),
        ("nodes.0.endpoints", [{"name": "site", "scheme": "http", "port": 80, "path": None}]),
        ("nodes.0.endpoints", [{"name": "site", "scheme": "http", "port": 0, "path": "/"}]),
        ("nodes.0.endpoints", [{"name": "site", "scheme": "ftp", "port": 21, "path": "/"}]),
        ("nodes.0.endpoints", [{"name": "site", "scheme": "http", "port": 80, "path": "/a b"}]),
        ("nodes.0.endpoints", [{"name": "Site", "scheme": "http", "port": 80, "path": "/"}]),
        ("nodes.0.address", "198.51.100.7"),
        ("nodes.0.instance", "r-0a1b2c3d4e5f-web"),
    ],
)
def test_a_context_has_the_form_of_a_system_s_description_and_holds_no_instance(
    place: str, value: Any
) -> None:
    data = json.loads(CONTEXT.model_dump_json())
    *above, last = place.split(".")
    at: Any = data
    for key in above:
        at = at[int(key)] if key.isdigit() else at[key]
    at[last] = value
    with pytest.raises(ValueError, match="validation error") as raised:
        Context.model_validate_json(json.dumps(data), strict=True)
    # The error shows the place and never what stood there.
    shown = str(value) if isinstance(value, str) else json.dumps(value)[1:20]
    assert shown not in str(raised.value) or len(shown) < 4
    assert "input_value" not in str(raised.value)


def test_a_context_answers_by_alias_and_gives_a_probe_an_address_by_alias() -> None:
    web = CONTEXT.node("web")
    assert web is not None and CONTEXT.node("cache") is None
    assert web.readable == (
        "/etc/nginx",
        "/srv/webapp",
        "/var/log/nginx",
        "/etc/nginx/sites-available/default",
        "/srv/conf",
    )
    assert web.patchable == ("/etc/nginx/sites-available/default", "/srv/conf")
    assert web.patchable_paths[0].validator == ("nginx", "-t")
    assert (web.url("site"), web.url("admin")) == ("http://web:80/", "https://web:8443/health")
    assert web.url("raw") is None and web.url("none") is None
    assert Context.model_validate_json('{"nodes": []}', strict=True).nodes == ()
