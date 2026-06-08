"""Tool registration for the FastMCP server."""

from __future__ import annotations

from . import (
    clients,
    copymode,
    environment,
    hooks,
    io,
    keys,
    options,
    panes,
    passthrough,
    plumbing,
    sessions,
    stream,
    wait,
    windows,
)
from ._util import finalize_tools


def register_all(mcp, runner) -> None:
    passthrough.register(mcp, runner)
    sessions.register(mcp, runner)
    windows.register(mcp, runner)
    panes.register(mcp, runner)
    io.register(mcp, runner)
    options.register(mcp, runner)
    environment.register(mcp, runner)
    wait.register(mcp, runner)
    clients.register(mcp, runner)
    plumbing.register(mcp, runner)
    hooks.register(mcp, runner)
    keys.register(mcp, runner)
    copymode.register(mcp, runner)
    stream.register(mcp, runner)
    finalize_tools(mcp)
