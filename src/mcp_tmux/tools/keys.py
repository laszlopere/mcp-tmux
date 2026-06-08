"""Key bindings: list-keys, bind-key, unbind-key.

The key-table flag spelling differs by version: tmux 2.1+ uses ``-T <table>``,
older tmux uses ``-t <table>``. We pick the right one from detected
capabilities.
"""

from __future__ import annotations


async def _table_flag(runner, table: str | None, target: str | None) -> list[str]:
    if not table:
        return []
    caps = await runner.capabilities(target)
    return (["-T", table] if caps.has("key_tables") else ["-t", table])


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_list_keys(
        table: str | None = None, target: str | None = None
    ) -> dict:
        """List key bindings, optionally restricted to one key `table` (e.g.
        "prefix", "root", "copy-mode"). Returns the raw bindings as
        {"keys": <text>, "lines": [...]} — one `bind-key ...` line each."""
        args = ["list-keys", *await _table_flag(runner, table, target)]
        out = await runner.run_checked(args, target=target)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return {"keys": out, "lines": lines}

    @mcp.tool()
    async def tmux_bind_key(
        key: str,
        command: list[str],
        table: str | None = None,
        repeat: bool = False,
        root: bool = False,
        target: str | None = None,
    ) -> dict:
        """Bind `key` to a tmux command.

        `command` is the tmux command + args as a list, e.g.
        ["new-window", "-n", "logs"]. `table` selects the key table; root=True
        (-n) binds in the root table so no prefix is needed; repeat=True (-r)
        allows the key to repeat. Returns {"bound": key}.
        """
        if not command:
            raise ValueError("command must be a non-empty list of tmux command args")
        args = ["bind-key"]
        if repeat:
            args.append("-r")
        if root:
            args.append("-n")
        args += await _table_flag(runner, table, target)
        args += [key, *command]
        await runner.run_checked(args, target=target)
        return {"bound": key}

    @mcp.tool()
    async def tmux_unbind_key(
        key: str | None = None,
        table: str | None = None,
        root: bool = False,
        all: bool = False,
        target: str | None = None,
    ) -> dict:
        """Unbind a key. Provide `key`, or all=True (-a) to clear every binding
        (in `table` if given). root=True (-n) targets the root table. Returns
        {"unbound": ...}."""
        args = ["unbind-key"]
        if all:
            args.append("-a")
        if root:
            args.append("-n")
        args += await _table_flag(runner, table, target)
        if not all:
            if not key:
                raise ValueError("provide a key, or set all=True")
            args.append(key)
        await runner.run_checked(args, target=target)
        return {"unbound": "all" if all else key}
