"""Telemetry: the collector of endpoints.toml, nothing from the environment."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from potka_core.endpoints import load_endpoints
from potka_core.restricted import Restricted
from potka_core.telemetry import TelemetryError, resource, setup_telemetry

FIXTURES = Path(__file__).parents[1] / "fixtures"
LEAKED = "leaked-value"


class Collector(BaseHTTPRequestHandler):
    """An OTLP/HTTP collector that records what it receives."""

    received: ClassVar[list[tuple[str, dict[str, str], bytes]]] = []

    def do_POST(self) -> None:
        """Record the request and accept it."""
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Write nothing."""


@pytest.fixture
def collector(tmp_path: Path) -> Iterator[tuple[Path, list[tuple[str, dict[str, str], bytes]]]]:
    """A collector on a loopback port, and an endpoints.toml naming it."""
    received: list[tuple[str, dict[str, str], bytes]] = []
    handler = type("Handler", (Collector,), {"received": received})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    text = (FIXTURES / "endpoints.toml").read_text()
    port = server.server_address[1]
    endpoints = tmp_path / "endpoints.toml"
    endpoints.write_text(
        text.replace('otlp_http = "127.0.0.1:4318"', f'otlp_http = "127.0.0.1:{port}"')
    )
    try:
        yield endpoints, received
    finally:
        server.shutdown()
        server.server_close()


def test_spans_and_metrics_go_to_the_collector_and_nothing_comes_from_the_environment(
    collector: tuple[Path, list[tuple[str, dict[str, str], bytes]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    endpoints, received = collector
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://192.0.2.1:9")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "netrc").write_text(f"machine 127.0.0.1 login potka password {LEAKED}\n")
    monkeypatch.setenv("NETRC", str(tmp_path / "netrc"))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", f"authorization={LEAKED}")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", f"potka.run_id={LEAKED}")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "other")
    monkeypatch.setenv("_OTEL_PYTHON_EXPORTER_OTLP_HTTP_CREDENTIAL_PROVIDER", "x")
    with caplog.at_level(logging.WARNING):
        telemetry = setup_telemetry("exec", "1.2.3", load_endpoints(endpoints), install=False)
    assert telemetry is not None
    assert not [k for k in os.environ if k.startswith(("OTEL_", "_OTEL_"))]
    assert "OTEL_EXPORTER_OTLP_HEADERS" in caplog.text
    assert LEAKED not in caplog.text
    telemetry.tracer_provider.get_tracer("test").start_span("step").end()
    telemetry.meter_provider.get_meter("test").create_counter("steps").add(1)
    assert telemetry.tracer_provider.force_flush()
    assert telemetry.meter_provider.force_flush()
    telemetry.shutdown()
    assert {path for path, _, _ in received} == {"/v1/traces", "/v1/metrics"}
    for _, headers, body in received:
        assert "authorization" not in {key.lower() for key in headers}
        assert LEAKED.encode() not in body
        assert b"potka-exec" in body
        assert b"1.2.3" in body
        assert b"other" not in body
    assert dict(telemetry.tracer_provider.resource.attributes) == {
        "service.name": "potka-exec",
        "service.version": "1.2.3",
    }


def test_without_a_telemetry_section_nothing_is_exported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = (FIXTURES / "endpoints.toml").read_text()
    start, end = text.index("[telemetry]"), text.index("[inference]")
    (tmp_path / "endpoints.toml").write_text(text[:start] + text[end:])
    with caplog.at_level(logging.WARNING):
        assert setup_telemetry("gw", "1.2.3", load_endpoints(tmp_path / "endpoints.toml")) is None
    assert "no telemetry section" in caplog.text


class Name(str):
    """A str subclass: not a plain string."""


@pytest.mark.parametrize("value", [Restricted("exec"), Name("exec"), 3, None])
def test_the_resource_takes_plain_strings_only(value: object) -> None:
    with pytest.raises(TelemetryError):
        resource(value, "1.2.3")  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        resource("exec", value)  # type: ignore[arg-type]


INSTALL = """
import sys
from pathlib import Path
from opentelemetry import metrics, trace
from potka_core.endpoints import load_endpoints
from potka_core.telemetry import TelemetryError, setup_telemetry

endpoints = load_endpoints(Path(sys.argv[1]))
if sys.argv[2] == "early":
    trace.get_tracer("early")  # reads OTEL_PYTHON_TRACER_PROVIDER, as any early caller would
try:
    telemetry = setup_telemetry("exec", "1.2.3", endpoints)
except TelemetryError as exc:
    print("refused:", exc)
    sys.exit(0)
assert trace.get_tracer_provider() is telemetry.tracer_provider
assert metrics.get_meter_provider() is telemetry.meter_provider
try:
    setup_telemetry("exec", "1.2.3", endpoints)
except TelemetryError as exc:
    print("installed; again refused:", exc)
telemetry.shutdown()
"""


@pytest.mark.parametrize(
    ("environ", "when", "printed"),
    [
        (
            {"OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT": "abc"},
            "late",
            "installed; again refused: a tracer or meter provider was set before setup_telemetry",
        ),
        (
            {"OTEL_PYTHON_TRACER_PROVIDER": "default_tracer_provider"},
            "early",
            "refused: a tracer or meter provider was set before setup_telemetry",
        ),
    ],
    ids=["an-import-time-variable-is-dropped-first", "an-early-provider-is-refused"],
)
def test_the_providers_are_installed_once_and_only_by_setup(
    environ: dict[str, str], when: str, printed: str
) -> None:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "_OTEL_"))}
    done = subprocess.run(  # noqa: S603 (the test's own interpreter and script)
        [sys.executable, "-c", INSTALL, str(FIXTURES / "endpoints.toml"), when],
        env={**clean, **environ},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == printed
