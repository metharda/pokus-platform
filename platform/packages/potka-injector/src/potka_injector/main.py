"""The entry point of the `inject` service, which is not implemented yet.

It writes one JSON log line to standard output and exits with status 1, so
a deployment of this release never reports the service as running.
"""

from __future__ import annotations

import json
import sys

SERVICE = "inject"
MESSAGE = "not implemented in this release"


def main() -> int:
    """Log that the service is not implemented, and fail.

    Returns:
        The exit status: 1.
    """
    record = {"level": "error", "service": SERVICE, "message": MESSAGE}
    sys.stdout.write(json.dumps(record, sort_keys=True) + "\n")
    return 1
