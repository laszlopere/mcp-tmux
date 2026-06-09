"""Session/global environment tools (set-environment / show-environment).

Setting environment variables on the session (or globally with `-g`) before
launching commands is the race-free alternative to an `export` typed via
`send_keys`: variables set here are inherited by every command subsequently
spawned in the session's panes/windows.
"""

from __future__ import annotations

from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_set_environment(
        name: str,
        value: str | None = None,
        global_: bool = False,
        remove: bool = False,
        unset: bool = False,
        session: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Set or unset an environment variable for a session (or globally).

        Set `global_=True` (-g) to affect the global environment that new
        sessions inherit, instead of one session. With a `value`, sets the
        variable; commands spawned afterwards in that session's panes inherit it
        (the race-free alternative to an `export` typed via send_keys).

        To clear a variable, pass `remove=True` (-r, mark it for removal so the
        child sees it unset even if it exists globally) or `unset=True` (-u,
        delete it from this session's environment). `session` is the -t target.

        Returns {"set"/"removed"/"unset": name, ...}.
        """
        if remove and unset:
            raise ValueError("Provide at most one of 'remove' or 'unset'.")
        args = ["set-environment"]
        if global_:
            args.append("-g")
        if remove:
            args.append("-r")
        if unset:
            args.append("-u")
        if session:
            args += ["-t", session]
        args.append(name)
        if value is not None and not (remove or unset):
            args.append(value)
        await runner.run_checked(args, target=target)
        if unset:
            return {"unset": name, "global": global_}
        if remove:
            return {"removed": name, "global": global_}
        return {"set": name, "value": value, "global": global_}

    @tool()
    async def tmux_show_environment(
        name: str | None = None,
        global_: bool = False,
        session: str | None = None,
        target: str | None = None,
    ) -> dict:
        """Show a session's environment (or the global one with `global_=True`).

        With `name`, returns just that variable. Variables tmux has marked for
        removal (shown by tmux as `-NAME`) are reported as removed=True with a
        null value.

        Returns {"environment": {name: value_or_None}, "removed": [names...]}.
        """
        args = ["show-environment"]
        if global_:
            args.append("-g")
        if session:
            args += ["-t", session]
        if name:
            args.append(name)
        out = await runner.run_checked(args, target=target)
        environment: dict[str, str | None] = {}
        removed: list[str] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            if line.startswith("-"):
                # `-NAME` => marked for removal (unset for children).
                var = line[1:]
                environment[var] = None
                removed.append(var)
                continue
            key, sep, val = line.partition("=")
            # A bare line with no '=' is a variable present without a value.
            environment[key] = val if sep else None
        return {"environment": environment, "removed": removed}
