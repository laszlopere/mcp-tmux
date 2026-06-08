"""Read-only MCP resources for browsing tmux state on the local server.

Resources offer a no-side-effect way for clients to inspect state. They operate
on the local tmux; use the `target`-aware tools for remote hosts.
"""

from __future__ import annotations

import json

from .formats import PANE_FIELDS, SESSION_FIELDS, WINDOW_FIELDS


def register(mcp, runner) -> None:
    @mcp.resource("tmux://sessions")
    async def sessions_resource() -> str:
        """All sessions on the local tmux server, as JSON."""
        records = await runner.list_records(["list-sessions"], SESSION_FIELDS)
        return json.dumps({"sessions": records}, indent=2)

    @mcp.resource("tmux://{session}/windows")
    async def windows_resource(session: str) -> str:
        """Windows of a given local session, as JSON."""
        records = await runner.list_records(
            ["list-windows", "-t", session], WINDOW_FIELDS
        )
        return json.dumps({"session": session, "windows": records}, indent=2)

    @mcp.resource("tmux://{window}/panes")
    async def panes_resource(window: str) -> str:
        """Panes of a given local window, as JSON."""
        records = await runner.list_records(
            ["list-panes", "-t", window], PANE_FIELDS
        )
        return json.dumps({"window": window, "panes": records}, indent=2)
