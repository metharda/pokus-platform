"""pytest plugin: every skipped test is named; in CI only the listed ones may skip.

Loaded for every pytest run of the repository (``-p potka_ci.skip_policy``
in ``platform/pyproject.toml``). After the run it prints one line per
skipped test with its reason, locally too, so the gate's and CI's logs name
every skip. In CI (``CI=true``, set by GitHub Actions) a skipped test, or a
module skipped at collection, that the CI skip list (``ci/ci-skips.json``)
does not name for the run ``pytest`` is reported as failed.

An expected failure (``xfail``) is reported by pytest as a skip and is
treated as one; an unexpected pass of an ``xfail`` test fails everywhere
(``xfail_strict`` in ``platform/pyproject.toml``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator, Mapping
from pathlib import Path

import pytest

LIST = Path(__file__).resolve().parents[1] / "ci-skips.json"
RUN = "pytest"
LINE_PREFIX = "skipped test: "
ALLOWED = pytest.StashKey[dict[str, str]]()
SKIPPED = pytest.StashKey[list[tuple[str, str]]]()


def allowed(run: str, path: Path = LIST) -> dict[str, str]:
    """The tests that may skip in CI in one run, from the CI skip list.

    Args:
        run: The run the entries are for (``pytest``).
        path: The list.

    Returns:
        Test id to reason.

    Raises:
        ValueError: The list is not an object with a ``skips`` array of
            entries that each have a non-empty ``test``, ``run`` and
            ``reason``.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("skips") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path}: no skips array")
    found: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("test", "run", "reason")
        ):
            raise ValueError(f"{path}: an entry without test, run and reason")
        if entry["run"] == run:
            found[entry["test"]] = entry["reason"]
    return found


def in_ci(environ: Mapping[str, str] | None = None) -> bool:
    """Whether this runs in CI (GitHub Actions sets ``CI=true``).

    Args:
        environ: The environment (default: this process's).

    Returns:
        True in CI.
    """
    return (os.environ if environ is None else environ).get("CI") == "true"


def line(test: str, reason: str) -> str:
    """The line that names a skipped test.

    Args:
        test: Its id.
        reason: Why it skipped.

    Returns:
        ``skipped test: <id> (reason: <reason>)``, on one line.
    """
    return f"{LINE_PREFIX}{test} (reason: {' '.join(reason.split())})"


def refusal(test: str, reason: str) -> str:
    """The failure of a skip in CI that the list does not name.

    Args:
        test: Its id.
        reason: Why it skipped.

    Returns:
        The message.
    """
    return f"{test} skipped in CI, and ci/ci-skips.json does not name it: {reason}"


def _reason(report: pytest.TestReport | pytest.CollectReport) -> str:
    wasxfail = getattr(report, "wasxfail", None)
    if wasxfail is not None:
        return f"expected to fail: {wasxfail}" if wasxfail else "expected to fail"
    longrepr = report.longrepr
    text = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    return text.removeprefix("Skipped: ")


def pytest_addoption(parser: pytest.Parser) -> None:
    """The ``ci_skip_list`` ini option (the plugin's own tests use other lists)."""
    parser.addini("ci_skip_list", "the CI skip list", default=str(LIST))


def pytest_configure(config: pytest.Config) -> None:
    """Read the CI skip list once and start recording skips."""
    config.stash[ALLOWED] = allowed(RUN, Path(str(config.getini("ci_skip_list"))))
    config.stash[SKIPPED] = []
    config.pluginmanager.register(_Recorder(config), "potka-skip-recorder")


def _judge(config: pytest.Config, report: pytest.TestReport | pytest.CollectReport) -> None:
    if in_ci() and report.nodeid not in config.stash[ALLOWED]:
        reason = _reason(report)
        # pytest counts a failed report that still says "xfail" as no failure.
        if hasattr(report, "wasxfail"):
            del report.wasxfail
        report.outcome = "failed"
        report.longrepr = refusal(report.nodeid, reason)


class _Recorder:
    """Records every skip the run reports."""

    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """A skipped test (in any phase)."""
        if report.skipped:
            self.config.stash[SKIPPED].append((report.nodeid, _reason(report)))

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        """A module skipped as a whole at collection."""
        if report.skipped:
            self.config.stash[SKIPPED].append((report.nodeid, _reason(report)))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """In CI, fail a skipped test unless the list names it."""
    report = yield
    if report.skipped:
        _judge(item.config, report)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    """In CI, fail a module skipped at collection unless the list names it."""
    report = yield
    if report.skipped:
        _judge(collector.config, report)
    return report


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Name every skipped test, also with ``-q``."""
    for test, reason in config.stash[SKIPPED]:
        terminalreporter.write_line(line(test, reason))
