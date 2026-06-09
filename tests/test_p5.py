"""Integration tests for the P5 curated tools.

Skipped automatically when tmux is not installed. Uses a private socket so it
never touches the user's real tmux server.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest

from mcp_tmux.runner import TmuxRunner
from mcp_tmux.server import build_server

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

CONFIG = {
    "defaults": {"socket_name": "mcp-tmux-p5", "timeout": 10},
    "targets": {},
    "toolsets": ["all"],
}


@pytest.fixture()
async def runner():
    r = TmuxRunner(CONFIG)
    yield r
    await r.run(["kill-server"])


def _tool_json(res):
    payload = res[0] if isinstance(res, tuple) else res
    return json.loads(payload[0].text)


async def _history_size(runner, pane: str) -> int:
    out = await runner.run_checked(["display-message", "-p", "-t", pane, "#{history_size}"])
    return int(out.strip())


async def test_clear_history_wipes_scrollback(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "clr", "-x", "80", "-y", "24"])

    # Produce enough output to push lines into the scrollback buffer.
    await runner.run_checked(["send-keys", "-t", "clr", "-l", "seq 200"])
    await runner.run_checked(["send-keys", "-t", "clr", "Enter"])
    for _ in range(20):
        if await _history_size(runner, "clr") > 0:
            break
        await asyncio.sleep(0.1)
    assert await _history_size(runner, "clr") > 0

    cleared = _tool_json(await mcp.call_tool("tmux_clear_history", {"target_pane": "clr"}))
    assert cleared == {"cleared": True, "pane": "clr"}

    assert await _history_size(runner, "clr") == 0


async def test_clear_history_is_destructive(runner):
    mcp = build_server(config=CONFIG)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    ann = tools["tmux_clear_history"].annotations
    assert ann is not None
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is True


async def _current_command(runner, pane: str) -> str:
    out = await runner.run_checked(["display-message", "-p", "-t", pane, "#{pane_current_command}"])
    return out.strip()


async def test_respawn_pane_restarts_command(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "rsp"])

    # Force-restart the (still-running shell) pane with a new command.
    res = _tool_json(
        await mcp.call_tool(
            "tmux_respawn",
            {"kind": "pane", "id": "rsp", "command": "sleep 300", "kill": True},
        )
    )
    assert res == {"respawned": True, "kind": "pane", "id": "rsp"}

    for _ in range(20):
        if await _current_command(runner, "rsp") == "sleep":
            break
        await asyncio.sleep(0.1)
    assert await _current_command(runner, "rsp") == "sleep"


async def test_respawn_pane_with_env(runner):
    caps = await runner.capabilities()
    if not caps.has("respawn_env"):
        pytest.skip("tmux too old for respawn -e")
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "rspe", "-x", "80", "-y", "24"])

    res = _tool_json(
        await mcp.call_tool(
            "tmux_respawn",
            {
                "kind": "pane",
                "id": "rspe",
                "command": "sh -c 'echo GOT-$MCP_FOO; exec sleep 300'",
                "kill": True,
                "env": {"MCP_FOO": "barbaz"},
            },
        )
    )
    assert res["respawned"] is True
    assert "notes" not in res

    for _ in range(20):
        out = await runner.run_checked(["capture-pane", "-p", "-t", "rspe"])
        if "GOT-barbaz" in out:
            break
        await asyncio.sleep(0.1)
    assert "GOT-barbaz" in out


async def test_respawn_window_restarts_command(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "rsw"])

    res = _tool_json(
        await mcp.call_tool(
            "tmux_respawn",
            {"kind": "window", "id": "rsw", "command": "sleep 300", "kill": True},
        )
    )
    assert res == {"respawned": True, "kind": "window", "id": "rsw"}

    for _ in range(20):
        if await _current_command(runner, "rsw") == "sleep":
            break
        await asyncio.sleep(0.1)
    assert await _current_command(runner, "rsw") == "sleep"


async def test_set_show_environment_roundtrip(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "envt"])

    setres = _tool_json(
        await mcp.call_tool(
            "tmux_set_environment",
            {"name": "MCP_ENVVAR", "value": "hello", "session": "envt"},
        )
    )
    assert setres == {"set": "MCP_ENVVAR", "value": "hello", "global": False}

    # Read just that variable back.
    shown = _tool_json(
        await mcp.call_tool(
            "tmux_show_environment",
            {"name": "MCP_ENVVAR", "session": "envt"},
        )
    )
    assert shown["environment"]["MCP_ENVVAR"] == "hello"

    # A command launched afterwards inherits it.
    await runner.run_checked(
        ["respawn-pane", "-k", "-t", "envt", "sh -c 'echo SAW-$MCP_ENVVAR; exec sleep 300'"]
    )
    for _ in range(20):
        out = await runner.run_checked(["capture-pane", "-p", "-t", "envt"])
        if "SAW-hello" in out:
            break
        await asyncio.sleep(0.1)
    assert "SAW-hello" in out


async def test_unset_environment(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "envu"])

    await mcp.call_tool(
        "tmux_set_environment",
        {"name": "MCP_GONE", "value": "x", "session": "envu"},
    )
    unset = _tool_json(
        await mcp.call_tool(
            "tmux_set_environment",
            {"name": "MCP_GONE", "unset": True, "session": "envu"},
        )
    )
    assert unset == {"unset": "MCP_GONE", "global": False}

    shown = _tool_json(await mcp.call_tool("tmux_show_environment", {"session": "envu"}))
    assert "MCP_GONE" not in shown["environment"]


async def test_show_environment_is_read_only(runner):
    mcp = build_server(config=CONFIG)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    assert tools["tmux_show_environment"].annotations.readOnlyHint is True


async def test_new_session_attach_or_create_is_idempotent(runner):
    mcp = build_server(config=CONFIG)

    first = _tool_json(
        await mcp.call_tool("tmux_new_session", {"name": "aoc", "attach_or_create": True})
    )
    assert first["name"] == "aoc"

    # A second create-or-attach with the same name must not error; it reuses
    # the existing session (same session id).
    again = _tool_json(
        await mcp.call_tool("tmux_new_session", {"name": "aoc", "attach_or_create": True})
    )
    assert again["name"] == "aoc"
    assert again["id"] == first["id"]

    sessions = await runner.run_checked(["list-sessions", "-F", "#{session_name}"])
    assert sessions.split().count("aoc") == 1


async def test_new_session_with_env(runner):
    caps = await runner.capabilities()
    if not caps.has("new_session_env"):
        pytest.skip("tmux too old for new-session -e")
    mcp = build_server(config=CONFIG)

    res = _tool_json(
        await mcp.call_tool(
            "tmux_new_session",
            {
                "name": "nse",
                "width": 80,
                "height": 24,
                "command": "sh -c 'echo GOT-$MCP_BAR; exec sleep 300'",
                "env": {"MCP_BAR": "quux"},
            },
        )
    )
    assert res["name"] == "nse"
    assert "notes" not in res

    for _ in range(20):
        out = await runner.run_checked(["capture-pane", "-p", "-t", "nse"])
        if "GOT-quux" in out:
            break
        await asyncio.sleep(0.1)
    assert "GOT-quux" in out


async def test_set_pane_title(runner):
    caps = await runner.capabilities()
    if not caps.has("pane_title"):
        pytest.skip("tmux too old for select-pane -T")
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "ptitle"])

    res = _tool_json(
        await mcp.call_tool(
            "tmux_set_pane_title",
            {"target_pane": "ptitle", "title": "agent-pane-1"},
        )
    )
    assert res == {"pane": "ptitle", "title": "agent-pane-1"}

    out = await runner.run_checked(["display-message", "-p", "-t", "ptitle", "#{pane_title}"])
    assert out.strip() == "agent-pane-1"

    # And it surfaces through the curated listing.
    panes = _tool_json(await mcp.call_tool("tmux_list_panes", {"session": "ptitle"}))
    assert any(p["title"] == "agent-pane-1" for p in panes["panes"])


async def test_last_window_switches_back(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "nav"])
    # Creating a second window makes it active; window 0 becomes "last".
    await runner.run_checked(["new-window", "-t", "nav"])
    assert (
        await runner.run_checked(["display-message", "-p", "-t", "nav", "#{window_index}"])
    ).strip() == "1"

    res = _tool_json(await mcp.call_tool("tmux_last", {"kind": "window", "scope": "nav"}))
    assert res == {"selected": "last", "kind": "window"}
    assert (
        await runner.run_checked(["display-message", "-p", "-t", "nav", "#{window_index}"])
    ).strip() == "0"


async def test_last_pane_switches_back(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "navp", "-x", "80", "-y", "24"])
    # Splitting makes the new pane (index 1) active; pane 0 becomes "last".
    await runner.run_checked(["split-window", "-t", "navp"])
    assert (
        await runner.run_checked(["display-message", "-p", "-t", "navp", "#{pane_index}"])
    ).strip() == "1"

    res = _tool_json(await mcp.call_tool("tmux_last", {"kind": "pane", "scope": "navp"}))
    assert res == {"selected": "last", "kind": "pane"}
    assert (
        await runner.run_checked(["display-message", "-p", "-t", "navp", "#{pane_index}"])
    ).strip() == "0"


async def test_next_layout_changes_layout(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "navl", "-x", "80", "-y", "24"])
    await runner.run_checked(["split-window", "-t", "navl"])

    async def _layout() -> str:
        return (
            await runner.run_checked(["display-message", "-p", "-t", "navl", "#{window_layout}"])
        ).strip()

    before = await _layout()
    res = _tool_json(await mcp.call_tool("tmux_next_layout", {"window": "navl"}))
    assert res == {"window": "navl"}
    assert await _layout() != before


async def test_save_buffer_writes_file(runner, tmp_path):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "savb"])
    await runner.run_checked(["set-buffer", "-b", "b1", "buffer-payload-XYZ"])

    out = tmp_path / "buf.txt"
    res = _tool_json(await mcp.call_tool("tmux_save_buffer", {"path": str(out), "name": "b1"}))
    assert res == {"saved": str(out), "name": "b1", "appended": False}
    assert out.read_text() == "buffer-payload-XYZ"


async def test_load_buffer_reads_file(runner, tmp_path):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "loadb"])

    src = tmp_path / "in.txt"
    src.write_text("loaded-content-ABC")
    res = _tool_json(await mcp.call_tool("tmux_load_buffer", {"path": str(src), "name": "lb"}))
    assert res == {"loaded": str(src), "name": "lb"}

    back = await runner.run_checked(["show-buffer", "-b", "lb"])
    assert back == "loaded-content-ABC"
