"""Structured logs: one JSON object per line on standard output (journald).

Every line carries the time (UTC), the level, the service, the logger and
the message, and the run, the campaign and the trace when they are known.

The message's arguments, a message that is not a string and the extra
fields are written through an allowlist of types: None, and values of
exactly bool, int, float or str, with their content; lists, tuples,
dictionaries, sets and frozensets, with their content walked the same way;
a restricted value (`potka_core.restricted`) as ``<restricted>``. Every
other object, exceptions, secrets, tasks and subclasses of the allowed
types included (an enum, a named tuple, an ``OrderedDict``), is written as
its type's name only, never its ``str`` or ``repr``. A type whose content
must be logged is added to the allowlist with a test.

An exception given as ``exc_info`` is written as its type and its frames,
never its message, along its whole chain: an exception's message can hold
a value revealed upstream. The same holds for an uncaught exception, an
exception Python ignores (in a finalizer), a warning (its category and
place, not its text) and a record that cannot be formatted. A record's
``stack_info`` is not written. The lines are ASCII, so no character in a
value can split a line.

What the formatter cannot see is the caller's: a message it formats itself
(an f-string, ``str(exc)``). ruff's logging rules refuse a formatted string
as the message of the loggers ruff recognises (the ``logging`` module and
objects named like ``log`` or ``logger``).
"""

from __future__ import annotations

import errno
import json
import logging
import math
import sys
import threading
import traceback
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import MappingProxyType, TracebackType
from typing import Any, Final

from opentelemetry import trace

from potka_core.restricted import MARKER, Restricted

CONTEXT: ContextVar[Mapping[str, str]] = ContextVar(
    "potka_log_context", default=MappingProxyType({})
)
CYCLE: Final = "<cycle>"
TOO_DEEP: Final = "<too deep>"
MAX_DEPTH: Final = 32
MAX_CHAIN: Final = 64
# The fields of every line; a context field cannot replace them.
RESERVED: Final = frozenset(
    {"time", "level", "service", "logger", "message", "trace_id", "fields", "exception"}
)
# The attributes every LogRecord has; anything else on a record is an extra field.
STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


def type_name(value: object) -> str:
    """The name of VALUE's type, never anything of the value itself.

    Args:
        value: Any object.

    Returns:
        For example ``KeyError``, ``collections.deque`` or ``_asyncio.Task``.
    """
    kind = type(value)
    name = kind.__qualname__
    return name if kind.__module__ == "builtins" else f"{kind.__module__}.{name}"


def describe_exception(exc: BaseException) -> str:
    """An exception's type (and an OS error's errno name), never its message.

    Args:
        exc: The exception.

    Returns:
        For example ``KeyError`` or ``PermissionError [EACCES]``.
    """
    name = type_name(exc)
    code = exc.errno if isinstance(exc, OSError) else None
    if code is not None and code in errno.errorcode:
        name += f" [{errno.errorcode[code]}]"
    return name


def _exception_lines(exc: BaseException, seen: set[int], depth: int = 0) -> list[str]:
    if id(exc) in seen:
        return [f"{describe_exception(exc)} (shown above)"]
    if depth >= MAX_CHAIN:
        return [
            f"{describe_exception(exc)} (and the exceptions before it or within it, not written)"
        ]
    seen.add(id(exc))
    lines: list[str] = []
    if exc.__cause__ is not None:
        lines += _exception_lines(exc.__cause__, seen, depth + 1)
        lines.append("The above exception was the direct cause of the following exception:")
    elif exc.__context__ is not None and not exc.__suppress_context__:
        lines += _exception_lines(exc.__context__, seen, depth + 1)
        lines.append("During handling of the above exception, another exception occurred:")
    lines.append("Traceback (most recent call last):")
    for frame, line in traceback.walk_tb(exc.__traceback__):
        lines.append(f'  File "{frame.f_code.co_filename}", line {line}, in {frame.f_code.co_name}')
    lines.append(f"{describe_exception(exc)} (message not written)")
    if isinstance(exc, BaseExceptionGroup):
        for number, inner in enumerate(exc.exceptions, 1):
            lines.append(f"+ exception {number} of the group:")
            lines += [f"  {line}" for line in _exception_lines(inner, seen, depth + 1)]
    return lines


CONTAINERS: Final = (list, tuple, dict, set, frozenset)


def _walk(value: Any, as_json: bool, path: set[int], depth: int) -> Any:
    """VALUE through the allowlist (the module's docstring), at any depth.

    With AS_JSON the result is a JSON value (tuples and sets become lists,
    dictionary keys strings, NaN and the infinities strings); otherwise the
    allowed containers keep their kind, so a format string formats them.
    """
    if isinstance(value, Restricted):
        return MARKER
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        return value if not as_json or math.isfinite(value) else repr(value)
    if kind not in CONTAINERS:
        return type_name(value)
    if id(value) in path:
        return CYCLE
    if depth >= MAX_DEPTH:
        return TOO_DEEP
    path.add(id(value))
    try:
        if kind is dict:
            walked = {}
            for k, v in value.items():
                key = _walk(k, False, path, depth + 1)
                if as_json or not _hashable(key):
                    key = str(key)
                walked[key] = _walk(v, as_json, path, depth + 1)
            return walked
        items = [_walk(item, as_json, path, depth + 1) for item in value]
        if as_json or kind is list:
            return items
        if kind is tuple:
            return tuple(items)
        if not all(_hashable(item) for item in items):
            return items
        return frozenset(items) if kind is frozenset else set(items)
    finally:
        path.discard(id(value))


def _hashable(value: Any) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


def safe(value: Any) -> Any:
    """VALUE as a log argument, through the allowlist.

    Args:
        value: Any value.

    Returns:
        An allowed scalar as it is, an allowed container as a copy of its
        kind, anything else as its type's name.
    """
    return _walk(value, False, set(), 0)


def safe_json(value: Any) -> Any:
    """VALUE as a JSON value of a log line: replaced as by :func:`safe`.

    Args:
        value: A value.

    Returns:
        A JSON value (NaN and the infinities as strings).
    """
    return _walk(value, True, set(), 0)


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    """Add FIELDS (``run_id``, ``campaign_id``) to every log line written inside.

    Args:
        **fields: The context fields; a restricted value is refused.

    Yields:
        Nothing.

    Raises:
        TypeError: A field's value is not a string, or its name is one of the
            line's own fields.
    """
    for key, value in fields.items():
        if key in RESERVED:
            raise TypeError(f"log context field {key} is a field of every line")
        if not isinstance(value, str):
            raise TypeError(f"log context field {key} must be a string")
    token = CONTEXT.set({**CONTEXT.get(), **fields})
    try:
        yield
    finally:
        CONTEXT.reset(token)


class JSONFormatter(logging.Formatter):
    """Formats a record as one JSON object, redacted."""

    def __init__(self, service: str) -> None:
        """Format for SERVICE.

        Args:
            service: The service's name in the manifest.
        """
        super().__init__()
        self.service = service

    def formatException(self, ei: Any) -> str:
        """The exception's types and frames along its chain, never a message.

        Args:
            ei: The record's ``exc_info``.

        Returns:
            The text, or an empty string when there is no exception.
        """
        exc = ei[1]
        return "\n".join(_exception_lines(exc, set())) if isinstance(exc, BaseException) else ""

    def base(self, record: logging.LogRecord) -> dict[str, Any]:
        """The fields of every line: the context, time, level, service, logger, trace.

        Args:
            record: The log record.

        Returns:
            The fields, without the message.
        """
        line: dict[str, Any] = {
            **CONTEXT.get(),
            "time": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
        }
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            line["trace_id"] = format(span.trace_id, "032x")
        return line

    def format(self, record: logging.LogRecord) -> str:
        """The record as a JSON line.

        Args:
            record: The log record.

        Returns:
            One line of JSON, without its newline.
        """
        if isinstance(record.args, tuple):
            record.args = tuple(safe(arg) for arg in record.args)
        elif isinstance(record.args, Mapping):
            record.args = safe(record.args)
        if type(record.msg) is not str:
            record.msg = safe(record.msg)
        line = self.base(record)
        line["message"] = record.getMessage()
        extra = {k: v for k, v in vars(record).items() if k not in STANDARD}
        if extra:
            line["fields"] = safe_json(extra)
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=True, allow_nan=False, sort_keys=True)


class JSONHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A stream handler whose failures are written as JSON lines too."""

    def handleError(self, record: logging.LogRecord) -> None:
        """Write that RECORD could not be formatted: no message, no argument.

        The standard handler prints the error with its chain, which inside an
        ``except`` block holds the exception being handled, its message too.

        Args:
            record: The record that could not be written.
        """
        exc = sys.exc_info()[1]
        try:
            formatter = self.formatter
            line = formatter.base(record) if isinstance(formatter, JSONFormatter) else {}
            line |= {
                "level": "error",
                "logger": record.name,
                "message": "a log record could not be formatted",
                "exception": "\n".join(_exception_lines(exc, set())) if exc is not None else "",
            }
            self.stream.write(json.dumps(line, ensure_ascii=True, sort_keys=True) + "\n")
            self.flush()
        except Exception:  # noqa: S110 (nothing is left to report it to)
            pass


def _log_uncaught(
    exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
) -> None:
    logging.getLogger("potka").critical("uncaught exception", exc_info=(exc_type, exc, tb))


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
        _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)


def _log_unraisable(unraisable: Any) -> None:
    exc = unraisable.exc_value
    if exc is not None:
        info = (type(exc), exc, exc.__traceback__)
        logging.getLogger("potka").error("an exception was ignored", exc_info=info)


def _log_warning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    logging.getLogger("potka").warning(
        "a %s at %s:%d (its text not written)", category.__name__, filename, lineno
    )


def setup_logging(service: str, level: int = logging.INFO) -> None:
    """Send every log record of the process to standard output as JSON lines.

    The root logger's handlers are replaced by one JSON handler; an uncaught
    exception (in the main thread or another thread), an exception Python
    ignores and a warning are logged through it, without their text.

    Args:
        service: The service's name in the manifest.
        level: The lowest level written.
    """
    handler = JSONHandler(sys.stdout)
    handler.setFormatter(JSONFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    sys.unraisablehook = _log_unraisable
    warnings.showwarning = _log_warning
