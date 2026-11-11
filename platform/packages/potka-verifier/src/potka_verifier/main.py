"""The entry point of the `verify` service (verifier), which is not implemented yet.

It serves its sockets from systemd with ``/healthz`` and ``/readyz``, the
latter answering 503 "not implemented", and nothing else.
"""

from __future__ import annotations

from potka_core.server import run_not_implemented

SERVICE = "verify"


def main() -> int:
    """Serve the service's sockets until SIGTERM.

    Returns:
        The exit status: 0 after a graceful stop, 1 when the service cannot start.
    """
    return run_not_implemented(SERVICE)
