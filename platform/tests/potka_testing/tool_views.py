"""The release's tool registry as the catalog lint sees it."""

from __future__ import annotations

from potka_cli.main import tool_views
from potka_tools.registry import load_registry

TOOLS = tool_views(load_registry())
