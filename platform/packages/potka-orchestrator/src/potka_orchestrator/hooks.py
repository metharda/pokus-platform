"""The hooks pokus-infrastructure runs for this release: health, reconcile, drain.

Each hook prints exactly one JSON line on standard output. pokus-infrastructure
judges a hook by that line and its exit status: ``"ok": true`` (for ``drain``,
``"drained": true``) with exit status 0 is success.

No service of this release is implemented yet, so:

- ``health`` reports the release as not healthy (exit status 1);
- ``reconcile`` has nothing to clean up, since the release creates nothing;
- ``drain`` has no work in flight to finish.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="potka-hook",
        description="Run one of the release's hooks; it prints one JSON line on standard output.",
    )
    hooks = parser.add_subparsers(dest="hook", required=True, metavar="HOOK")
    hooks.add_parser("health", help="whether the release's services are healthy")
    hooks.add_parser("reconcile", help="clean up what an interrupted run left behind")
    drain = hooks.add_parser("drain", help="finish or checkpoint the work in flight")
    drain.add_argument(
        "--timeout",
        type=int,
        required=True,
        metavar="SECONDS",
        help="how long the drain may take",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the hook named on the command line.

    Args:
        argv: The arguments after the program name; the process's own when None.

    Returns:
        The exit status: 0 when the hook succeeded, 1 when it did not.
    """
    args = _parser().parse_args(argv)
    if args.hook == "health":
        _emit({"ok": False, "error": "no service of this release is implemented"})
        return 1
    if args.hook == "reconcile":
        _emit({"ok": True, "reconciled": []})
        return 0
    _emit({"drained": True})
    return 0
