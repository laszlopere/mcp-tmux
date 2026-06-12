"""Client and server introspection tools."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..formats import FIELD_SEP
from ._params import Target, TargetPaneOpt
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_server_info(target: Target = None) -> dict:
        """Report basic server facts: pid, socket path, and tmux version.

        For just the version (and whether it is supported), `tmux_version` is the
        lighter call; `tmux_list_targets` enumerates configurable targets.

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

    @tool()
    async def tmux_display_message(
        message: Annotated[
            str,
            Field(description="Text to show; #{...} formats expand against target_pane."),
        ],
        target_client: Annotated[
            str | None,
            Field(description="Client to show the message on (-c)."),
        ] = None,
        target_pane: TargetPaneOpt = None,
        target: Target = None,
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
