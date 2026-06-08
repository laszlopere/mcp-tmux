"""Integration tests for the P2 curated tools (clients/server, plumbing,
hooks, keys, copy-mode) and the target-aware resources.

Skipped automatically when tmux is not installed. Uses a private socket so it
never touches the user's real tmux server.
"""

from __future__ import annotations

import json
import shutil

import pytest

from mcp_tmux.runner import TmuxRunner
from mcp_tmux.server import build_server

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)

CONFIG = {"defaults": {"socket_name": "mcp-tmux-p2", "timeout": 10}, "targets": {}}


@pytest.fixture()
async def runner():
    r = TmuxRunner(CONFIG)
    yield r
    await r.run(["kill-server"])


def _tool_json(res):
    payload = res[0] if isinstance(res, tuple) else res
    return json.loads(payload[0].text)


async def test_server_info_and_list_clients(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "infotest"])

    info = _tool_json(await mcp.call_tool("tmux_server_info", {}))
    assert isinstance(info["pid"], int)
    assert "mcp-tmux-p2" in info["socket_path"]
    assert info["supported"] is True

    # Detached session => no attached clients, but the call must succeed.
    clients = _tool_json(await mcp.call_tool("tmux_list_clients", {}))
    assert isinstance(clients["clients"], list)


async def test_break_and_join_pane_roundtrip(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "bj"])
    # Two panes in window bj:0.
    await runner.run_checked(["split-window", "-t", "bj:0"])

    broken = _tool_json(
        await mcp.call_tool(
            "tmux_break_pane",
            {"target_pane": "bj:0.1", "window_name": "broken", "select": False},
        )
    )
    assert broken["name"] == "broken"
    new_window = broken["id"]

    # The broken-out window now exists.
    windows = await runner.list_records(["list-windows", "-t", "bj"], _WINDOW_FIELDS())
    assert any(w["id"] == new_window for w in windows)

    # Join it back into the original window.
    joined = _tool_json(
        await mcp.call_tool(
            "tmux_join_pane", {"src": new_window, "dst": "bj:0", "select": False}
        )
    )
    assert joined["joined"] is True


async def test_find_window(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "fw"])
    await runner.run_checked(["new-window", "-t", "fw", "-n", "needle-haystack"])

    found = _tool_json(
        await mcp.call_tool("tmux_find_window", {"pattern": "needle"})
    )
    assert any(m["name"] == "needle-haystack" for m in found["matches"])

    none = _tool_json(
        await mcp.call_tool("tmux_find_window", {"pattern": "no-such-window-xyz"})
    )
    assert none["matches"] == []


async def test_hooks_set_and_show(runner):
    caps = await runner.capabilities()
    if not caps.has("send_keys_X"):  # proxy for "modern enough" (2.4+ > 2.2)
        pytest.skip("tmux too old for hooks test")
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "hk"])

    _tool_json(
        await mcp.call_tool(
            "tmux_set_hook",
            {"hook": "session-created", "command": 'display "made"', "global_": True},
        )
    )
    shown = _tool_json(await mcp.call_tool("tmux_show_hooks", {"global_": True}))
    # tmux indexes hooks (e.g. "session-created[0]"); match the prefix.
    assert any(k.startswith("session-created") for k in shown["hooks"])


async def test_keys_bind_list_unbind(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "keys"])

    _tool_json(
        await mcp.call_tool(
            "tmux_bind_key",
            {"key": "F12", "command": ["new-window"], "root": True},
        )
    )
    listed = _tool_json(await mcp.call_tool("tmux_list_keys", {}))
    assert "F12" in listed["keys"]

    _tool_json(
        await mcp.call_tool("tmux_unbind_key", {"key": "F12", "root": True})
    )


async def test_pipe_pane_to_file(runner, tmp_path):
    import asyncio

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "pp"])
    logfile = tmp_path / "pane.log"

    _tool_json(
        await mcp.call_tool(
            "tmux_pipe_pane",
            {"target_pane": "pp", "command": f"cat >> {logfile}"},
        )
    )
    await runner.run_checked(["send-keys", "-t", "pp", "-l", "echo piped-XYZ"])
    await runner.run_checked(["send-keys", "-t", "pp", "Enter"])

    for _ in range(20):
        if logfile.exists() and "piped-XYZ" in logfile.read_text():
            break
        await asyncio.sleep(0.1)
    assert "piped-XYZ" in logfile.read_text()

    # Stop piping (no command).
    stopped = _tool_json(
        await mcp.call_tool("tmux_pipe_pane", {"target_pane": "pp"})
    )
    assert stopped["piping"] is False


async def test_copy_mode_enter(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "cm"])
    _tool_json(await mcp.call_tool("tmux_copy_mode", {"target_pane": "cm"}))
    in_mode = (
        await runner.run_checked(
            ["display-message", "-p", "-t", "cm", "#{pane_in_mode}"]
        )
    ).strip()
    assert in_mode == "1"


async def test_target_aware_resource_local(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "restest"])
    res = await mcp.read_resource("tmux://local/sessions")
    payload = res[0] if isinstance(res, (list, tuple)) else res
    data = json.loads(payload.content)
    assert data["target"] == "local"
    assert any(s["name"] == "restest" for s in data["sessions"])


def _WINDOW_FIELDS():
    from mcp_tmux.formats import WINDOW_FIELDS

    return WINDOW_FIELDS
