"""OpenTelemetry for a service: traces and metrics over OTLP/HTTP.

The exporters send to the collector address of ``endpoints.toml``
(``telemetry.otlp_http``), which exists when the manifest requires
``telemetry``. The SDK and the exporters read ``OTEL_*`` and ``_OTEL_*``
variables (headers, certificates, compression, a credential provider, the
sampler, the providers, extra resource attributes), some of them when the
SDK is imported, so `setup_telemetry` removes them from the process
environment before it imports the SDK; the exporters' HTTP sessions ignore
the environment too (proxies, netrc). The endpoints, the timeout and the
sampler are given here; the SDK's other settings (batch sizes, intervals,
limits) are its defaults. The resource carries the service's name and the
release's version, plain strings, never a restricted value.

A service calls `setup_telemetry` before anything sets a tracer or meter
provider; a provider set before it is refused. The services do not fork:
in a forked child the SDK adds its own ``service.instance.id`` to the
resource.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests
from opentelemetry import metrics, trace

from potka_core.endpoints import Endpoints

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

EXPORT_TIMEOUT_SECONDS = 10.0
log = logging.getLogger("potka.telemetry")


class TelemetryError(ValueError):
    """A resource attribute is not a plain string, or a provider was set before."""


@dataclass(frozen=True)
class Telemetry:
    """The service's tracer and meter providers."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider

    def shutdown(self) -> None:
        """Flush and stop both exporters."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def resource(service: str, version: str) -> Resource:
    """The resource of SERVICE at VERSION, read from nothing else.

    Args:
        service: The service's name in the manifest.
        version: The release's version.

    Returns:
        The resource (``service.name`` ``potka-<service>``, ``service.version``).

    Raises:
        TelemetryError: An argument is not a plain string (a restricted value,
            for example).
    """
    if type(service) is not str or type(version) is not str:
        raise TelemetryError("resource attributes are plain strings")
    from opentelemetry.sdk.resources import Resource

    return Resource({"service.name": f"potka-{service}", "service.version": version})


def drop_otel_environment() -> list[str]:
    """Remove every ``OTEL_*`` and ``_OTEL_*`` variable from the process environment.

    Returns:
        The names removed (never their values).
    """
    names = sorted(k for k in os.environ if k.startswith(("OTEL_", "_OTEL_")))
    for name in names:
        del os.environ[name]
    return names


def _session() -> requests.Session:
    """An HTTP session that reads nothing from the environment (proxies, netrc)."""
    session = requests.Session()
    session.trust_env = False
    return session


def setup_telemetry(
    service: str, version: str, endpoints: Endpoints, install: bool = True
) -> Telemetry | None:
    """Export SERVICE's traces and metrics to the collector of ``endpoints.toml``.

    Args:
        service: The service's name in the manifest.
        version: The release's version.
        endpoints: ``endpoints.toml``.
        install: Make the providers the process's global ones.

    Returns:
        The providers, or None when ``endpoints.toml`` has no telemetry
        section (the manifest does not require it).

    Raises:
        TelemetryError: With INSTALL, a tracer or meter provider was set
            before (the providers are shut down).
    """
    dropped = drop_otel_environment()
    if dropped:
        log.warning("OTEL_* and _OTEL_* variables removed, not used: %s", ", ".join(dropped))
    if endpoints.telemetry is None:
        log.warning("endpoints.toml has no telemetry section: nothing is exported")
        return None
    # The SDK is imported only now, after the variables are removed.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

    base = f"http://{endpoints.telemetry.otlp_http}"
    attributes = resource(service, version)
    tracer_provider = TracerProvider(resource=attributes, sampler=ParentBased(ALWAYS_ON))
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{base}/v1/traces", timeout=EXPORT_TIMEOUT_SECONDS, session=_session()
            )
        )
    )
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(
            endpoint=f"{base}/v1/metrics", timeout=EXPORT_TIMEOUT_SECONDS, session=_session()
        )
    )
    meter_provider = MeterProvider(resource=attributes, metric_readers=[reader])
    telemetry = Telemetry(tracer_provider, meter_provider)
    if install:
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        if (
            trace.get_tracer_provider() is not tracer_provider
            or metrics.get_meter_provider() is not meter_provider
        ):
            telemetry.shutdown()
            raise TelemetryError("a tracer or meter provider was set before setup_telemetry")
    return telemetry
