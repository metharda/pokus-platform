"""A run's context for the tests of potka-tools."""

from __future__ import annotations

import json

from potka_tools.context import Context


def context() -> Context:
    """A run of two nodes: a web node with paths and endpoints, and a database node."""
    web = {
        "alias": "web",
        "services": ["nginx.service", "webapp.service"],
        "readable_paths": ["/etc/nginx", "/srv/webapp", "/var/log/nginx"],
        "patchable_paths": [
            {"path": "/etc/nginx/sites-available/default", "validator": ["nginx", "-t"]},
            {"path": "/srv/conf", "validator": None},
        ],
        "endpoints": [
            {"name": "site", "scheme": "http", "port": 80, "path": "/"},
            {"name": "admin", "scheme": "https", "port": 8443, "path": "/health"},
            {"name": "raw", "scheme": "tcp", "port": 9000, "path": None},
        ],
    }
    db = {
        "alias": "db",
        "services": ["postgresql.service"],
        "readable_paths": ["/var/lib/postgresql/16/main/log"],
        "patchable_paths": [],
        "endpoints": [],
    }
    return Context.model_validate_json(json.dumps({"nodes": [web, db]}), strict=True)
