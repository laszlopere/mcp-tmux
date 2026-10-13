"""Passthrough and global tools.

``tmux_command`` is the comprehensiveness guarantee: whatever the installed tmux
supports, it supports. The other tools are conveniences around server-wide
introspection.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ..targets import list_target_names
from ._params import Target, TargetPaneOpt
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_command(
        args: Annotated[
            list[str],
            Field(description='tmux subcommand + flags as a list (no leading "tmux").'),
        ],
        target: Target = None,
        timeout: Annotated[
            float | None,
            Field(description="Max seconds to wait for the command."),
        ] = None,
    ) -> dict:
        """Run an arbitrary tmux command and return its raw result.

        This is the universal escape hatch — it runs `tmux <args>` verbatim, so
        it can do anything the target's tmux supports. Pass the subcommand and
        its flags as a list, e.g. args=["new-window", "-t", "mysess", "-n",
        "logs"]. Do NOT include the leading "tmux". As on the command line, an
        argument ";" (or one ending in ";") separates commands, so several run
        in one call; end an argument with "\\;" for a literal trailing ";".

        Returns {"stdout", "stderr", "exit_code"}. Non-zero exit codes are
        returned, not raised, so you can inspect failures.

        `target` selects where tmux runs: omit (or "local") for the local
        machine, a named profile from the config file, or an ad-hoc ssh
        destination like "user@host".
        """
        result = await runner.run(args, target=target, timeout=timeout, verbatim=True)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    @tool()
    async def tmux_query(
        format: Annotated[
            str,
            Field(description='tmux format string to expand, e.g. "#{pane_current_path}".'),
        ],
        target_pane: TargetPaneOpt = None,
        target: Target = None,
    ) -> dict:
        """Evaluate a tmux format string (e.g. "#{pane_current_path}").

        Uses `display-message -p` to expand any #{...} format variable. Optional
        `target_pane` sets the context (-t) the format is evaluated against.

        This returns the expanded value to you; to instead show a message on a
        client's status line, use `tmux_display_message`.

        Returns {"value": <expanded string>}.
        """
        args = ["display-message", "-p"]
        if target_pane:
            args += ["-t", target_pane]
        args.append(format)
        out = await runner.run_checked(args, target=target)
        return {"value": out.rstrip("\n")}

    @tool()
    async def tmux_version(target: Target = None) -> dict:
        """Report the target's tmux version and whether it is supported (1.8+).

        For more server facts (pid, socket path) use `tmux_server_info`.
        """
        caps = await runner.capabilities(target)
        return {
            "raw": caps.raw,
            "version": caps.version_str,
            "supported": caps.supported,
        }

    @tool()
    async def tmux_transport(
        transport: Annotated[
            Literal["oneshot", "control"] | None,
            Field(description="Switch to this transport; omit to only report the current one."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Report or switch how commands reach a target's tmux.

        ASK THE USER BEFORE SWITCHING, and say why; never change the transport
        on your own initiative. Reporting (no `transport`) is always fine.

        * "oneshot" (the default): one `tmux` process per command, plus an SSH
          session for a remote target. Roughly 2 ms per command locally and
          about 20 ms to a LAN host over SSH. Nothing stays attached.
        * "control": one persistent control-mode client (`tmux -C`, tmux 3.2+)
          carries the commands. Roughly 0.1 ms per command locally and about
          1 ms to a LAN host: 20x faster over SSH. Worth it for long scripted
          sequences or many reads, not for a handful of calls. Drawbacks: the
          client is attached to a session, which then shows as attached (also
          to the user's own tmux), becomes the "most recent" session that a
          plain `tmux attach` or a command without -t picks, and is the default
          for commands sent without -t. `tmux_list(kind="client")` shows it.
          The wait tools attach to the waited-on pane's session while they wait,
          to be woken by its output instead of polling. Blocking or
          client-level commands (wait-for, run-shell, attach/detach/switch,
          kill-server, new-session...) still run one-shot, as does everything
          while the target has no tmux session.

        The switch lasts until this MCP server restarts; to make it permanent
        the user sets `transport` in the config file (`[defaults]` or a
        `[targets.<name>]` table).

        Returns {"target", "transport", "configured", "control_supported",
        "connected"}.
        """
        if transport is not None:
            await runner.set_transport(target, transport)
        caps = await runner.capabilities(target)
        return {
            "target": runner.resolve(target).name,
            "transport": runner.transport(target),
            "configured": runner.configured_transport(target),
            "control_supported": caps.has("control_transport"),
            "connected": runner.control_connected(target),
        }

    @tool()
    async def tmux_list_targets() -> dict:
        """List configured targets: 'local' plus any named profiles from config.

        Pass one of these names as the `target` argument of any other tool to run
        it against that host.
        """
        return {"targets": list_target_names(runner.config)}
