"""Tool registration for the FastMCP server."""

from __future__ import annotations

from . import io, options, panes, passthrough, sessions, windows


def register_all(mcp, runner) -> None:
    passthrough.register(mcp, runner)
    sessions.register(mcp, runner)
    windows.register(mcp, runner)
    panes.register(mcp, runner)
    io.register(mcp, runner)
    options.register(mcp, runner)
