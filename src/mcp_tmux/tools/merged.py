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

from typing import Annotated

from pydantic import Field

from ..formats import CLIENT_FIELDS, FIELD_SEP, SESSION_FIELDS, WINDOW_FIELDS
from ._params import Target
from ._util import require_kind, toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_list(
        kind: Annotated[
            str,
            Field(description='What to enumerate: "session", "window", "client", or "buffer".'),
        ],
        scope: Annotated[
            str | None,
            Field(
                description=(
                    "Context for the listing: a session for kind=\"window\"/\"client\" "
                    "(omit for server-wide); ignored for \"session\"/\"buffer\"."
                )
            ),
        ] = None,
        target: Target = None,
    ) -> dict:
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
    async def tmux_kill(
        kind: Annotated[
            str,
            Field(description='What to kill: "session", "window", "pane", or "server".'),
        ],
        id: Annotated[
            str | None,
            Field(
                description=(
                    'The entity to kill (e.g. session "work", window "sess:2", pane '
                    '"%3"); required for every kind except "server".'
                )
            ),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Kill a session, window, pane, or the whole server (destructive).

        Irreversible and immediate: tmux does not prompt for confirmation, and
        any program running in the killed entity is terminated (SIGHUP) along
        with its unsaved state and scrollback. Killing the last pane in a window
        closes the window; killing the last window in a session ends the session.

        `kind` selects what to kill: "session" / "window" / "pane" / "server".
        `id` is the target entity (e.g. session name "work", a window "sess:2",
        a pane "%3" or "sess:2.1") and is required for every kind except
        "server" — which ends ALL sessions on the host and takes no id.
        `target` — optional host/profile to run against (omit for local).

        Maps to `kill-<kind> [-t id]`.

        To restart a crashed command in place without destroying the pane/window
        and its layout, prefer `tmux_respawn`.

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
    async def tmux_rename(
        kind: Annotated[str, Field(description='What to rename: "session" or "window".')],
        id: Annotated[
            str,
            Field(description='The entity to rename (e.g. "work" or "work:2").'),
        ],
        new_name: Annotated[str, Field(description="The new name to assign.")],
        target: Target = None,
    ) -> dict:
        """Rename a session or window.

        `kind` is "session" or "window"; `id` is the entity to rename. Maps to
        `rename-<kind> -t id new_name`.

        Returns {"renamed": True, "kind": kind, "name": new_name}.
        """
        require_kind(kind, {"session", "window"})
        await runner.run_checked([f"rename-{kind}", "-t", id, new_name], target=target)
        return {"renamed": True, "kind": kind, "name": new_name}

    @tool()
    async def tmux_select(
        kind: Annotated[str, Field(description='What to activate: "window" or "pane".')],
        id: Annotated[
            str,
            Field(description='The entity to activate (e.g. "mysess:2" or pane "%3").'),
        ],
        target: Target = None,
    ) -> dict:
        """Make a window or pane the active (focused) one.

        Moves the selection so that subsequent interactive input and any attached
        client's view go to this entity; selecting a pane also selects its window.
        This only changes focus — it does not alter contents or layout, and it is
        not required before targeting an entity by id elsewhere (e.g.
        `tmux_send_keys` and `tmux_capture_pane` address a pane directly,
        regardless of which one is active).

        `kind` is "window" or "pane"; `id` is the entity to activate (e.g.
        "mysess:2" for a window, "%3" or "mysess:2.1" for a pane).
        `target` — optional host/profile to run against (omit for local).

        Maps to `select-<kind> -t id`. To jump back to the previously selected
        one, use `tmux_last`.

        Returns {"selected": id, "kind": kind}.
        """
        require_kind(kind, {"window", "pane"})
        await runner.run_checked([f"select-{kind}", "-t", id], target=target)
        return {"selected": id, "kind": kind}

    @tool()
    async def tmux_last(
        kind: Annotated[
            str,
            Field(description='Which previously selected entity to return to: "window" or "pane".'),
        ],
        scope: Annotated[
            str | None,
            Field(
                description=(
                    'The -t context: a session for kind="window", a window for '
                    'kind="pane"; omit to act on the current one.'
                )
            ),
        ] = None,
        target: Target = None,
    ) -> dict:
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
    async def tmux_swap(
        kind: Annotated[str, Field(description='What to exchange: "window" or "pane".')],
        src: Annotated[
            str,
            Field(
                description='One of the two entities to exchange (e.g. "dev:1" or "%3").'
            ),
        ],
        dst: Annotated[
            str,
            Field(
                description="The other entity to exchange; order relative to src does not matter."
            ),
        ],
        target: Target = None,
    ) -> dict:
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
        kind: Annotated[str, Field(description='What to respawn: "pane" or "window".')],
        id: Annotated[
            str,
            Field(description='The entity to respawn (e.g. pane "%3" or window "dev:1").'),
        ],
        command: Annotated[
            str | None,
            Field(description="Command to run; defaults to the entity's original command."),
        ] = None,
        kill: Annotated[
            bool,
            Field(description="Force-restart even if the command is still running (-k)."),
        ] = False,
        start_directory: Annotated[
            str | None,
            Field(description="Working directory for the respawned command (-c)."),
        ] = None,
        env: Annotated[
            dict[str, str] | None,
            Field(description="Environment variables to inject (-e KEY=VAL; requires tmux 3.0+)."),
        ] = None,
        target: Target = None,
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
