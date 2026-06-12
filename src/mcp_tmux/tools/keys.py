"""Key bindings: list-keys, bind-key, unbind-key.

The key-table flag spelling differs by version: tmux 2.1+ uses ``-T <table>``,
older tmux uses ``-t <table>``. We pick the right one from detected
capabilities.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ._params import Target
from ._util import toolset_gate


async def _table_flag(runner, table: str | None, target: str | None) -> list[str]:
    if not table:
        return []
    caps = await runner.capabilities(target)
    return ["-T", table] if caps.has("key_tables") else ["-t", table]


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_list_keys(
        table: Annotated[
            str | None,
            Field(description='Restrict to one key table, e.g. "prefix", "root", "copy-mode".'),
        ] = None,
        target: Target = None,
    ) -> dict:
        """List key bindings, optionally restricted to one key `table` (e.g.
        "prefix", "root", "copy-mode"). Returns the raw bindings as
        {"keys": <text>, "lines": [...]} — one `bind-key ...` line each.

        The read counterpart to `tmux_bind_key` / `tmux_unbind_key`."""
        args = ["list-keys", *await _table_flag(runner, table, target)]
        out = await runner.run_checked(args, target=target)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return {"keys": out, "lines": lines}

    @tool()
    async def tmux_bind_key(
        key: Annotated[str, Field(description='Key to bind, e.g. "C-a" or "F2".')],
        command: Annotated[
            list[str],
            Field(description='tmux command + args as a list, e.g. ["new-window", "-n", "logs"].'),
        ],
        table: Annotated[
            str | None,
            Field(description="Key table to bind in."),
        ] = None,
        repeat: Annotated[
            bool,
            Field(description="Allow the key to repeat while held (-r)."),
        ] = False,
        root: Annotated[
            bool,
            Field(description="Bind in the root table so no prefix is needed (-n)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Bind `key` to a tmux command.

        `command` is the tmux command + args as a list, e.g.
        ["new-window", "-n", "logs"]. `table` selects the key table; root=True
        (-n) binds in the root table so no prefix is needed; repeat=True (-r)
        allows the key to repeat.

        Inspect existing bindings with `tmux_list_keys`; remove one with
        `tmux_unbind_key`.

        Returns {"bound": key}.
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

    @tool()
    async def tmux_unbind_key(
        key: Annotated[
            str | None,
            Field(description="Key to unbind; required unless all=True."),
        ] = None,
        table: Annotated[
            str | None,
            Field(description="Key table to unbind from."),
        ] = None,
        root: Annotated[
            bool,
            Field(description="Target the root table (-n)."),
        ] = False,
        all: Annotated[
            bool,
            Field(description="Clear every binding (in table if given) (-a)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Unbind a key. Provide `key`, or all=True (-a) to clear every binding
        (in `table` if given). root=True (-n) targets the root table.

        The inverse of `tmux_bind_key`; see current bindings with
        `tmux_list_keys`. Returns {"unbound": ...}."""
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
