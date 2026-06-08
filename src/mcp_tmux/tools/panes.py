"""Pane tools (excluding I/O, which lives in io.py)."""

from __future__ import annotations

from ..formats import FIELD_SEP, PANE_FIELDS, parse_records


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_list_panes(
        window: str | None = None,
        session: str | None = None,
        target: str | None = None,
    ) -> dict:
        """List panes. Scope to a `window` (-t), a `session` (-s), or the whole
        server (default, -a)."""
        cmd = ["list-panes"]
        if window:
            cmd += ["-t", window]
        elif session:
            cmd += ["-s", "-t", session]
        else:
            cmd += ["-a"]
        records = await runner.list_records(cmd, PANE_FIELDS, target=target)
        return {"panes": records}

    @mcp.tool()
    async def tmux_split_window(
        target_pane: str | None = None,
        vertical: bool = False,
        start_directory: str | None = None,
        command: str | None = None,
        percentage: int | None = None,
        select: bool = True,
        target: str | None = None,
    ) -> dict:
        """Split a pane. By default splits left/right; vertical=True splits
        top/bottom. `percentage` sizes the new pane (e.g. 30). select=False
        keeps focus on the original pane (-d).

        Returns the new pane's {"id", "index"}.
        """
        # tmux convention: -h = horizontal split = side-by-side (left/right);
        # -v = vertical split = stacked (top/bottom).
        args = ["split-window", "-v" if vertical else "-h"]
        if not select:
            args.append("-d")
        if target_pane:
            args += ["-t", target_pane]
        if start_directory:
            args += ["-c", start_directory]
        if percentage is not None:
            args += ["-p", str(percentage)]
        args += ["-P", "-F", f"#{{pane_id}}{FIELD_SEP}#{{pane_index}}"]
        if command:
            args.append(command)
        out = await runner.run_checked(args, target=target)
        rec = parse_records(out, ["id", "index"])
        return rec[0] if rec else {}

    @mcp.tool()
    async def tmux_select_pane(target_pane: str, target: str | None = None) -> dict:
        """Make a pane the active one."""
        await runner.run_checked(["select-pane", "-t", target_pane], target=target)
        return {"selected": target_pane}

    @mcp.tool()
    async def tmux_last_pane(
        window: str | None = None, target: str | None = None
    ) -> dict:
        """Switch to the previously active pane (last-pane).

        With `window` (-t), acts on that window; otherwise the current one.
        Returns {"selected": "last"}.
        """
        args = ["last-pane"]
        if window:
            args += ["-t", window]
        await runner.run_checked(args, target=target)
        return {"selected": "last"}

    @mcp.tool()
    async def tmux_set_pane_title(
        title: str, target_pane: str | None = None, target: str | None = None
    ) -> dict:
        """Set a pane's title (select-pane -T, tmux 2.6+).

        The title is the `#{pane_title}` reported by `tmux_list_panes`; it's a
        handy label for agents driving several panes (it does not change the
        window name). Requires tmux 2.6+; on older tmux it is a no-op with a
        note.

        Returns {"pane": target_pane, "title": title} (or {"notes": [...]}).
        """
        caps = await runner.capabilities(target)
        if not caps.has("pane_title"):
            return {"notes": ["pane title ignored: select-pane -T requires tmux 2.6+"]}
        args = ["select-pane"]
        if target_pane:
            args += ["-t", target_pane]
        args += ["-T", title]
        await runner.run_checked(args, target=target)
        return {"pane": target_pane, "title": title}

    @mcp.tool()
    async def tmux_resize_pane(
        target_pane: str,
        left: int | None = None,
        right: int | None = None,
        up: int | None = None,
        down: int | None = None,
        target: str | None = None,
    ) -> dict:
        """Resize a pane by a number of cells in one or more directions."""
        args = ["resize-pane", "-t", target_pane]
        if left:
            args += ["-L", str(left)]
        if right:
            args += ["-R", str(right)]
        if up:
            args += ["-U", str(up)]
        if down:
            args += ["-D", str(down)]
        await runner.run_checked(args, target=target)
        return {"resized": target_pane}

    @mcp.tool()
    async def tmux_swap_pane(src: str, dst: str, target: str | None = None) -> dict:
        """Swap two panes."""
        await runner.run_checked(["swap-pane", "-s", src, "-t", dst], target=target)
        return {"swapped": True, "src": src, "dst": dst}

    @mcp.tool()
    async def tmux_kill_pane(target_pane: str, target: str | None = None) -> dict:
        """Kill a pane (destructive)."""
        await runner.run_checked(["kill-pane", "-t", target_pane], target=target)
        return {"killed": True, "pane": target_pane}

    @mcp.tool()
    async def tmux_clear_history(
        target_pane: str | None = None, target: str | None = None
    ) -> dict:
        """Wipe a pane's scrollback history (clear-history -t pane).

        Use this before a `tmux_send_keys` / `tmux_run` so a subsequent
        `tmux_capture_pane(start=...)` starts from a clean slate, without the
        previous output bleeding into the captured range. It clears the
        scrollback buffer only — the visible screen is untouched.

        Returns {"cleared": True, "pane": target_pane}.
        """
        args = ["clear-history"]
        if target_pane:
            args += ["-t", target_pane]
        await runner.run_checked(args, target=target)
        return {"cleared": True, "pane": target_pane}

    @mcp.tool()
    async def tmux_respawn_pane(
        target_pane: str,
        command: str | None = None,
        kill: bool = False,
        start_directory: str | None = None,
        env: dict[str, str] | None = None,
        target: str | None = None,
    ) -> dict:
        """Restart the command in a pane (respawn-pane), reusing it in place.

        Useful for retrying a crashed command or supervising a service without
        recreating the window layout. By default tmux only respawns a pane whose
        command has already exited; set kill=True (-k) to force-restart one that
        is still running. `command` is the shell command to run (defaults to the
        pane's original command); `start_directory` sets its cwd (-c). `env`
        (-e KEY=VAL, tmux 3.0+) injects environment variables; ignored with a
        note on older tmux.

        Returns {"respawned": True, "pane": target_pane}.
        """
        args = ["respawn-pane"]
        if kill:
            args.append("-k")
        if start_directory:
            args += ["-c", start_directory]
        notes = []
        if env:
            caps = await runner.capabilities(target)
            if caps.has("respawn_env"):
                for key, value in env.items():
                    args += ["-e", f"{key}={value}"]
            else:
                notes.append("env ignored: respawn -e requires tmux 3.0+")
        args += ["-t", target_pane]
        if command:
            args.append(command)
        await runner.run_checked(args, target=target)
        result = {"respawned": True, "pane": target_pane}
        if notes:
            result["notes"] = notes
        return result

    @mcp.tool()
    async def tmux_select_layout(
        layout: str, window: str | None = None, target: str | None = None
    ) -> dict:
        """Apply a named layout (even-horizontal, even-vertical, main-horizontal,
        main-vertical, tiled) or a layout string to a window."""
        args = ["select-layout"]
        if window:
            args += ["-t", window]
        args.append(layout)
        await runner.run_checked(args, target=target)
        return {"layout": layout}
