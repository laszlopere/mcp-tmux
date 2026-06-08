"""Passthrough and global tools.

``tmux_command`` is the comprehensiveness guarantee: whatever the installed tmux
supports, it supports. The other tools are conveniences around server-wide
introspection.
"""

from __future__ import annotations

from ..targets import list_target_names


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_command(
        args: list[str],
        target: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Run an arbitrary tmux command and return its raw result.

        This is the universal escape hatch — it runs `tmux <args>` verbatim, so
        it can do anything the target's tmux supports. Pass the subcommand and
        its flags as a list, e.g. args=["new-window", "-t", "mysess", "-n",
        "logs"]. Do NOT include the leading "tmux".

        Returns {"stdout", "stderr", "exit_code"}. Non-zero exit codes are
        returned, not raised, so you can inspect failures.

        `target` selects where tmux runs: omit (or "local") for the local
        machine, a named profile from the config file, or an ad-hoc ssh
        destination like "user@host".
        """
        result = await runner.run(args, target=target, timeout=timeout)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    @mcp.tool()
    async def tmux_query(
        format: str,
        target_pane: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Evaluate a tmux format string (e.g. "#{pane_current_path}").

        Uses `display-message -p` to expand any #{...} format variable. Optional
        `target_pane` sets the context (-t) the format is evaluated against.
        Returns {"value": <expanded string>}.
        """
        args = ["display-message", "-p"]
        if target_pane:
            args += ["-t", target_pane]
        args.append(format)
        out = await runner.run_checked(args, target=target)
        return {"value": out.rstrip("\n")}

    @mcp.tool()
    async def tmux_version(target: str | None = None) -> dict:
        """Report the target's tmux version and whether it is supported (1.8+)."""
        caps = await runner.capabilities(target)
        return {
            "raw": caps.raw,
            "version": caps.version_str,
            "supported": caps.supported,
        }

    @mcp.tool()
    async def tmux_list_targets() -> dict:
        """List configured targets: 'local' plus any named profiles from config."""
        return {"targets": list_target_names(runner.config)}

    @mcp.tool()
    async def tmux_kill_server(target: str | None = None) -> dict:
        """Kill the tmux server on the target (destructive: ends ALL sessions)."""
        await runner.run_checked(["kill-server"], target=target)
        return {"killed": True}
