"""Window/pane plumbing: link, break, join, find, and pipe.

These are the less-common structural operations — moving a window into another
session, splitting a pane out into its own window (and back), searching for a
window by name, and streaming a pane's output to a command.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..formats import FIELD_SEP, WINDOW_FIELDS, parse_records
from ._params import Target, TargetPane
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_link_window(
        src: Annotated[str, Field(description='Source window (e.g. "sess1:2").')],
        dst: Annotated[
            str,
            Field(description='Destination, e.g. "sess2:" for next free index or "sess2:5".'),
        ],
        select: Annotated[
            bool,
            Field(description="Focus the linked window (default); False links in background (-d)."),
        ] = True,
        target: Target = None,
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

    @tool()
    async def tmux_unlink_window(
        window: Annotated[str, Field(description='Window to unlink (e.g. "sess:2").')],
        target: Target = None,
    ) -> dict:
        """Unlink a window (remove one of its links).

        Fails if the window is only linked once, unless it is not the last —
        tmux refuses to leave a window with no links. Use
        `tmux_kill(kind="window")` to destroy it outright.
        """
        await runner.run_checked(["unlink-window", "-t", window], target=target)
        return {"unlinked": True, "window": window}

    @tool()
    async def tmux_break_pane(
        target_pane: TargetPane,
        window_name: Annotated[
            str | None,
            Field(description="Name for the new window (-n)."),
        ] = None,
        select: Annotated[
            bool,
            Field(description="Focus the new window (default); False backgrounds it (-d)."),
        ] = True,
        target: Target = None,
    ) -> dict:
        """Break a pane out into a new window of its own.

        `window_name` names the new window; select=False creates it in the
        background (-d).

        The inverse of `tmux_join_pane` (which folds a pane back into another
        window's layout).

        Returns the new window's {"id", "index", "name"}.
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

    @tool()
    async def tmux_join_pane(
        src: Annotated[str, Field(description='Pane to move (e.g. "%3").')],
        dst: Annotated[str, Field(description="Pane/window whose window receives the split.")],
        vertical: Annotated[
            bool,
            Field(description="Stack top/bottom instead of the default left/right."),
        ] = False,
        percentage: Annotated[
            int | None,
            Field(description="Size of the joined pane as a percentage (e.g. 30)."),
        ] = None,
        select: Annotated[
            bool,
            Field(description="Focus the joined pane (default); False keeps the original (-d)."),
        ] = True,
        target: Target = None,
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

    @tool()
    async def tmux_find_window(
        pattern: Annotated[
            str,
            Field(description="Case-insensitive substring matched against window name/title."),
        ],
        session: Annotated[
            str | None,
            Field(description="Limit the search to this session; default searches all."),
        ] = None,
        target: Target = None,
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
            if needle in str(r.get("name", "")).lower() or needle in str(r.get("title", "")).lower()
        ]
        return {"matches": matches}

    @tool()
    async def tmux_pipe_pane(
        target_pane: TargetPane,
        command: Annotated[
            str | None,
            Field(description="Shell command fed the pane's output; omit to stop piping."),
        ] = None,
        only_new: Annotated[
            bool,
            Field(description="Toggle: close an open pipe, otherwise open one (-o)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Pipe a pane's output to a shell command (great for logging).

        With `command`, tmux feeds everything the pane prints to that command's
        stdin, e.g. command="cat >> /tmp/pane.log". only_new=True (-o) toggles:
        if a pipe is already open it is closed, otherwise opened. Call with no
        `command` to stop piping.

        Best for continuously logging a pane to a file/command. To follow output
        live in-process use `tmux_stream_read`; for a one-shot snapshot use
        `tmux_capture_pane`.

        Returns {"piping": bool}.
        """
        args = ["pipe-pane"]
        if only_new:
            args.append("-o")
        args += ["-t", target_pane]
        if command:
            args.append(command)
        await runner.run_checked(args, target=target)
        return {"piping": bool(command), "pane": target_pane}
