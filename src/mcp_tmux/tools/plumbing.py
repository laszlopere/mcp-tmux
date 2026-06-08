"""Window/pane plumbing: link, break, join, find, and pipe.

These are the less-common structural operations — moving a window into another
session, splitting a pane out into its own window (and back), searching for a
window by name, and streaming a pane's output to a command.
"""

from __future__ import annotations

from ..formats import FIELD_SEP, WINDOW_FIELDS, parse_records


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_link_window(
        src: str, dst: str, select: bool = True, target: str | None = None
    ) -> dict:
        """Link a window into another location (it then appears in both).

        `src` is the source window (e.g. "sess1:2"), `dst` the destination
        (e.g. "sess2:" for the next free index, or "sess2:5"). select=False
        links it in the background (-d). The same window now lives in both spots;
        use `tmux_unlink_window` to remove a link.
        """
        args = ["link-window", "-s", src, "-t", dst]
        if not select:
            args.append("-d")
        await runner.run_checked(args, target=target)
        return {"linked": True, "src": src, "dst": dst}

    @mcp.tool()
    async def tmux_unlink_window(
        window: str, target: str | None = None
    ) -> dict:
        """Unlink a window (remove one of its links).

        Fails if the window is only linked once, unless it is not the last —
        tmux refuses to leave a window with no links. Use `tmux_kill_window` to
        destroy it outright.
        """
        await runner.run_checked(["unlink-window", "-t", window], target=target)
        return {"unlinked": True, "window": window}

    @mcp.tool()
    async def tmux_break_pane(
        target_pane: str,
        window_name: str | None = None,
        select: bool = True,
        target: str | None = None,
    ) -> dict:
        """Break a pane out into a new window of its own.

        `window_name` names the new window; select=False creates it in the
        background (-d). Returns the new window's {"id", "index", "name"}.
        """
        args = ["break-pane", "-s", target_pane]
        if not select:
            args.append("-d")
        if window_name:
            args += ["-n", window_name]
        args += [
            "-P",
            "-F",
            f"#{{window_id}}{FIELD_SEP}#{{window_index}}{FIELD_SEP}#{{window_name}}",
        ]
        out = await runner.run_checked(args, target=target)
        rec = parse_records(out, ["id", "index", "name"])
        return rec[0] if rec else {}

    @mcp.tool()
    async def tmux_join_pane(
        src: str,
        dst: str,
        vertical: bool = False,
        percentage: int | None = None,
        select: bool = True,
        target: str | None = None,
    ) -> dict:
        """Join pane `src` into `dst`'s window as a new split (the inverse of
        break-pane).

        By default splits left/right; vertical=True stacks top/bottom.
        `percentage` sizes the joined pane. select=False keeps focus on the
        original pane (-d).
        """
        args = ["join-pane", "-v" if vertical else "-h", "-s", src, "-t", dst]
        if not select:
            args.append("-d")
        if percentage is not None:
            args += ["-p", str(percentage)]
        await runner.run_checked(args, target=target)
        return {"joined": True, "src": src, "dst": dst}

    @mcp.tool()
    async def tmux_find_window(
        pattern: str,
        session: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Find windows whose name, title, or current command matches `pattern`.

        `pattern` is matched case-insensitively as a substring. Scope to a
        `session`, or search the whole server (default). Returns the matching
        window records {"matches": [...]}.

        (tmux's own `find-window` is interactive — it opens a chooser in an
        attached client — so this performs a non-interactive search over
        `list-windows` instead, which is what an agent actually wants.)
        """
        cmd = ["list-windows"]
        if session:
            cmd += ["-t", session]
        else:
            cmd += ["-a"]
        records = await runner.list_records(cmd, WINDOW_FIELDS, target=target)
        needle = pattern.lower()
        matches = [
            r
            for r in records
            if needle in str(r.get("name", "")).lower()
            or needle in str(r.get("title", "")).lower()
        ]
        return {"matches": matches}

    @mcp.tool()
    async def tmux_pipe_pane(
        target_pane: str,
        command: str | None = None,
        only_new: bool = False,
        target: str | None = None,
    ) -> dict:
        """Pipe a pane's output to a shell command (great for logging).

        With `command`, tmux feeds everything the pane prints to that command's
        stdin, e.g. command="cat >> /tmp/pane.log". only_new=True (-o) toggles:
        if a pipe is already open it is closed, otherwise opened. Call with no
        `command` to stop piping. Returns {"piping": bool}.
        """
        args = ["pipe-pane"]
        if only_new:
            args.append("-o")
        args += ["-t", target_pane]
        if command:
            args.append(command)
        await runner.run_checked(args, target=target)
        return {"piping": bool(command), "pane": target_pane}
