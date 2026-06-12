"""Streaming tools built on tmux control mode (`tmux -C`).

Opt-in: open a persistent control-mode connection to a session, then long-poll
its event stream — chiefly ``%output`` (pane wrote data) — so an agent can watch
a pane live instead of polling ``tmux_capture_pane``. One connection is shared
per (target, session); ``tmux_stream_start`` is idempotent.

For one-shot reads, the CLI tools remain the right default; reach for these when
you need to follow output as it happens (a build, a tail, a long-running job).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..control import ControlManager
from ._params import Target
from ._util import toolset_gate

# A stream handle returned by tmux_stream_start.
StreamId = Annotated[str, Field(description="Stream id returned by tmux_stream_start.")]


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)
    manager = ControlManager(runner)

    @tool()
    async def tmux_stream_start(
        session: Annotated[str, Field(description="Session to attach the control-mode stream to.")],
        target: Target = None,
        width: Annotated[
            int | None,
            Field(description="Control client width in columns (with height; tmux 2.4+)."),
        ] = None,
        height: Annotated[
            int | None,
            Field(description="Control client height in rows (with width; tmux 2.4+)."),
        ] = None,
    ) -> dict:
        """Open (or reuse) a control-mode stream attached to `session`.

        Starts a persistent `tmux -C attach -t <session>` connection that
        captures pane output and window/layout events. Idempotent per
        (target, session). Returns {"stream_id", "session", "target", "alive"};
        pass the stream_id to `tmux_stream_read`/`_send`/`_stop`.

        Use this to follow a pane live (a build, a tail, a long job); for a
        one-shot read `tmux_capture_pane` is simpler, and to block until specific
        text appears use `tmux_wait_for_text`.

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

    @tool()
    async def tmux_stream_resize(
        stream_id: StreamId,
        width: Annotated[int, Field(description="New control-client width in columns.")],
        height: Annotated[int, Field(description="New control-client height in rows.")],
    ) -> dict:
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

    @tool()
    async def tmux_stream_read(
        stream_id: StreamId,
        timeout: Annotated[
            float,
            Field(description="Max seconds to block waiting for new events."),
        ] = 10.0,
        max_events: Annotated[
            int,
            Field(description="Maximum events to return in one batch."),
        ] = 500,
        pane: Annotated[
            str | None,
            Field(description='Only return events for this pane (e.g. "%0").'),
        ] = None,
        kinds: Annotated[
            list[str] | None,
            Field(description='Only return these event types (e.g. ["output"]).'),
        ] = None,
        cursor: Annotated[
            int | None,
            Field(description="Re-read from this sequence point; omit to continue from last."),
        ] = None,
        strip_ansi: Annotated[
            bool,
            Field(description="Strip ANSI sequences from output data (default)."),
        ] = True,
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

    @tool()
    async def tmux_stream_send(
        stream_id: StreamId,
        command: Annotated[
            str,
            Field(description='tmux command line to run (no leading "tmux"), e.g. "list-windows".'),
        ],
        timeout: Annotated[
            float,
            Field(description="Max seconds to wait for the command reply."),
        ] = 10.0,
    ) -> dict:
        """Run a tmux command over the stream's control connection.

        `command` is a tmux command line (no leading "tmux"), e.g.
        "list-windows". Returns {"reply"} with the command's text reply. Errors
        from tmux are raised. Output the command triggers in panes still arrives
        as `%output` events via `tmux_stream_read`.
        """
        conn = manager.get(stream_id)
        reply = await conn.send_command(command, timeout=timeout)
        return {"reply": reply}

    @tool()
    async def tmux_stream_list() -> dict:
        """List active control-mode streams and their state. Returns
        {"streams": [{stream_id, session, target, alive, buffered, panes, ...}]}."""
        return {"streams": manager.list()}

    @tool()
    async def tmux_stream_stop(stream_id: StreamId) -> dict:
        """Stop a stream: detach the control client (the session keeps running)
        and free the connection. Returns {"stopped": stream_id}."""
        await manager.stop(stream_id)
        return {"stopped": stream_id}
