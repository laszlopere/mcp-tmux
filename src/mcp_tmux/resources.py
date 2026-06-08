"""Read-only MCP resources for browsing tmux state.

Resources offer a no-side-effect way for clients to inspect state. The bare
`tmux://...` resources operate on the local server; the `tmux://{target}/...`
variants take a target (a named profile or an ssh destination) so remote servers
can be browsed the same way. (`target` = "local" is equivalent to the bare form.)
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
        records = await runner.list_records(["list-windows", "-t", session], WINDOW_FIELDS)
        return json.dumps({"session": session, "windows": records}, indent=2)

    @mcp.resource("tmux://{window}/panes")
    async def panes_resource(window: str) -> str:
        """Panes of a given local window, as JSON."""
        records = await runner.list_records(["list-panes", "-t", window], PANE_FIELDS)
        return json.dumps({"window": window, "panes": records}, indent=2)

    # --- Target-aware variants (browse a remote/named target) ----------------

    @mcp.resource("tmux://{target}/sessions")
    async def target_sessions_resource(target: str) -> str:
        """All sessions on `target`'s tmux server, as JSON."""
        records = await runner.list_records(["list-sessions"], SESSION_FIELDS, target=target)
        return json.dumps({"target": target, "sessions": records}, indent=2)

    @mcp.resource("tmux://{target}/{session}/windows")
    async def target_windows_resource(target: str, session: str) -> str:
        """Windows of `session` on `target`, as JSON."""
        records = await runner.list_records(
            ["list-windows", "-t", session], WINDOW_FIELDS, target=target
        )
        return json.dumps({"target": target, "session": session, "windows": records}, indent=2)

    @mcp.resource("tmux://{target}/{window}/panes")
    async def target_panes_resource(target: str, window: str) -> str:
        """Panes of `window` on `target`, as JSON."""
        records = await runner.list_records(
            ["list-panes", "-t", window], PANE_FIELDS, target=target
        )
        return json.dumps({"target": target, "window": window, "panes": records}, indent=2)
