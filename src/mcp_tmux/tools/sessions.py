"""Session tools."""

from __future__ import annotations

from ..formats import FIELD_SEP, SESSION_FIELDS, parse_records


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_list_sessions(target: str | None = None) -> dict:
        """List all sessions on the target with id, name, window count, etc."""
        records = await runner.list_records(["list-sessions"], SESSION_FIELDS, target=target)
        return {"sessions": records}

    @mcp.tool()
    async def tmux_has_session(session: str, target: str | None = None) -> dict:
        """Check whether a session exists. Returns {"exists": bool}."""
        result = await runner.run(["has-session", "-t", session], target=target)
        return {"exists": result.ok}

    @mcp.tool()
    async def tmux_new_session(
        name: str | None = None,
        start_directory: str | None = None,
        command: str | None = None,
        width: int | None = None,
        height: int | None = None,
        detached: bool = True,
        target: str | None = None,
    ) -> dict:
        """Create a new session (detached by default).

        Optional `command` runs as the first pane's command; `start_directory`
        sets the working dir; `width`/`height` size the detached session.
        Returns the created session's {"id", "name"}.
        """
        args = ["new-session"]
        if detached:
            args.append("-d")
        if name:
            args += ["-s", name]
        if start_directory:
            args += ["-c", start_directory]
        if width:
            args += ["-x", str(width)]
        if height:
            args += ["-y", str(height)]
        args += ["-P", "-F", f"#{{session_id}}{FIELD_SEP}#{{session_name}}"]
        if command:
            args.append(command)
        out = await runner.run_checked(args, target=target)
        rec = parse_records(out, ["id", "name"])
        return rec[0] if rec else {"id": "", "name": name or ""}

    @mcp.tool()
    async def tmux_rename_session(
        session: str, new_name: str, target: str | None = None
    ) -> dict:
        """Rename a session."""
        await runner.run_checked(
            ["rename-session", "-t", session, new_name], target=target
        )
        return {"renamed": True, "name": new_name}

    @mcp.tool()
    async def tmux_kill_session(session: str, target: str | None = None) -> dict:
        """Kill a session (destructive). Ends all its windows and panes."""
        await runner.run_checked(["kill-session", "-t", session], target=target)
        return {"killed": True, "session": session}
