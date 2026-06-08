"""Tests for control-mode streaming (P3).

The parsing / reply-framing / event-buffer logic is exercised directly with no
subprocess (deterministic). A single end-to-end test drives a real `tmux -C`
connection and is skipped when tmux is absent.
"""

from __future__ import annotations

import shutil

import pytest

from mcp_tmux.control import (
    ControlConnection,
    decode_output,
    parse_event,
    unescape_output_bytes,
)
from mcp_tmux.runner import TmuxError, TmuxRunner

# --- pure helpers -----------------------------------------------------------


def test_unescape_octal_and_backslash():
    assert unescape_output_bytes("abc") == b"abc"
    assert unescape_output_bytes("a\\015\\012b") == b"a\r\nb"
    assert unescape_output_bytes("x\\\\y") == b"x\\y"
    # multibyte UTF-8 escaped byte-by-byte (é == \303\251) round-trips.
    assert unescape_output_bytes("\\303\\251").decode("utf-8") == "é"


def test_decode_output_strips_ansi_and_normalises_crlf():
    raw = "\\033[01;32mhi\\033[00m\\015\\012"
    assert decode_output(raw) == "hi\n"
    assert decode_output(raw, strip_ansi=False) == "\x1b[01;32mhi\x1b[00m\n"
    # bracketed-paste markers are CSI sequences and get stripped too.
    assert decode_output("\\033[?2004lDONE") == "DONE"


def test_parse_event_output():
    ev = parse_event("%output %0 echo hi\\015\\012", 7)
    assert ev.type == "output"
    assert ev.pane == "%0"
    assert ev.seq == 7
    assert ev.as_dict()["data"] == "echo hi\n"


def test_parse_event_window_and_session_and_exit():
    assert parse_event("%window-add @1", 1).window == "@1"
    lc = parse_event("%layout-change @0 b25d,80x24,0,0,0 b25d,80x24,0,0,0 *", 2)
    assert lc.type == "layout-change" and lc.window == "@0"
    sc = parse_event("%session-changed $0 p", 3)
    assert sc.type == "session-changed" and sc.session == "$0"
    pm = parse_event("%pane-mode-changed %3", 4)
    assert pm.pane == "%3"
    ex = parse_event("%exit", 5)
    assert ex.type == "exit" and ex.data is None


# --- connection logic, fed lines directly (no subprocess) -------------------


def _conn():
    runner = TmuxRunner({"defaults": {}, "targets": {}})
    return ControlConnection(runner, runner.resolve(None), "s")


async def test_reply_framing_success_and_error():
    import asyncio

    conn = _conn()
    conn.alive = True
    loop = asyncio.get_running_loop()

    ok = loop.create_future()
    conn._pending.append(ok)
    await conn._handle_line("%begin 1 2 0")
    await conn._handle_line("line one")
    await conn._handle_line("line two")
    await conn._handle_line("%end 1 2 0")
    assert ok.result() == "line one\nline two"

    bad = loop.create_future()
    conn._pending.append(bad)
    await conn._handle_line("%begin 1 3 0")
    await conn._handle_line("parse error: nope")
    await conn._handle_line("%error 1 3 0")
    with pytest.raises(TmuxError) as ei:
        bad.result()
    assert "parse error: nope" in str(ei.value)


async def test_events_buffer_and_cursor_advance():
    conn = _conn()
    conn.alive = True
    await conn._handle_line("%output %0 hello\\015\\012")
    await conn._handle_line("%window-add @1")

    r1 = await conn.read(timeout=0.1)
    assert [e["type"] for e in r1["events"]] == ["output", "window-add"]
    assert r1["events"][0]["data"] == "hello\n"
    assert r1["cursor"] == 2 and r1["lagged"] is False

    # Cursor auto-advanced: nothing new now.
    r2 = await conn.read(timeout=0.1)
    assert r2["events"] == [] and r2["cursor"] == 2

    # Filter by pane: only the output event carries pane %0.
    await conn._handle_line("%output %0 again\\012")
    r3 = await conn.read(timeout=0.1, pane="%0")
    assert len(r3["events"]) == 1 and r3["events"][0]["pane"] == "%0"


async def test_exit_marks_not_alive():
    conn = _conn()
    conn.alive = True
    await conn._handle_line("%exit")
    assert conn.alive is False


# --- auto-reconnect machinery (no subprocess) -------------------------------


async def test_reconnect_succeeds_and_emits_event():
    conn = _conn()
    conn._reconnect_base_delay = 0  # don't actually wait
    spawns = []

    async def fake_spawn():
        spawns.append(1)
        conn.alive = True

    conn._spawn = fake_spawn  # type: ignore[assignment]
    await conn._reconnect()

    assert conn.reconnects == 1
    assert len(spawns) == 1
    r = await conn.read(timeout=0.1)
    assert r["events"][-1]["type"] == "reconnected"


async def test_reconnect_retries_then_gives_up():
    conn = _conn()
    conn._reconnect_base_delay = 0
    conn._max_reconnects = 3
    attempts = []

    async def always_fail():
        attempts.append(1)
        raise OSError("ssh down")

    conn._spawn = always_fail  # type: ignore[assignment]
    await conn._reconnect()

    assert len(attempts) == 3
    assert conn.reconnects == 0
    r = await conn.read(timeout=0.1)
    assert r["events"][-1]["type"] == "disconnected"


async def test_close_suppresses_reconnect():
    conn = _conn()
    conn._reconnect_base_delay = 0
    called = []

    async def fake_spawn():
        called.append(1)

    conn._spawn = fake_spawn  # type: ignore[assignment]
    await conn.close()  # sets _closing
    await conn._reconnect()

    assert called == []
    assert conn.reconnects == 0


# --- end-to-end against real tmux -------------------------------------------

pytestmark_e2e = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)

CONFIG = {"defaults": {"socket_name": "mcp-tmux-cm", "timeout": 10}, "targets": {}}


def _tool_json(res):
    import json

    payload = res[0] if isinstance(res, tuple) else res
    return json.loads(payload[0].text)


@pytestmark_e2e
async def test_control_mode_end_to_end():
    from mcp_tmux.runner import TmuxRunner
    from mcp_tmux.server import build_server

    runner = TmuxRunner(CONFIG)
    mcp = build_server(config=CONFIG)
    try:
        await runner.run_checked(["new-session", "-d", "-s", "strm", "-x", "80", "-y", "24"])

        start = _tool_json(
            await mcp.call_tool("tmux_stream_start", {"session": "strm"})
        )
        sid = start["stream_id"]
        assert start["alive"] is True

        # Idempotent: same (target, session) reuses the connection.
        again = _tool_json(
            await mcp.call_tool("tmux_stream_start", {"session": "strm"})
        )
        assert again["stream_id"] == sid

        # Trigger pane output and watch it arrive as %output events.
        await runner.run_checked(["send-keys", "-t", "strm", "-l", "echo MARKER-123"])
        await runner.run_checked(["send-keys", "-t", "strm", "Enter"])

        seen = ""
        for _ in range(8):
            r = _tool_json(
                await mcp.call_tool(
                    "tmux_stream_read",
                    {"stream_id": sid, "timeout": 2, "kinds": ["output"]},
                )
            )
            seen += "".join(e.get("data", "") for e in r["events"])
            if "MARKER-123" in seen:
                break
        assert "MARKER-123" in seen

        # Send a command through the control connection and read its reply.
        rep = _tool_json(
            await mcp.call_tool(
                "tmux_stream_send", {"stream_id": sid, "command": "list-windows"}
            )
        )
        assert "(active)" in rep["reply"]

        # It shows up in the stream list...
        listed = _tool_json(await mcp.call_tool("tmux_stream_list", {}))
        assert any(s["stream_id"] == sid for s in listed["streams"])

        # ...and is gone after stopping.
        _tool_json(await mcp.call_tool("tmux_stream_stop", {"stream_id": sid}))
        listed2 = _tool_json(await mcp.call_tool("tmux_stream_list", {}))
        assert all(s["stream_id"] != sid for s in listed2["streams"])
    finally:
        await runner.run(["kill-server"])


@pytestmark_e2e
async def test_stream_start_sets_client_size():
    import asyncio

    from mcp_tmux.runner import TmuxRunner
    from mcp_tmux.server import build_server

    runner = TmuxRunner(CONFIG)
    caps = await runner.capabilities()
    if not caps.has("refresh_client_size"):
        await runner.run(["kill-server"])
        pytest.skip("tmux too old for refresh-client -C")

    mcp = build_server(config=CONFIG)
    try:
        # Session starts at 80x24; a sized control client should widen it.
        await runner.run_checked(
            ["new-session", "-d", "-s", "sz", "-x", "80", "-y", "24"]
        )
        start = _tool_json(
            await mcp.call_tool(
                "tmux_stream_start",
                {"session": "sz", "width": 200, "height": 50},
            )
        )
        sid = start["stream_id"]

        async def window_width() -> int:
            out = await runner.run_checked(
                ["display-message", "-p", "-t", "sz", "#{window_width}"]
            )
            return int(out.strip())

        for _ in range(20):
            if await window_width() == 200:
                break
            await asyncio.sleep(0.1)
        assert await window_width() == 200

        # The size is reported in the stream listing.
        listed = _tool_json(await mcp.call_tool("tmux_stream_list", {}))
        info = next(s for s in listed["streams"] if s["stream_id"] == sid)
        assert info["size"] == [200, 50]
        assert info["reconnects"] == 0

        # Resize narrower via the dedicated tool.
        _tool_json(
            await mcp.call_tool(
                "tmux_stream_resize",
                {"stream_id": sid, "width": 120, "height": 40},
            )
        )
        for _ in range(20):
            if await window_width() == 120:
                break
            await asyncio.sleep(0.1)
        assert await window_width() == 120

        # Stop the stream (suppresses auto-reconnect) before tearing down.
        _tool_json(await mcp.call_tool("tmux_stream_stop", {"stream_id": sid}))
    finally:
        await runner.run(["kill-server"])
