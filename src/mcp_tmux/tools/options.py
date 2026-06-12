"""Options and paste-buffer tools."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ._params import Target, TargetPane
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_set_option(
        name: Annotated[str, Field(description="Option name to set.")],
        value: Annotated[str, Field(description="Value to assign to the option.")],
        scope: Annotated[
            str,
            Field(description='Option scope: "server", "session" (default), "window", or "pane".'),
        ] = "session",
        target_entity: Annotated[
            str | None,
            Field(description="The -t target for session/window/pane scope."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Set a tmux option.

        `scope` is one of "server" (-s), "session" (default), "window" (-w),
        or "pane" (-p, requires newer tmux). `target_entity` is the -t target
        for session/window/pane scope.
        """
        args = ["set-option"]
        flag = {"server": "-s", "session": None, "window": "-w", "pane": "-p"}.get(scope)
        if scope not in ("server", "session", "window", "pane"):
            raise ValueError("scope must be server, session, window, or pane")
        if flag:
            args.append(flag)
        if target_entity:
            args += ["-t", target_entity]
        args += [name, value]
        await runner.run_checked(args, target=target)
        return {"set": name, "value": value, "scope": scope}

    @tool()
    async def tmux_show_options(
        scope: Annotated[
            str,
            Field(description='Option scope: "server", "session" (default), "window", or "pane".'),
        ] = "session",
        target_entity: Annotated[
            str | None,
            Field(description="The -t target for session/window/pane scope."),
        ] = None,
        global_: Annotated[
            bool,
            Field(description="Show global options (-g)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Show tmux options for a scope. Returns {"options": {name: value}}."""
        args = ["show-options"]
        flag = {"server": "-s", "session": None, "window": "-w", "pane": "-p"}.get(scope)
        if scope not in ("server", "session", "window", "pane"):
            raise ValueError("scope must be server, session, window, or pane")
        if flag:
            args.append(flag)
        if global_:
            args.append("-g")
        if target_entity:
            args += ["-t", target_entity]
        out = await runner.run_checked(args, target=target)
        options: dict[str, str] = {}
        for line in out.splitlines():
            if not line.strip():
                continue
            key, _, val = line.partition(" ")
            options[key] = val.strip().strip('"')
        return {"options": options}

    @tool()
    async def tmux_set_buffer(
        data: Annotated[str, Field(description="Contents to store in the paste buffer.")],
        name: Annotated[
            str | None,
            Field(description="Name the buffer (-b); default is an automatic name."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Set a paste buffer's contents (optionally named with -b)."""
        args = ["set-buffer"]
        if name:
            args += ["-b", name]
        args.append(data)
        await runner.run_checked(args, target=target)
        return {"set": True, "name": name}

    @tool()
    async def tmux_paste_buffer(
        target_pane: TargetPane,
        name: Annotated[
            str | None,
            Field(description="Buffer to paste (-b); default is the most recent."),
        ] = None,
        delete: Annotated[
            bool,
            Field(description="Delete the buffer after pasting (-d)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Paste a buffer into a pane. delete=True removes the buffer after (-d)."""
        args = ["paste-buffer", "-t", target_pane]
        if name:
            args += ["-b", name]
        if delete:
            args.append("-d")
        await runner.run_checked(args, target=target)
        return {"pasted": True, "pane": target_pane}

    @tool()
    async def tmux_delete_buffer(
        name: Annotated[str, Field(description="Name of the paste buffer to delete.")],
        target: Target = None,
    ) -> dict:
        """Delete a named paste buffer."""
        await runner.run_checked(["delete-buffer", "-b", name], target=target)
        return {"deleted": name}

    @tool()
    async def tmux_save_buffer(
        path: Annotated[
            str,
            Field(description="Destination file path, resolved on the target host."),
        ],
        name: Annotated[
            str | None,
            Field(description="Buffer to save (-b); default is the most recent."),
        ] = None,
        append: Annotated[
            bool,
            Field(description="Append to the file instead of overwriting (-a)."),
        ] = False,
        target: Target = None,
    ) -> dict:
        """Write a paste buffer to a file (save-buffer).

        `name` selects a named buffer (-b); the default is the most recent one.
        `append=True` appends to the file instead of overwriting (-a). IMPORTANT:
        `path` is resolved on the **target** — for an SSH target it is a file on
        the remote host, not the local machine.

        Returns {"saved": path, "name": name}.
        """
        args = ["save-buffer"]
        if append:
            args.append("-a")
        if name:
            args += ["-b", name]
        args.append(path)
        await runner.run_checked(args, target=target)
        return {"saved": path, "name": name, "appended": append}

    @tool()
    async def tmux_load_buffer(
        path: Annotated[
            str,
            Field(description="Source file path, resolved on the target host."),
        ],
        name: Annotated[
            str | None,
            Field(description="Store under this buffer name (-b)."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Load a file's contents into a paste buffer (load-buffer).

        `name` stores it under a named buffer (-b). IMPORTANT: `path` is resolved
        on the **target** — for an SSH target it is a file on the remote host,
        not the local machine.

        Returns {"loaded": path, "name": name}.
        """
        args = ["load-buffer"]
        if name:
            args += ["-b", name]
        args.append(path)
        await runner.run_checked(args, target=target)
        return {"loaded": path, "name": name}
