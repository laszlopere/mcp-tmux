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
