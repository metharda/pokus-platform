"""Logs are JSON lines, with context, and never show a restricted value or a secret."""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import enum
import functools
import io
import json
import logging
import sys
import threading
import types
import warnings
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from potka_core.logging import JSONFormatter, log_context, setup_logging
from potka_core.restricted import Restricted
from pydantic import SecretStr

TRUTH = "scenario-FIXTURE-disk-full"
HIDDEN_VALUE = "hunter2-not-a-real-one"
RUN = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
CAMPAIGN = "01BX5ZZKBKACTAV9WEVGEMMVRZ"


@pytest.fixture
def capture(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """setup_logging("verify") in the test's body; the root logger and the hooks are restored.

    The handler takes standard output when it is made, so it is made in the
    test's body, where capsys captures it.
    """
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", list(root.handlers))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "unraisablehook", sys.unraisablehook)
    monkeypatch.setattr(warnings, "showwarning", warnings.showwarning)
    level = root.level

    def records() -> list[dict[str, Any]]:
        out, err = capsys.readouterr()
        assert err == ""
        return [json.loads(line) for line in out.split("\n") if line]

    try:
        yield records
    finally:
        root.setLevel(level)


def revealed_failure() -> None:
    """Fail as a service does on a value it revealed: the value is in the messages."""
    scenarios: dict[str, str] = {}
    try:
        scenarios[Restricted(TRUTH).reveal()]
    except KeyError as exc:
        raise RuntimeError(f"no scenario {exc}") from exc


def test_every_line_is_a_json_object_with_its_context(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    with log_context(run_id=RUN, campaign_id=CAMPAIGN):
        log.info("checked %s with %r", Restricted(TRUTH), SecretStr(HIDDEN_VALUE))
        log.warning("about %(what)s", {"what": Restricted(TRUTH)})
        log.info(Restricted(TRUTH))
        log.info(
            "extra",
            extra={"injection": Restricted(TRUTH), "token": SecretStr(HIDDEN_VALUE), "n": 3},
        )
    log.info("outside")
    records = capture()
    assert [r["message"] for r in records] == [
        "checked <restricted> with 'pydantic.types.SecretStr'",
        "about <restricted>",
        "<restricted>",
        "extra",
        "outside",
    ]
    for record in records[:4]:
        assert record["service"] == "verify"
        assert record["logger"] == "potka.test"
        assert record["run_id"] == RUN
        assert record["campaign_id"] == CAMPAIGN
        assert record["time"].endswith("Z")
        assert "trace_id" not in record
    assert "run_id" not in records[4]
    assert records[3]["fields"] == {
        "injection": "<restricted>",
        "token": "pydantic.types.SecretStr",
        "n": 3,
    }
    for record in records:
        assert TRUTH not in json.dumps(record)
        assert HIDDEN_VALUE not in json.dumps(record)


def test_an_exception_shows_its_types_and_frames_never_its_message(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    try:
        revealed_failure()
    except RuntimeError as exc:
        log.exception("boom")
        log.error("failed: %s", exc)
        log.error(exc)
        log.error("with", extra={"error": exc, "cause": exc.__cause__})
    records = capture()
    text = "\n".join(json.dumps(r) for r in records)
    assert TRUTH not in text
    assert records[0]["level"] == "error"
    exception = records[0]["exception"].split("\n")
    assert exception[0] == "Traceback (most recent call last):"
    assert exception[-1] == "RuntimeError (message not written)"
    assert "KeyError (message not written)" in exception
    assert "The above exception was the direct cause of the following exception:" in exception
    assert any(line.endswith(", in revealed_failure") for line in exception)
    assert [r["message"] for r in records[1:3]] == ["failed: RuntimeError", "RuntimeError"]
    assert records[3]["fields"] == {"error": "RuntimeError", "cause": "KeyError"}


def test_an_os_error_and_a_group_show_no_message(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    try:
        Path(f"/nonexistent/{TRUTH}").read_text()
    except OSError as exc:
        log.error("read: %s", exc)
    try:
        raise ExceptionGroup(TRUTH, [ValueError(TRUTH), TypeError(TRUTH)])
    except ExceptionGroup:
        log.exception("group")
    records = capture()
    assert TRUTH not in json.dumps(records)
    assert records[0]["message"] == "read: FileNotFoundError"
    exception = records[1]["exception"].split("\n")
    assert "ExceptionGroup (message not written)" in exception
    assert "  ValueError (message not written)" in exception
    assert "  TypeError (message not written)" in exception


def test_allowed_values_keep_their_format_and_odd_values_still_log(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    cycle: dict[str, Any] = {}
    cycle["self"] = cycle
    log.info("score %.3f of %r and %s", 0.5, Path("/x"), Decimal("0.5"))
    log.info("calls", extra={"by_pair": {("orch", "gw"): 3}, "ratio": float("nan"), "loop": cycle})
    log.info("%s", cycle)
    log.info("text %s", "caf\u00e9 \u2028 end")
    log.info("pair %r", ("x", {"k": 1}, [Restricted(TRUTH)]))
    records = capture()
    assert records[0]["message"] == "score 0.500 of 'pathlib.PosixPath' and decimal.Decimal"
    assert records[1]["fields"] == {
        "by_pair": {"('orch', 'gw')": 3},
        "ratio": "nan",
        "loop": {"self": "<cycle>"},
    }
    assert records[2]["message"] == "{'self': '<cycle>'}"
    assert records[3]["message"] == "text caf\u00e9 \u2028 end"
    assert records[4]["message"] == "pair ('x', {'k': 1}, ['<restricted>'])"
    assert len(records) == 5


def test_lines_are_ascii() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter("exec"))
    record = logging.LogRecord("potka", logging.INFO, "", 0, "caf\u00e9 \u2028 \x85", None, None)
    handler.emit(record)
    line = stream.getvalue()
    assert line.isascii()
    assert line.count("\n") == 1
    assert json.loads(line)["message"] == "caf\u00e9 \u2028 \x85"


def test_the_trace_of_the_current_span_is_logged() -> None:
    formatter = JSONFormatter("exec")
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("step") as span:
        record = logging.LogRecord("potka", logging.INFO, "", 0, "in a span", None, None)
        line = json.loads(formatter.format(record))
    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_the_context_takes_only_strings_and_never_a_line_s_own_field() -> None:
    with pytest.raises(TypeError), log_context(run_id=Restricted(TRUTH)):  # type: ignore[arg-type]
        pass
    for name in (
        "service",
        "level",
        "message",
        "time",
        "logger",
        "trace_id",
        "fields",
        "exception",
    ):
        with pytest.raises(TypeError, match=f"^log context field {name} is a field of every line$"):
            with log_context(**{name: "x"}):
                pass


def test_setup_replaces_the_root_handlers(capture: Any) -> None:
    stream = io.StringIO()
    old = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.handlers.insert(0, old)
    setup_logging("exec")
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)
    assert old not in root.handlers


def test_an_uncaught_exception_is_logged_without_its_message(capture: Any) -> None:
    setup_logging("verify")
    try:
        revealed_failure()
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)
    for target in (revealed_failure, sys.exit):  # a thread's SystemExit is not an error
        thread = threading.Thread(target=target)
        thread.start()
        thread.join()
    records = capture()
    assert len(records) == 2
    for record in records:
        assert record["message"] == "uncaught exception"
        assert record["level"] == "critical"
        assert record["exception"].endswith("RuntimeError (message not written)")
    assert TRUTH not in json.dumps(records)


def test_a_record_that_cannot_be_formatted_shows_nothing_it_was_given(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    shown = KeyError(f"argument {TRUTH}")
    with log_context(run_id=RUN):
        try:
            revealed_failure()
        except RuntimeError:
            # An argument short, one of them an exception holding the value.
            log.exception("no scenario %s for run %s in campaign %s", shown, Restricted(TRUTH))
    (record,) = capture()  # standard error stays empty
    assert set(record) == {"time", "level", "service", "logger", "run_id", "message", "exception"}
    assert (record["level"], record["service"], record["run_id"]) == ("error", "verify", RUN)
    assert record["message"] == "a log record could not be formatted"
    assert record["logger"] == "potka.test"
    assert "TypeError (message not written)" in record["exception"]
    assert TRUTH not in json.dumps(record)


def test_containers_at_any_depth(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    deep: list[Any] = []
    inner = deep
    for _ in range(100):
        inner.append([])
        inner = inner[0]
    error = KeyError(TRUTH)
    log.info("%s", deep)
    log.info("%s", {("a", 1): 1, (Restricted(TRUTH), 2): 2, "set": {("b", 2)}})
    log.error("%s", [error], extra={"errors": [error], "nested": {"e": (error,)}})
    records = capture()
    assert records[0]["message"].count("[") == 32
    assert "<too deep>" in records[0]["message"]
    assert records[1]["message"] == "{('a', 1): 1, ('<restricted>', 2): 2, 'set': {('b', 2)}}"
    assert records[2]["message"] == "['KeyError']"
    assert records[2]["fields"] == {"errors": ["KeyError"], "nested": {"e": ["KeyError"]}}
    assert TRUTH not in json.dumps(records)


def test_a_task_is_written_as_its_type_name(capture: Any) -> None:
    setup_logging("verify")

    async def fail() -> None:
        raise KeyError(TRUTH)

    async def run() -> asyncio.Task[None]:
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return task

    task = asyncio.run(run())
    assert isinstance(task.exception(), KeyError)
    logging.getLogger("potka.test").error("step failed: %s", task, extra={"task": task})
    (record,) = capture()
    assert record["message"] == "step failed: _asyncio.Task"
    assert record["fields"] == {"task": "_asyncio.Task"}


def test_chains_ignored_exceptions_and_warnings_never_show_a_message(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    error: BaseException | None = None
    for number in range(1200):
        try:
            raise ValueError(f"{TRUTH} {number}") from error
        except ValueError as exc:
            error = exc
    assert error is not None
    log.error("long", exc_info=error)
    known: dict[str, str] = {}
    try:
        try:
            known[TRUTH]
        except KeyError:
            raise ValueError(TRUTH)  # noqa: B904 (an implicit context, on purpose)
    except ValueError as exc:
        log.error("context", exc_info=exc)
        suppressed = ValueError(TRUTH)
        suppressed.__context__, suppressed.__suppress_context__ = exc, True
        log.error("suppressed", exc_info=suppressed)
        log.error("group", exc_info=ExceptionGroup(TRUTH, [exc, exc]))

    class Finalized:
        def __del__(self) -> None:
            raise KeyError(TRUTH)

    Finalized()  # CPython finalizes it at once
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(TRUTH, UserWarning, stacklevel=1)
    records = capture()
    assert TRUTH not in json.dumps(records)
    long, context, suppressed_line, group, ignored, warned = records
    assert long["exception"].startswith(
        "ValueError (and the exceptions before it or within it, not written)"
    )
    assert "During handling of the above exception" in context["exception"]
    assert "During handling" not in suppressed_line["exception"]
    assert "ValueError (shown above)" in group["exception"]
    assert ignored["message"] == "an exception was ignored"
    assert ignored["exception"].endswith("KeyError (message not written)")
    assert warned["message"].startswith("a UserWarning at ")
    assert warned["message"].endswith(" (its text not written)")


# Every container of builtins and collections, nested, around exceptions whose
# messages hold MARK: only the allowlisted types show their content, so MARK
# never reaches a line.
MARK = "marker-SECRET-7f3a"
Pair = collections.namedtuple("Pair", "first second")


@dataclasses.dataclass
class Box:
    inner: object


class Shows:
    """An object whose own repr shows what it holds."""

    def __init__(self, inner: object) -> None:
        self.inner = inner

    def __repr__(self) -> str:
        return f"Shows({self.inner!r})"

    __str__ = __repr__


class Level(enum.IntEnum):
    HIGH = 1


class Text(str):
    """A str subclass: not an allowed type."""


WRAPPERS: dict[str, Callable[[Any], Any]] = {
    "list": lambda x: [x],
    "tuple": lambda x: (x,),
    "dict value": lambda x: {"k": x},
    "dict key": lambda x: {x: 1},
    "set": lambda x: {x},
    "frozenset": lambda x: frozenset({x}),
    "dict keys": lambda x: {x: 1}.keys(),
    "dict values": lambda x: {"k": x}.values(),
    "dict items": lambda x: {"k": x}.items(),
    "deque": lambda x: collections.deque([x]),
    "defaultdict": lambda x: collections.defaultdict(list, {"k": x}),
    "OrderedDict": lambda x: collections.OrderedDict(k=x),
    "Counter": lambda x: collections.Counter({x: 1}),
    "ChainMap": lambda x: collections.ChainMap({"k": x}),
    "namedtuple": lambda x: Pair(x, 1),
    "UserDict": lambda x: collections.UserDict(k=x),
    "UserList": lambda x: collections.UserList([x]),
    "UserString": lambda x: collections.UserString(repr(x)),
    "MappingProxyType": lambda x: types.MappingProxyType({"k": x}),
    "dataclass": Box,
    "custom repr": Shows,
    "str subclass": lambda x: Text(repr(x)),
    "slice": lambda x: slice(x),
    "partial": lambda x: functools.partial(print, x),
}


def nested_values() -> Iterator[object]:
    """Each exception alone, in each wrapper, and in each wrapper of each wrapper."""
    cause = KeyError(MARK)
    chained = RuntimeError(f"failed on {MARK}")
    chained.__cause__ = cause
    for leaf in (cause, chained, ValueError(MARK)):
        yield leaf
        for outer in WRAPPERS.values():
            try:
                once = outer(leaf)
            except TypeError:  # an unhashable value as a key or in a set
                continue
            yield once
            for second in WRAPPERS.values():
                try:
                    yield second(once)
                except TypeError:
                    continue


def test_no_object_outside_the_allowlist_shows_its_content(capture: Any) -> None:
    setup_logging("verify")
    log = logging.getLogger("potka.test")
    count = 0
    for value in nested_values():
        log.info("%s", value)
        log.info("%r", value)
        log.info(value)
        log.info("extra", extra={"value": value})
        count += 1
    log.info("%s %s %r", Level.HIGH, Text("x"), Shows("x"))
    log.info(Text(MARK))
    records = capture()
    assert count > 1000
    assert len(records) == 4 * count + 2
    leaked = [json.dumps(record) for record in records if MARK in json.dumps(record)]
    assert leaked[:3] == []  # (a short list, so a failure shows only a few lines)
    assert records[-2]["message"] == (
        f"{type_of(Level.HIGH)} {type_of(Text('x'))} '{type_of(Shows('x'))}'"
    )
    assert records[-1]["message"] == type_of(Text("x"))


def type_of(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def test_the_allowed_types_show_their_content(capture: Any) -> None:
    setup_logging("verify")
    allowed = [None, True, 3, 2.5, "a", [1], (2,), {"k": 1}, {3}, frozenset({4})]
    logging.getLogger("potka.test").info("%r", allowed, extra={"allowed": allowed})
    (record,) = capture()
    assert record["message"] == repr(allowed)
    assert record["fields"] == {"allowed": [None, True, 3, 2.5, "a", [1], [2], {"k": 1}, [3], [4]]}
