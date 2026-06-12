"""Consolidated tools: one tool per signature-equivalence class.

tmux separates `kill-session` / `kill-window` / `kill-pane`, but those differ
only in *which entity kind* they act on while sharing the exact same argument
and return shape. An MCP client picks a tool by name+description, so a single
`tmux_kill(kind=...)` is just as discoverable as three — and cheaper to load.
Each tool here takes a `kind` discriminator (validated against an allow-list)
and returns a dict carrying that `kind` so callers can tell what acted.

See TODO P6 for the equivalence-class inventory and the merge rationale.
"""

from __future__ import annotations

from ..formats import CLIENT_FIELDS, FIELD_SEP, SESSION_FIELDS, WINDOW_FIELDS
from ._util import require_kind, toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_list(kind: str, scope: str | None = None, target: str | None = None) -> dict:
        """List sessions, windows, clients, or paste buffers.

        `kind` selects what to enumerate:
          - "session" — all sessions (`scope` ignored)
          - "window"  — windows in `scope` (a session); all windows server-wide
            (-a) when `scope` is omitted
          - "client"  — clients attached to `scope` (a session); all clients
            when omitted
          - "buffer"  — paste buffers (`scope` ignored)

        Panes are NOT here — use `tmux_list_panes`, which has two independent
        scope axes (a window vs a session). Key bindings use `tmux_list_keys`
        (its return is text, not a record list).

        Returns {"items": [...], "kind": kind}: the per-entity records under a
        uniform "items" key regardless of `kind`.
        """
        require_kind(kind, {"session", "window", "client", "buffer"})
        if kind == "session":
            items = await runner.list_records(["list-sessions"], SESSION_FIELDS, target=target)
        elif kind == "window":
            cmd = ["list-windows", "-t", scope] if scope else ["list-windows", "-a"]
            items = await runner.list_records(cmd, WINDOW_FIELDS, target=target)
        elif kind == "client":
            cmd = ["list-clients"]
            if scope:
                cmd += ["-t", scope]
            items = await runner.list_records(cmd, CLIENT_FIELDS, target=target)
        else:  # buffer
            out = await runner.run_checked(
                ["list-buffers", "-F", f"#{{buffer_name}}{FIELD_SEP}#{{buffer_size}}"],
                target=target,
            )
            items = []
            for line in out.splitlines():
                if not line:
                    continue
                name, _, size = line.partition(FIELD_SEP)
                items.append({"name": name, "size": size})
        return {"items": items, "kind": kind}

    @tool()
    async def tmux_kill(kind: str, id: str | None = None, target: str | None = None) -> dict:
        """Kill a session, window, pane, or the whole server (destructive).

        `kind` selects what to kill: "session" / "window" / "pane" / "server".
        `id` is the target entity (e.g. session name, "sess:2", "%3") and is
        required for every kind except "server" (which ends ALL sessions and
        takes no id). Maps to `kill-<kind> [-t id]`.

        Returns {"killed": True, "kind": kind, "id": id}.
        """
        require_kind(kind, {"session", "window", "pane", "server"})
        if kind == "server":
            await runner.run_checked(["kill-server"], target=target)
            return {"killed": True, "kind": kind, "id": None}
        if not id:
            raise ValueError(f"id is required for kind={kind!r}")
        await runner.run_checked([f"kill-{kind}", "-t", id], target=target)
        return {"killed": True, "kind": kind, "id": id}

    @tool()
    async def tmux_rename(kind: str, id: str, new_name: str, target: str | None = None) -> dict:
        """Rename a session or window.

        `kind` is "session" or "window"; `id` is the entity to rename. Maps to
        `rename-<kind> -t id new_name`.

        Returns {"renamed": True, "kind": kind, "name": new_name}.
        """
        require_kind(kind, {"session", "window"})
        await runner.run_checked([f"rename-{kind}", "-t", id, new_name], target=target)
        return {"renamed": True, "kind": kind, "name": new_name}

    @tool()
    async def tmux_select(kind: str, id: str, target: str | None = None) -> dict:
        """Make a window or pane the active one.

        `kind` is "window" or "pane"; `id` is the entity to activate (e.g.
        "mysess:2" for a window, "%3" for a pane). Maps to `select-<kind> -t id`.

        Returns {"selected": id, "kind": kind}.
        """
        require_kind(kind, {"window", "pane"})
        await runner.run_checked([f"select-{kind}", "-t", id], target=target)
        return {"selected": id, "kind": kind}

    @tool()
    async def tmux_last(kind: str, scope: str | None = None, target: str | None = None) -> dict:
        """Switch to the previously selected window or pane.

        `kind` is "window" (last-window) or "pane" (last-pane). `scope` is the
        optional `-t` context: a session for `kind="window"`, a window for
        `kind="pane"`; omit to act on the current one.

        Returns {"selected": "last", "kind": kind}.
        """
        require_kind(kind, {"window", "pane"})
        args = [f"last-{kind}"]
        if scope:
            args += ["-t", scope]
        await runner.run_checked(args, target=target)
        return {"selected": "last", "kind": kind}

    @tool()
    async def tmux_swap(kind: str, src: str, dst: str, target: str | None = None) -> dict:
        """Exchange the positions of two windows or two panes.

        Swapping trades the two entities' slots: each ends up where the other
        was. For windows that means their index positions within the session are
        exchanged; for panes, their positions within the window's layout. The
        contents (running programs, history) travel with each entity — only the
        location changes. Geometry is preserved, so swapping two panes of unequal
        size makes each take over the other's cell.

        `kind` — "window" or "pane".
        `src`, `dst` — the two entities to exchange, in tmux target syntax: for
          windows a session:index like "dev:1" (or a bare index for the current
          session); for panes a pane id like "%3" or a window:pane like "dev:1.2".
          The order of `src`/`dst` does not matter — the result is symmetric.
        `target` — optional host/profile to run against (omit for local).

        Maps to `swap-<kind> -s src -t dst`.

        When to use: reach for this to reorder existing entities relative to each
        other. To move one window to a different (possibly empty) index instead,
        use `tmux_move_window`; to rearrange all panes in a window at once, use a
        layout via `tmux_select_layout`.

        Returns {"swapped": True, "kind": kind, "src": src, "dst": dst}.
        """
        require_kind(kind, {"window", "pane"})
        await runner.run_checked([f"swap-{kind}", "-s", src, "-t", dst], target=target)
        return {"swapped": True, "kind": kind, "src": src, "dst": dst}

    @tool()
    async def tmux_respawn(
        kind: str,
        id: str,
        command: str | None = None,
        kill: bool = False,
        start_directory: str | None = None,
        env: dict[str, str] | None = None,
        target: str | None = None,
    ) -> dict:
        """Restart the command in a pane or window, reusing it in place.

        `kind` is "pane" (respawn-pane) or "window" (respawn-window); `id` is the
        entity to respawn. Useful for retrying a crashed command or supervising a
        service without recreating the layout. By default tmux only respawns an
        entity whose command has already exited; set kill=True (-k) to
        force-restart a live one. `command` defaults to the original command;
        `start_directory` sets its cwd (-c). `env` (-e KEY=VAL, tmux 3.0+) injects
        environment variables; ignored with a note on older tmux.

        Returns {"respawned": True, "kind": kind, "id": id} (plus "notes" if env
        was dropped).
        """
        require_kind(kind, {"pane", "window"})
        args = [f"respawn-{kind}"]
        if kill:
            args.append("-k")
        if start_directory:
            args += ["-c", start_directory]
        notes = []
        if env:
            caps = await runner.capabilities(target)
            if caps.has("respawn_env"):
                for key, value in env.items():
                    args += ["-e", f"{key}={value}"]
            else:
                notes.append("env ignored: respawn -e requires tmux 3.0+")
        args += ["-t", id]
        if command:
            args.append(command)
        await runner.run_checked(args, target=target)
        result = {"respawned": True, "kind": kind, "id": id}
        if notes:
            result["notes"] = notes
        return result
