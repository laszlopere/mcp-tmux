"""FastMCP server assembly for mcp-tmux."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import resources
from .config import load_config
from .runner import TmuxRunner
from .tools import register_all
from .toolsets import resolve_enabled, select_toolsets

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
tmux_select / tmux_last / tmux_swap (window/pane), tmux_respawn (pane/window),
tmux_list (session/window/client/buffer). E.g. tmux_kill(kind="window",
id="dev:2"), tmux_kill(kind="server"), or tmux_list(kind="window", scope="dev").
Panes are listed by their own tmux_list_panes (it has two scope axes).

Tools are grouped into toolsets; a session loads `core` + `automation` by
default (the create -> send -> read loop, the wait/run helpers, and the
`tmux_command` passthrough that can reach anything not loaded). Widen the
surface with the `toolsets` config key or `MCP_TMUX_TOOLSETS` env var (e.g.
"core,stream" or "all"). Opt-in groups: automation, layout, buffers, config,
keybindings, copymode, clients, stream.
"""


def build_server(config: dict[str, Any] | None = None, config_path: Path | None = None) -> FastMCP:
    """Construct and fully wire the FastMCP server."""
    cfg = config if config is not None else load_config(config_path)
    runner = TmuxRunner(cfg)
    mcp = FastMCP("tmux", instructions=INSTRUCTIONS)
    enabled = resolve_enabled(select_toolsets(cfg))
    register_all(mcp, runner, enabled)
    resources.register(mcp, runner)
    return mcp
