"""The release's tool registry and its library of steps, as the catalog lint sees them."""

from __future__ import annotations

from potka_cli.main import library_views, tool_views
from potka_tools.registry import load_registry

TOOLS = tool_views(load_registry())
LIBRARY = library_views()
