"""Tool registration for the FastMCP server."""

from __future__ import annotations

from . import (
    clients,
    copymode,
    environment,
    hooks,
    io,
    keys,
    merged,
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


def register_all(mcp, runner, enabled) -> None:
    """Register the curated tools, gated by the active set of tool names.

    ``enabled`` is the resolved set of tool names (see
    :mod:`mcp_tmux.toolsets`). Every module receives it and registers only its
    tools whose name is in the set, so toolsets that span a module still work.
    """
    passthrough.register(mcp, runner, enabled)
    sessions.register(mcp, runner, enabled)
    windows.register(mcp, runner, enabled)
    panes.register(mcp, runner, enabled)
    merged.register(mcp, runner, enabled)
    io.register(mcp, runner, enabled)
    options.register(mcp, runner, enabled)
    environment.register(mcp, runner, enabled)
    wait.register(mcp, runner, enabled)
    clients.register(mcp, runner, enabled)
    plumbing.register(mcp, runner, enabled)
    hooks.register(mcp, runner, enabled)
    keys.register(mcp, runner, enabled)
    copymode.register(mcp, runner, enabled)
    stream.register(mcp, runner, enabled)
    finalize_tools(mcp)
