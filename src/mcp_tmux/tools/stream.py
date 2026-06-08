"""Streaming tools built on tmux control mode (`tmux -C`).

Opt-in: open a persistent control-mode connection to a session, then long-poll
its event stream — chiefly ``%output`` (pane wrote data) — so an agent can watch
a pane live instead of polling ``tmux_capture_pane``. One connection is shared
per (target, session); ``tmux_stream_start`` is idempotent.

For one-shot reads, the CLI tools remain the right default; reach for these when
you need to follow output as it happens (a build, a tail, a long-running job).
"""

from __future__ import annotations

from ..control import ControlManager


def register(mcp, runner) -> None:
    manager = ControlManager(runner)

    @mcp.tool()
    async def tmux_stream_start(
        session: str,
        target: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> dict:
        """Open (or reuse) a control-mode stream attached to `session`.

        Starts a persistent `tmux -C attach -t <session>` connection that
        captures pane output and window/layout events. Idempotent per
        (target, session). Returns {"stream_id", "session", "target", "alive"};
        pass the stream_id to `tmux_stream_read`/`_send`/`_stop`.

        Pass `width`/`height` (both, tmux 2.4+) to set the control client's size
        via `refresh-client -C WxH` right after attach — otherwise it defaults to
        80x24 and wraps `%output` for wider panes oddly. The size is re-applied
        automatically if the connection drops and reconnects; `tmux_stream_resize`
        changes it later. The connection also auto-reconnects on an unexpected
        drop (e.g. a flaky SSH link), surfacing `reconnected`/`disconnected`
        events in the stream.
        """
        conn = await manager.start(session, target=target, width=width, height=height)
        return {
            "stream_id": conn.stream_id,
            "session": conn.session,
            "target": conn.target.name,
            "alive": conn.alive,
        }

    @mcp.tool()
    async def tmux_stream_resize(stream_id: str, width: int, height: int) -> dict:
        """Set a stream's control-client size (refresh-client -C WxH, tmux 2.4+).

        Use this when pane output wraps at the wrong width — a control client
        defaults to 80x24. The size sticks across auto-reconnects. Returns
        {"stream_id", "width", "height"}.
        """
        conn = manager.get(stream_id)
        caps = await runner.capabilities(conn.target.name)
        if not caps.has("refresh_client_size"):
            raise ValueError(f"target tmux {caps.version_str} lacks refresh-client -C (needs 2.4+)")
        await conn.refresh_size(width, height)
        return {"stream_id": stream_id, "width": width, "height": height}

    @mcp.tool()
    async def tmux_stream_read(
        stream_id: str,
        timeout: float = 10.0,
        max_events: int = 500,
        pane: str | None = None,
        kinds: list[str] | None = None,
        cursor: int | None = None,
        strip_ansi: bool = True,
    ) -> dict:
        """Long-poll new events from a stream (blocks until output or `timeout`).

        Returns {"events", "cursor", "alive", "lagged"}. Each event has a `seq`,
        a `type` ("output", "window-add", "layout-change", "exit", ...) and,
        where relevant, `pane`/`window`/`session` ids and `data`. For "output"
        events `data` is the decoded pane text (ANSI stripped unless
        strip_ansi=False).

        Filter with `pane` (e.g. "%0") and/or `kinds` (e.g. ["output"]). The
        cursor auto-advances between calls, so just call again to get the next
        batch; pass an explicit `cursor` to re-read from a known point. `lagged`
        is true if the buffer overflowed and some events were dropped.
        """
        conn = manager.get(stream_id)
        return await conn.read(
            cursor=cursor,
            timeout=timeout,
            max_events=max_events,
            pane=pane,
            kinds=kinds,
            strip_ansi=strip_ansi,
        )

    @mcp.tool()
    async def tmux_stream_send(stream_id: str, command: str, timeout: float = 10.0) -> dict:
        """Run a tmux command over the stream's control connection.

        `command` is a tmux command line (no leading "tmux"), e.g.
        "list-windows". Returns {"reply"} with the command's text reply. Errors
        from tmux are raised. Output the command triggers in panes still arrives
        as `%output` events via `tmux_stream_read`.
        """
        conn = manager.get(stream_id)
        reply = await conn.send_command(command, timeout=timeout)
        return {"reply": reply}

    @mcp.tool()
    async def tmux_stream_list() -> dict:
        """List active control-mode streams and their state. Returns
        {"streams": [{stream_id, session, target, alive, buffered, panes, ...}]}."""
        return {"streams": manager.list()}

    @mcp.tool()
    async def tmux_stream_stop(stream_id: str) -> dict:
        """Stop a stream: detach the control client (the session keeps running)
        and free the connection. Returns {"stopped": stream_id}."""
        await manager.stop(stream_id)
        return {"stopped": stream_id}
