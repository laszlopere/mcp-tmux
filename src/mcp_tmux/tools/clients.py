"""Client and server introspection tools."""

from __future__ import annotations

from ..formats import CLIENT_FIELDS, FIELD_SEP


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_list_clients(session: str | None = None, target: str | None = None) -> dict:
        """List clients attached to the server.

        With `session`, lists only the clients attached to that session (-t).
        Each client has its tty, attached session, and size. Returns
        {"clients": [...]}.
        """
        cmd = ["list-clients"]
        if session:
            cmd += ["-t", session]
        records = await runner.list_records(cmd, CLIENT_FIELDS, target=target)
        return {"clients": records}

    @mcp.tool()
    async def tmux_server_info(target: str | None = None) -> dict:
        """Report basic server facts: pid, socket path, and tmux version.

        Returns {"pid", "socket_path", "version", "supported"}.
        """
        caps = await runner.capabilities(target)
        out = await runner.run_checked(
            ["display-message", "-p", f"#{{pid}}{FIELD_SEP}#{{socket_path}}"],
            target=target,
        )
        pid_s, _, socket_path = out.rstrip("\n").partition(FIELD_SEP)
        try:
            pid: int | str = int(pid_s)
        except ValueError:
            pid = pid_s
        return {
            "pid": pid,
            "socket_path": socket_path,
            "version": caps.version_str,
            "supported": caps.supported,
        }

    @mcp.tool()
    async def tmux_display_message(
        message: str,
        target_client: str | None = None,
        target_pane: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Show a message on a client's status line.

        Unlike `tmux_query` (which prints an expanded format to you via
        `display-message -p`), this displays `message` *on the tmux screen* of
        the attached client. `#{...}` format variables in `message` are expanded
        against `target_pane`'s context if given. Returns {"displayed": True}.
        """
        args = ["display-message"]
        if target_client:
            args += ["-c", target_client]
        if target_pane:
            args += ["-t", target_pane]
        args.append(message)
        await runner.run_checked(args, target=target)
        return {"displayed": True}
