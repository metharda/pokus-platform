"""The command of a checked call: a tuple with one element for each element of its template."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st
from potka_testing.tool_commands import shape
from potka_testing.tool_context import context
from potka_testing.tools import (
    LIMITS,
    changed,
    handler,
    http_probe,
    restart_service,
    tail_journal,
    tool,
)
from potka_tools.arguments import ArgumentError, check
from potka_tools.render import TIMEOUT, Command, render

CONTEXT = context()
WRAP = (TIMEOUT, "--kill-after=5s", "30s")
# A tool with every form of element: an option, a marked literal, values alone and a free one.
FREE = {"type": "string", "pattern": "^.*$"}
SINCE = {"type": "string", "pattern": "^-?[0-9]+[smh]$", "default": "-1h"}
ENTRY: dict[str, Any] = {
    **tail_journal(),
    "name": "journal_grep",
    "args": {
        **tail_journal()["args"],
        "since": SINCE,
        "word": FREE,
    },
    "exec": {
        "argv": [
            "journalctl",
            "--no-pager",
            "--unit={unit}",
            "--lines={lines}",
            "--since={since}",
            {"literal": "--output-fields={MESSAGE}"},
            "--grep",
            "{word}",
        ],
        **LIMITS,
    },
    "post_check": {"argv": ["systemctl", "is-active", "{unit}"]},
}
GREP = tool(ENTRY)


def test_a_command_is_its_template_with_each_value_in_its_place_under_timeout() -> None:
    args = {"node": "web", "unit": "nginx.service", "lines": 7, "word": "error"}
    assert render(check(GREP, args, CONTEXT)) == Command(
        on="node",
        argv=(
            *WRAP,
            "journalctl",
            "--no-pager",
            "--unit=nginx.service",
            "--lines=7",
            "--since=-1h",
            "--output-fields={MESSAGE}",
            "--grep",
            "error",
        ),
        post_check=(*WRAP, "systemctl", "is-active", "nginx.service"),
        timeout_s=30,
        kill_after_s=5,
        max_output_bytes=65536,
    )
    # The tool's own limits are in the wrapper and beside the command.
    slow = changed(changed(restart_service(), "exec.timeout_s", 120), "exec.kill_after_s", 10)
    made = render(check(tool(slow), {"node": "web", "unit": "nginx.service"}, CONTEXT))
    assert made is not None and made.post_check is not None
    assert made.argv == (
        TIMEOUT,
        "--kill-after=10s",
        "120s",
        "systemctl",
        "restart",
        "nginx.service",
    )
    assert made.post_check[:3] == (TIMEOUT, "--kill-after=10s", "120s")
    assert (made.timeout_s, made.kill_after_s) == (120, 10)


def test_a_probe_runs_on_the_probe_client_against_the_alias_and_a_handler_has_no_command() -> None:
    made = render(
        check(tool(http_probe()), {"node": "web", "endpoint": "admin", "via": "db"}, CONTEXT)
    )
    assert made == Command(
        "probe_client", (*WRAP, "curl", "--silent", "https://web:8443/health"), None, 30, 5, 65536
    )
    read = check(tool(handler("read_file")), {"node": "web", "path": "/etc/nginx/a"}, CONTEXT)
    assert render(read) is None
    assert render(check(tool(handler("list_nodes")), {}, CONTEXT)) is None


def test_a_value_is_one_element_whatever_it_holds() -> None:
    for word in (
        "a b",
        "a; rm -rf / #",
        "$(reboot) `id` | tee > /etc/passwd && x",
        "'quoted' \"twice\" \\",
        "{unit} {{literal}} %s",
        "*.? ~ [a-z]",
    ):
        args = {"node": "web", "unit": "nginx.service", "word": word}
        made = render(check(GREP, args, CONTEXT))
        assert made is not None
        assert len(made.argv) == 3 + 8 and made.argv[-1] == word and made.argv[-2] == "--grep"
    # A value that would read as an option never becomes an element of its own...
    try:
        check(GREP, {"node": "web", "unit": "nginx.service", "word": "--file=/etc/shadow"}, CONTEXT)
    except ArgumentError as error:
        assert (error.code, error.argument) == ("not_renderable", "word")
    else:
        raise AssertionError("a value that begins with a dash stood alone")
    # ...and inside an option it is the option's value and nothing else.
    made = render(
        check(GREP, {"node": "web", "unit": "nginx.service", "word": "x", "since": "-5m"}, CONTEXT)
    )
    assert made is not None and "--since=-5m" in made.argv


UNITS = st.sampled_from(["nginx.service", "webapp.service"])
WORDS = st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1, max_size=30)
SINCES = st.from_regex(r"-?[0-9]{1,4}[smh]", fullmatch=True)


@given(unit=UNITS, lines=st.integers(1, 500), since=SINCES, word=WORDS)
def test_every_command_of_a_checked_call_has_its_template_s_shape(
    unit: str, lines: int, since: str, word: str
) -> None:
    args = {"node": "web", "unit": unit, "lines": lines, "since": since, "word": word}
    try:
        call = check(GREP, args, CONTEXT)
    except ArgumentError as error:
        assert error.code == "not_renderable" and word.startswith("-")
        return
    made = render(call)
    assert made is not None and made.post_check is not None
    for template_, argv in ((GREP.command, made.argv), (GREP.post_check, made.post_check)):
        assert template_ is not None
        shape(GREP, template_.argv, argv, call.values)
