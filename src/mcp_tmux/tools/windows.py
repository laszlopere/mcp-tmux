"""Window tools."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..formats import FIELD_SEP, parse_records
from ._params import Target
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_new_window(
        session: Annotated[
            str | None,
            Field(description='Target session or "sess:index" (-t); current session if omitted.'),
        ] = None,
        name: Annotated[
            str | None,
            Field(description="Name for the new window (-n)."),
        ] = None,
        start_directory: Annotated[
            str | None,
            Field(description="Working directory for the new window (-c)."),
        ] = None,
        command: Annotated[
            str | None,
            Field(description="Command to run instead of the default shell."),
        ] = None,
        select: Annotated[
            bool,
            Field(description="Focus the new window (default); False backgrounds it (-d)."),
        ] = True,
        target: Target = None,
    ) -> dict:
        """Create a window. `session` is the target session (or "sess:index").

        Set select=False to create it in the background (-d).

        Use this to add a window to an existing session; for a whole new session
        use `tmux_new_session`, or `tmux_split_window` to add a pane to the
        current window instead.

        Returns the new window's {"id", "index", "name"}.
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

    @tool()
    async def tmux_next_layout(
        window: Annotated[
            str | None,
            Field(description="Window to rotate (-t); the current window if omitted."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Rotate a window to its next preset layout (next-layout).

        With `window` (-t), acts on that window; otherwise the current one.

        This just cycles to the next preset; to apply a specific layout by name,
        use `tmux_select_layout`.

        Returns {"window": window}.
        """
        args = ["next-layout"]
        if window:
            args += ["-t", window]
        await runner.run_checked(args, target=target)
        return {"window": window}

    @tool()
    async def tmux_move_window(
        src: Annotated[str, Field(description='Window to move (e.g. "sess:5").')],
        dst: Annotated[str, Field(description='Destination session:index (e.g. "sess:2").')],
        target: Target = None,
    ) -> dict:
        """Move/renumber a window from `src` to `dst` (e.g. "sess:5").

        Use this to relocate one window to a (possibly empty) index; to exchange
        two existing windows use `tmux_swap(kind="window")`, or `tmux_link_window`
        to make one window appear in two places at once.
        """
        await runner.run_checked(["move-window", "-s", src, "-t", dst], target=target)
        return {"moved": True, "src": src, "dst": dst}
