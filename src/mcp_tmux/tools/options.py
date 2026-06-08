"""Options and paste-buffer tools."""

from __future__ import annotations

from ..formats import FIELD_SEP


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_set_option(
        name: str,
        value: str,
        scope: str = "session",
        target_entity: str | None = None,
        target: str | None = None,
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

    @mcp.tool()
    async def tmux_show_options(
        scope: str = "session",
        target_entity: str | None = None,
        global_: bool = False,
        target: str | None = None,
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

    @mcp.tool()
    async def tmux_list_buffers(target: str | None = None) -> dict:
        """List paste buffers with their names and sizes."""
        out = await runner.run_checked(
            ["list-buffers", "-F", f"#{{buffer_name}}{FIELD_SEP}#{{buffer_size}}"],
            target=target,
        )
        buffers = []
        for line in out.splitlines():
            if not line:
                continue
            name, _, size = line.partition(FIELD_SEP)
            buffers.append({"name": name, "size": size})
        return {"buffers": buffers}

    @mcp.tool()
    async def tmux_set_buffer(
        data: str, name: str | None = None, target: str | None = None
    ) -> dict:
        """Set a paste buffer's contents (optionally named with -b)."""
        args = ["set-buffer"]
        if name:
            args += ["-b", name]
        args.append(data)
        await runner.run_checked(args, target=target)
        return {"set": True, "name": name}

    @mcp.tool()
    async def tmux_paste_buffer(
        target_pane: str,
        name: str | None = None,
        delete: bool = False,
        target: str | None = None,
    ) -> dict:
        """Paste a buffer into a pane. delete=True removes the buffer after (-d)."""
        args = ["paste-buffer", "-t", target_pane]
        if name:
            args += ["-b", name]
        if delete:
            args.append("-d")
        await runner.run_checked(args, target=target)
        return {"pasted": True, "pane": target_pane}

    @mcp.tool()
    async def tmux_delete_buffer(name: str, target: str | None = None) -> dict:
        """Delete a named paste buffer."""
        await runner.run_checked(["delete-buffer", "-b", name], target=target)
        return {"deleted": name}

    @mcp.tool()
    async def tmux_save_buffer(
        path: str,
        name: str | None = None,
        append: bool = False,
        target: str | None = None,
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

    @mcp.tool()
    async def tmux_load_buffer(
        path: str,
        name: str | None = None,
        target: str | None = None,
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
