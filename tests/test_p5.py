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

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)

CONFIG = {"defaults": {"socket_name": "mcp-tmux-p5", "timeout": 10}, "targets": {}}


@pytest.fixture()
async def runner():
    r = TmuxRunner(CONFIG)
    yield r
    await r.run(["kill-server"])


def _tool_json(res):
    payload = res[0] if isinstance(res, tuple) else res
    return json.loads(payload[0].text)


async def _history_size(runner, pane: str) -> int:
    out = await runner.run_checked(
        ["display-message", "-p", "-t", pane, "#{history_size}"]
    )
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

    cleared = _tool_json(
        await mcp.call_tool("tmux_clear_history", {"target_pane": "clr"})
    )
    assert cleared == {"cleared": True, "pane": "clr"}

    assert await _history_size(runner, "clr") == 0


async def test_clear_history_is_destructive(runner):
    mcp = build_server(config=CONFIG)
    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    ann = tools["tmux_clear_history"].annotations
    assert ann is not None
    assert ann.readOnlyHint is False
    assert ann.destructiveHint is True
