"""A small synthetic catalog as files, for the tests of the loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from potka_testing.catalog import control_data, ground_truth_data, scenario_data, taxonomy_data


def blueprint_data() -> dict[str, Any]:
    return {
        "name": "fixture-web",
        "version": 3,
        "probe_client": {"blueprint": "probe-client", "version": "1.0.0"},
        "nodes": [
            {
                "alias": "web",
                "golden": {"blueprint": "web-nginx-flask", "version": "1.0.0"},
                "derived": {"name": "fixture-web-jobs", "version": 2},
                "services": ["nginx.service", "webapp.service"],
                "components": [
                    {
                        "name": "report-builder",
                        "settings_file": "/etc/fixture/report-builder.conf",
                        "settings": {"workers": 1, "batch_size": 50, "mode": "nightly"},
                    }
                ],
                "allowed_read_paths": ["/etc/nginx", "/etc/fixture", "/var/log/nginx"],
                "patchable_paths": [
                    {"path": "/etc/nginx/nginx.conf", "validator": ["nginx", "-t"]},
                    {"path": "/etc/fixture", "validator": None},
                ],
                "endpoints": [{"name": "site", "scheme": "http", "port": 80, "path": "/"}],
            },
            {
                "alias": "cache",
                "golden": {"blueprint": "redis-cache", "version": "1.0.0"},
                "services": ["redis-server.service"],
                "components": [],
                "allowed_read_paths": ["/etc/redis"],
                "patchable_paths": [],
                "endpoints": [{"name": "store", "scheme": "tcp", "port": 6379, "path": None}],
            },
        ],
        "health": [
            {"probe": "http_get", "params": {"node": "web", "endpoint": "site", "status": 200}}
        ],
    }


def catalog_files() -> dict[str, Any]:
    """A small catalog: each file's path and its data, or its text for an overlay's file."""
    return {
        "taxonomy.yaml": taxonomy_data(),
        "blueprints/fixture-web.yaml": blueprint_data(),
        "blueprints/fixture-web-jobs/overlay/install.sh": "systemctl enable report-builder.timer\n",
        "blueprints/fixture-web-jobs/overlay/files/etc/fixture/report-builder.conf": "workers=1\n",
        "scenarios/FIXTURE-001/2/scenario.yaml": scenario_data(),
        "scenarios/FIXTURE-001/2/ground_truth.yaml": ground_truth_data(),
        "scenarios/FIXTURE-CONTROL-000/1/scenario.yaml": scenario_data()
        | {"id": "FIXTURE-CONTROL-000", "version": 1, "inject": []},
        "scenarios/FIXTURE-CONTROL-000/1/ground_truth.yaml": control_data(),
    }


def write_catalog(root: Path, files: dict[str, Any] | None = None) -> Path:
    """Write FILES (`catalog_files` when None) below ROOT, data as YAML, and return ROOT."""
    for name, content in (catalog_files() if files is None else files).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False)
        path.write_text(text, encoding="utf-8")
    return root
