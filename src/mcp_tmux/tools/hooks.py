"""Hooks and scripting: set-hook, show-hooks, run-shell, if-shell.

Hooks fire a tmux command on an event (e.g. a pane dying); run-shell / if-shell
let you run shell commands and branch on their result from inside tmux. Hooks
require tmux 2.2+.
"""

from __future__ import annotations


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_set_hook(
        hook: str,
        command: str | None = None,
        global_: bool = False,
        unset: bool = False,
        target: str | None = None,
    ) -> dict:
        """Set (or unset) a hook that runs a tmux command on an event.

        `hook` is the event name, e.g. "pane-died" or "session-created";
        `command` is the tmux command to run (a command string like
        'display "gone"'). global_=True (-g) sets it server-wide; unset=True
        (-u) removes the hook instead. Requires tmux 2.2+.
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

    @mcp.tool()
    async def tmux_show_hooks(
        global_: bool = False,
        target_entity: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Show the hooks set on the server/session. Returns {"hooks": {name: command}}."""
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

    @mcp.tool()
    async def tmux_run_shell(
        command: str,
        background: bool = False,
        target_pane: str | None = None,
        target: str | None = None,
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

    @mcp.tool()
    async def tmux_if_shell(
        condition: str,
        if_command: str,
        else_command: str | None = None,
        background: bool = False,
        is_format: bool = False,
        target: str | None = None,
    ) -> dict:
        """Run a tmux command conditionally with `if-shell`.

        Runs `condition` as a shell command; if it succeeds, runs the tmux
        command `if_command`, otherwise `else_command` (if given). is_format=True
        (-F) treats `condition` as a tmux format that is true unless it evaluates
        to empty or "0" (no shell). background=True (-b) runs in the background.
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
