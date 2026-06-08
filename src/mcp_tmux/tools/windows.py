"""Window tools."""

from __future__ import annotations

from ..formats import FIELD_SEP, WINDOW_FIELDS, parse_records


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_list_windows(
        session: str | None = None, target: str | None = None
    ) -> dict:
        """List windows. With `session`, lists that session's windows; otherwise
        all windows on the server (-a)."""
        cmd = ["list-windows"]
        if session:
            cmd += ["-t", session]
        else:
            cmd += ["-a"]
        records = await runner.list_records(cmd, WINDOW_FIELDS, target=target)
        return {"windows": records}

    @mcp.tool()
    async def tmux_new_window(
        session: str | None = None,
        name: str | None = None,
        start_directory: str | None = None,
        command: str | None = None,
        select: bool = True,
        target: str | None = None,
    ) -> dict:
        """Create a window. `session` is the target session (or "sess:index").

        Set select=False to create it in the background (-d). Returns the new
        window's {"id", "index", "name"}.
        """
        args = ["new-window"]
        if not select:
            args.append("-d")
        if session:
            args += ["-t", session]
        if name:
            args += ["-n", name]
        if start_directory:
            args += ["-c", start_directory]
        args += [
            "-P",
            "-F",
            f"#{{window_id}}{FIELD_SEP}#{{window_index}}{FIELD_SEP}#{{window_name}}",
        ]
        if command:
            args.append(command)
        out = await runner.run_checked(args, target=target)
        rec = parse_records(out, ["id", "index", "name"])
        return rec[0] if rec else {}

    @mcp.tool()
    async def tmux_select_window(window: str, target: str | None = None) -> dict:
        """Make a window the active one (e.g. window="mysess:2")."""
        await runner.run_checked(["select-window", "-t", window], target=target)
        return {"selected": window}

    @mcp.tool()
    async def tmux_rename_window(
        window: str, new_name: str, target: str | None = None
    ) -> dict:
        """Rename a window."""
        await runner.run_checked(
            ["rename-window", "-t", window, new_name], target=target
        )
        return {"renamed": True, "name": new_name}

    @mcp.tool()
    async def tmux_move_window(
        src: str, dst: str, target: str | None = None
    ) -> dict:
        """Move/renumber a window from `src` to `dst` (e.g. "sess:5")."""
        await runner.run_checked(["move-window", "-s", src, "-t", dst], target=target)
        return {"moved": True, "src": src, "dst": dst}

    @mcp.tool()
    async def tmux_swap_window(
        src: str, dst: str, target: str | None = None
    ) -> dict:
        """Swap two windows."""
        await runner.run_checked(["swap-window", "-s", src, "-t", dst], target=target)
        return {"swapped": True, "src": src, "dst": dst}

    @mcp.tool()
    async def tmux_kill_window(window: str, target: str | None = None) -> dict:
        """Kill a window (destructive)."""
        await runner.run_checked(["kill-window", "-t", window], target=target)
        return {"killed": True, "window": window}
