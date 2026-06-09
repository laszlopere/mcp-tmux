"""Window tools."""

from __future__ import annotations

from ..formats import FIELD_SEP, parse_records


def register(mcp, runner) -> None:
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
    async def tmux_next_layout(window: str | None = None, target: str | None = None) -> dict:
        """Rotate a window to its next preset layout (next-layout).

        With `window` (-t), acts on that window; otherwise the current one.
        Returns {"window": window}.
        """
        args = ["next-layout"]
        if window:
            args += ["-t", window]
        await runner.run_checked(args, target=target)
        return {"window": window}

    @mcp.tool()
    async def tmux_move_window(src: str, dst: str, target: str | None = None) -> dict:
        """Move/renumber a window from `src` to `dst` (e.g. "sess:5")."""
        await runner.run_checked(["move-window", "-s", src, "-t", dst], target=target)
        return {"moved": True, "src": src, "dst": dst}
