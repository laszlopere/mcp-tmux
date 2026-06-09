"""FastMCP server assembly for mcp-tmux."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import resources
from .config import load_config
from .runner import TmuxRunner
from .tools import register_all

INSTRUCTIONS = """\
Drive tmux: create sessions/windows/panes, send keystrokes, and read pane
output — locally or on remote hosts over SSH.

Common flow: tmux_new_session(detached) -> tmux_send_keys(pane, text=...,
enter=True) -> tmux_capture_pane(pane) to read the result.

Every tool takes an optional `target`: omit it (or pass "local") for the local
machine, a named profile from the config file, or an ad-hoc ssh destination like
"user@host". `tmux_command(args=[...])` runs ANY tmux subcommand for anything
not covered by a dedicated tool.

Several tools take a `kind` discriminator rather than one tool per entity:
tmux_kill (session/window/pane/server), tmux_rename (session/window),
tmux_select / tmux_last / tmux_swap (window/pane), tmux_respawn (pane/window).
E.g. tmux_kill(kind="window", id="dev:2") or tmux_kill(kind="server").
"""


def build_server(config: dict[str, Any] | None = None, config_path: Path | None = None) -> FastMCP:
    """Construct and fully wire the FastMCP server."""
    cfg = config if config is not None else load_config(config_path)
    runner = TmuxRunner(cfg)
    mcp = FastMCP("tmux", instructions=INSTRUCTIONS)
    register_all(mcp, runner)
    resources.register(mcp, runner)
    return mcp
