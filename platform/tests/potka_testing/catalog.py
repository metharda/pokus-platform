"""Synthetic catalog files for the tests: written for them, taken from no catalog."""

from __future__ import annotations

from typing import Any


def taxonomy_data() -> dict[str, Any]:
    return {
        "version": 1,
        "labels": [
            {"name": "none", "description": "Nothing is wrong."},
            {"name": "unknown", "description": "Something is wrong that no label names."},
            {"name": "fixture", "description": "A fault of the fixtures."},
            {"name": "fixture.config.syntax_error", "description": "A file cannot be parsed."},
            {"name": "fixture.disk_full", "description": "A file system has no space left."},
        ],
    }


def scenario_data() -> dict[str, Any]:
    return {
        "id": "FIXTURE-001",
        "version": 2,
        "title": "A site's configuration cannot be parsed",
        "risk_level": "low",
        "isolation": "container",
        "blueprint": "fixture-web",
        "split": "dev",
        "tags": ["web", "config-error"],
        "parameters": {
            "site": {"type": "choice", "values": ["blog", "shop", "docs"]},
            "port": {"type": "range", "min": 8081, "max": 8085},
        },
        "preconditions": [
            {"probe": "http_get", "params": {"node": "web", "port": "${port}", "status": 200}}
        ],
        "setup": [
            {
                "primitive": "file.write",
                "params": {"node": "web", "path": "/srv/${site}/index.html", "content": "ok"},
            }
        ],
        "inject": [
            {
                "primitive": "nginx.corrupt_conf",
                "params": {"node": "web", "site": "${site}", "defect": "missing_semicolon"},
            }
        ],
        "confirm": [
            {"probe": "nginx_config_valid", "params": {"node": "web", "expect": False}},
        ],
        "confirm_timeout_seconds": 30,
        "settle_seconds": 5,
        "ticket": "The ${site} site on port ${port} stopped answering. It costs us $$ by the hour.",
    }


def ground_truth_data() -> dict[str, Any]:
    return {
        "fault": {
            "category": "fixture.config.syntax_error",
            "component": "the web server",
            "location": "/etc/nginx/sites-enabled/${site}.conf",
        },
        "diagnosis": {"accepted_labels": ["fixture.config.syntax_error", "fixture"]},
        "actions": {
            "acceptable": [
                {"tool": "apply_config_patch", "args": {"node": "web", "path": "/srv/${site}"}}
            ],
            "then_required": [{"tool": "nginx_reload", "args": {"node": "web"}}],
            "harmful": [{"tool": "stop_service", "args": {"node": "web"}}],
        },
        "verification": [
            {"probe": "http_get", "params": {"node": "web", "port": "${port}", "status": 200}}
        ],
        "collateral": [{"probe": "integrity", "params": {"node": "web", "paths": ["/srv/"]}}],
        "resolution_state": "The site answers again and its configuration parses.",
    }


def control_data() -> dict[str, Any]:
    return {
        "fault": {"category": "none", "component": None, "location": None},
        "diagnosis": {"accepted_labels": ["none"]},
        "actions": {"acceptable": [], "then_required": [], "harmful": []},
        "verification": [{"probe": "http_get", "params": {"node": "web", "status": 200}}],
        "collateral": [],
        "resolution_state": "The system is as it was.",
    }
