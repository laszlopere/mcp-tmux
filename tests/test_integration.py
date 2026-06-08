"""End-to-end integration against a real tmux on a throwaway socket.

Skipped automatically when tmux is not installed. Uses a private socket
(`-L mcp-tmux-test`) so it never touches the user's real tmux server.
"""

from __future__ import annotations

import shutil

import pytest

from mcp_tmux.runner import TmuxRunner

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)

# Route every command to an isolated server via a default socket name.
CONFIG = {"defaults": {"socket_name": "mcp-tmux-test", "timeout": 10}, "targets": {}}


@pytest.fixture()
async def runner():
    r = TmuxRunner(CONFIG)
    yield r
    # Tear down the throwaway server regardless of test outcome.
    await r.run(["kill-server"])


async def test_capabilities_detected(runner):
    caps = await runner.capabilities()
    assert caps.version >= (1, 8)
    assert caps.supported


async def test_session_send_capture_roundtrip(runner):
    # Create a detached session.
    out = await runner.run_checked(
        ["new-session", "-d", "-s", "itest", "-P", "-F", "#{session_name}"]
    )
    assert out.strip() == "itest"

    # has-session is true now.
    assert (await runner.run(["has-session", "-t", "itest"])).ok

    # Send a command and run it.
    await runner.run_checked(["send-keys", "-t", "itest", "-l", "echo hello-mcp"])
    await runner.run_checked(["send-keys", "-t", "itest", "Enter"])

    # Give the shell a moment, then capture and assert.
    import asyncio

    for _ in range(20):
        content = await runner.run_checked(["capture-pane", "-p", "-t", "itest"])
        if "hello-mcp" in content:
            break
        await asyncio.sleep(0.1)
    assert "hello-mcp" in content

    # list-sessions via the structured path returns our session.
    from mcp_tmux.formats import SESSION_FIELDS

    sessions = await runner.list_records(["list-sessions"], SESSION_FIELDS)
    assert any(s["name"] == "itest" for s in sessions)

    # Clean up the session.
    await runner.run_checked(["kill-session", "-t", "itest"])


async def test_capture_trim_via_tool(runner):
    """The capture_pane tool trims tmux's trailing blank padding lines."""
    import json

    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "trimtest"])
    await runner.run_checked(["send-keys", "-t", "trimtest", "-l", "echo trimmed"])
    await runner.run_checked(["send-keys", "-t", "trimtest", "Enter"])

    import asyncio

    content = ""
    for _ in range(20):
        res = await mcp.call_tool("tmux_capture_pane", {"target_pane": "trimtest"})
        payload = res[0] if isinstance(res, tuple) else res
        content = json.loads(payload[0].text)["content"]
        if "trimmed" in content:
            break
        await asyncio.sleep(0.1)

    assert "trimmed" in content
    # Trimmed: must not end with a blank line.
    assert not content.endswith("\n")
    assert content.splitlines()[-1].strip() != ""

    # Untrimmed keeps the padding (ends with blank lines).
    res = await mcp.call_tool(
        "tmux_capture_pane", {"target_pane": "trimtest", "trim": False}
    )
    payload = res[0] if isinstance(res, tuple) else res
    raw = json.loads(payload[0].text)["content"]
    assert raw.endswith("\n")

    await runner.run_checked(["kill-session", "-t", "trimtest"])


def _tool_json(res):
    payload = res[0] if isinstance(res, tuple) else res
    import json

    return json.loads(payload[0].text)


async def test_wait_for_text_via_tool(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "waittest"])

    # Send a command that prints a marker after a short delay.
    await runner.run_checked(
        ["send-keys", "-t", "waittest", "-l", "sleep 0.3; echo READY-XYZ"]
    )
    await runner.run_checked(["send-keys", "-t", "waittest", "Enter"])

    res = await mcp.call_tool(
        "tmux_wait_for_text",
        {"target_pane": "waittest", "pattern": "READY-XYZ", "timeout": 8},
    )
    data = _tool_json(res)
    assert data["matched"] is True
    assert "READY-XYZ" in data["content"]

    await runner.run_checked(["kill-session", "-t", "waittest"])


async def test_wait_for_text_timeout(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "wtimeout"])
    res = await mcp.call_tool(
        "tmux_wait_for_text",
        {"target_pane": "wtimeout", "pattern": "NEVER-APPEARS", "timeout": 0.6},
    )
    data = _tool_json(res)
    assert data["matched"] is False
    await runner.run_checked(["kill-session", "-t", "wtimeout"])


async def test_run_collects_output_and_exit(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runtest"])

    ok = _tool_json(
        await mcp.call_tool(
            "tmux_run",
            {"target_pane": "runtest", "command": "echo hello && echo world", "timeout": 10},
        )
    )
    assert ok["completed"] is True
    assert ok["exit_code"] == 0
    assert ok["output"].splitlines() == ["hello", "world"]

    bad = _tool_json(
        await mcp.call_tool(
            "tmux_run",
            {"target_pane": "runtest", "command": "false", "timeout": 10},
        )
    )
    assert bad["completed"] is True
    assert bad["exit_code"] == 1

    await runner.run_checked(["kill-session", "-t", "runtest"])
