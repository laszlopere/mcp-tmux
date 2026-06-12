"""Hooks and scripting: set-hook, show-hooks, run-shell, if-shell.

Hooks fire a tmux command on an event (e.g. a pane dying); run-shell / if-shell
let you run shell commands and branch on their result from inside tmux. Hooks
require tmux 2.2+.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ._params import Target, TargetPaneOpt
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_set_hook(
        hook: Annotated[
            str,
            Field(description='Event name, e.g. "pane-died" or "session-created".'),
        ],
        command: Annotated[
            str | None,
            Field(description="tmux command to run on the event; required unless unset=True."),
        ] = None,
        global_: Annotated[
            bool,
            Field(description="Set the hook server-wide (-g)."),
        ] = False,
        unset: Annotated[
            bool,
            Field(description="Remove the hook instead of setting it (-u)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Set (or unset) a hook that runs a tmux command on an event.

        `hook` is the event name, e.g. "pane-died" or "session-created";
        `command` is the tmux command to run (a command string like
        'display "gone"'). global_=True (-g) sets it server-wide; unset=True
        (-u) removes the hook instead. Requires tmux 2.2+.

        Inspect what is currently set with `tmux_show_hooks`. For one-off
        conditional logic (not event-triggered) use `tmux_if_shell` instead.
        """
        args = ["set-hook"]
        if global_:
            args.append("-g")
        if unset:
            args.append("-u")
            args.append(hook)
        else:
            if command is None:
                raise ValueError("command is required unless unset=True")
            args += [hook, command]
        await runner.run_checked(args, target=target)
        return {"hook": hook, "unset": unset}

    @tool()
    async def tmux_show_hooks(
        global_: Annotated[
            bool,
            Field(description="Show server-wide hooks instead of a session's (-g)."),
        ] = False,
        target_entity: Annotated[
            str | None,
            Field(description="Session/window to read hooks from (-t)."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Show the hooks set on the server/session.

        The read counterpart to `tmux_set_hook`. Returns {"hooks": {name: command}}.
        """
        args = ["show-hooks"]
        if global_:
            args.append("-g")
        if target_entity:
            args += ["-t", target_entity]
        out = await runner.run_checked(args, target=target)
        hooks: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            key, _, val = line.partition(" ")
            hooks[key] = val.strip()
        return {"hooks": hooks}

    @tool()
    async def tmux_run_shell(
        command: Annotated[str, Field(description="Shell command to run via run-shell.")],
        background: Annotated[
            bool,
            Field(description="Run without waiting for completion (-b)."),
        ] = False,
        target_pane: TargetPaneOpt = None,
        target: Target = None,
    ) -> dict:
        """Run a shell command from tmux via `run-shell`.

        background=True (-b) runs it without waiting. Note: tmux surfaces the
        command's stdout in a message/copy buffer rather than returning it here,
        so `output` is usually empty — to capture a command's output prefer
        `tmux_run`. Returns {"output", "exit_code"}.
        """
        args = ["run-shell"]
        if background:
            args.append("-b")
        if target_pane:
            args += ["-t", target_pane]
        args.append(command)
        result = await runner.run(args, target=target)
        return {"output": result.stdout, "exit_code": result.exit_code}

    @tool()
    async def tmux_if_shell(
        condition: Annotated[
            str,
            Field(description="Shell command (or tmux format if is_format) tested for success."),
        ],
        if_command: Annotated[
            str,
            Field(description="tmux command to run when the condition succeeds."),
        ],
        else_command: Annotated[
            str | None,
            Field(description="tmux command to run when the condition fails."),
        ] = None,
        background: Annotated[
            bool,
            Field(description="Run in the background (-b)."),
        ] = False,
        is_format: Annotated[
            bool,
            Field(description='Treat condition as a tmux format, true unless empty or "0" (-F).'),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Run a tmux command conditionally with `if-shell`.

        Runs `condition` as a shell command; if it succeeds, runs the tmux
        command `if_command`, otherwise `else_command` (if given). is_format=True
        (-F) treats `condition` as a tmux format that is true unless it evaluates
        to empty or "0" (no shell). background=True (-b) runs in the background.

        For an unconditional shell command use `tmux_run_shell`; to run a command
        and capture its output + exit code use `tmux_run`. To fire a command on a
        tmux event rather than on demand, use `tmux_set_hook`.
        """
        args = ["if-shell"]
        if background:
            args.append("-b")
        if is_format:
            args.append("-F")
        args += [condition, if_command]
        if else_command is not None:
            args.append(else_command)
        await runner.run_checked(args, target=target)
        return {"evaluated": True}
