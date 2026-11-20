"""Tool entries and registries for the tests of potka-tools.

The entries are plain data, as a registry file holds them; a test changes
one with :func:`changed` and writes a registry with :func:`write_registry`.
JSON is YAML of the platform's subset, so the files are written as JSON.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from potka_tools.registry import Tool

NODE = {"type": "string", "from": "context.nodes"}
UNIT = {"type": "string", "pattern": r"^[a-z0-9@._-]+\.service$", "from": "context.node.services"}
LIMITS = {"timeout_s": 30, "kill_after_s": 5, "max_output_bytes": 65536}
SINCE = {"type": "string", "pattern": "^[0-9]{1,4}[smhd]$"}


def restart_service() -> dict[str, Any]:
    """A mutating tool that runs a command and a check after it."""
    return {
        "name": "restart_service",
        "version": 3,
        "description": "Restart a systemd service on a node.",
        "risk": "medium",
        "mutating": True,
        "applicable_categories": ["service.stopped", "config.wrong_value"],
        "args": {"node": dict(NODE), "unit": dict(UNIT)},
        "exec": {"argv": ["systemctl", "restart", "{unit}"], **LIMITS},
        "post_check": {"argv": ["systemctl", "is-active", "{unit}"]},
    }


def tail_journal() -> dict[str, Any]:
    """A read-only tool with an integer that has a default."""
    return {
        "name": "tail_journal",
        "version": 1,
        "description": "The last lines of a unit's journal.",
        "risk": "read",
        "mutating": False,
        "applicable_categories": [],
        "args": {
            "node": dict(NODE),
            "unit": dict(UNIT),
            "lines": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
        "exec": {
            "argv": ["journalctl", "--no-pager", "--unit={unit}", "--lines={lines}"],
            **LIMITS,
        },
    }


def remove_file() -> dict[str, Any]:
    """A mutating tool whose path lies below fixed roots."""
    return {
        "name": "remove_file",
        "version": 1,
        "description": "Remove a temporary file.",
        "risk": "medium",
        "mutating": True,
        "applicable_categories": ["resource.disk_full"],
        "args": {
            "node": dict(NODE),
            "path": {"type": "path", "within": ["/tmp", "/var/tmp", "/var/cache"]},  # noqa: S108
        },
        "exec": {"argv": ["rm", "--force", "--", "{path}"], **LIMITS},
    }


def http_probe() -> dict[str, Any]:
    """A read-only tool that runs on the probe client against a node's endpoint."""
    return {
        "name": "http_probe",
        "version": 1,
        "description": "Ask a node's HTTP endpoint from the probe client.",
        "risk": "read",
        "mutating": False,
        "applicable_categories": [],
        "args": {
            "node": dict(NODE),
            "endpoint": {"type": "string", "from": "context.node.http_endpoints"},
            "via": {"type": "string", "from": "context.nodes"},
        },
        "exec": {"argv": ["curl", "--silent", "{endpoint}"], "on": "probe_client", **LIMITS},
    }


def tool(entry: dict[str, Any]) -> Tool:
    """ENTRY as the registry holds it."""
    return Tool.model_validate_json(json.dumps(entry), strict=True)


def handler(name: str) -> dict[str, Any]:
    """A tool that one of the five handlers carries out, with exactly its arguments."""
    table: dict[str, dict[str, dict[str, Any]]] = {
        "list_nodes": {},
        "read_file": {
            "node": dict(NODE),
            "path": {"type": "path", "from": "context.node.readable_paths"},
        },
        "apply_config_patch": {
            "node": dict(NODE),
            "path": {"type": "path", "from": "context.node.patchable_paths"},
            "diff": {"type": "text", "max_length": 65536},
        },
        "query_logs": {
            "node": dict(NODE),
            "service": {"type": "string", "from": "context.node.services"},
            "since": dict(SINCE),
            "level": {"type": "string", "enum": ["error", "warning", "info"]},
        },
        "query_metrics": {
            "node": dict(NODE),
            "metric": {"type": "string", "enum": ["cpu", "memory"]},
            "since": dict(SINCE),
        },
    }
    args = table[name]
    mutating = name == "apply_config_patch"
    return {
        "name": name,
        "version": 1,
        "description": f"What {name} does.",
        "risk": "medium" if mutating else "read",
        "mutating": mutating,
        "applicable_categories": ["config.syntax_error"] if mutating else [],
        "args": args,
        "handler": name,
        "max_output_bytes": 65536,
    }


def forbidden(name: str = "reboot") -> dict[str, Any]:
    """A tool that is in the registry to be denied."""
    return {
        "name": name,
        "version": 1,
        "description": "Restart the machine.",
        "risk": "forbidden",
        "mutating": True,
        "applicable_categories": [],
    }


def changed(entry: dict[str, Any], path: str, value: Any = None, *, drop: bool = False) -> Any:
    """A copy of ENTRY with the key at PATH (dots between keys) set to VALUE, or dropped."""
    result = copy.deepcopy(entry)
    *above, last = path.split(".")
    place = result
    for key in above:
        place = place[key]
    if drop:
        del place[last]
    else:
        place[last] = value
    return result


def write_registry(directory: Path, *files: list[dict[str, Any]], version: str = "1\n") -> Path:
    """Write a registry: its version and one file of entries for each of FILES."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "VERSION").write_text(version, encoding="utf-8")
    for number, entries in enumerate(files, 1):
        (directory / f"tools-{number}.yaml").write_text(json.dumps(entries), encoding="utf-8")
    return directory
