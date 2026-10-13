"""The loopback SSH fixture really goes over SSH and reaches a working tmux."""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest
from _sockets import private_socket

from mcp_tmux.runner import TmuxRunner

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

SOCKET = private_socket("mcp-tmux-loopback")


@pytest.fixture
def runner(loopback_ssh):
    config = {
        "defaults": {"timeout": 15},
        "targets": {"loop": loopback_ssh.target_spec(socket_name=SOCKET)},
    }
    r = TmuxRunner(config)
    yield r
    # Isolated socket: killing the whole server is safe and complete.
    subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)


async def test_target_runs_through_ssh(runner, loopback_ssh):
    argv = runner.resolve("loop").build_argv(["-V"])
    assert argv[0] == "ssh"
    assert loopback_ssh.host in argv
    caps = await runner.capabilities("loop")
    assert caps.raw.startswith("tmux")


async def test_session_roundtrip_over_ssh(runner):
    await runner.run_checked(["new-session", "-d", "-s", "lb"], target="loop")
    out = await runner.run_checked(
        ["display-message", "-p", "-t", "lb", "#{session_name}"], target="loop"
    )
    assert out.strip() == "lb"
    # The same server is visible locally: SSH really landed on this machine.
    local = TmuxRunner({"defaults": {"socket_name": SOCKET}, "targets": {}})
    assert (await local.run(["has-session", "-t", "lb"])).ok


async def test_timeout_returns_while_remote_command_still_runs(runner):
    # The multiplexing master keeps the killed client's pipes open until the
    # remote command ends; the timeout must not wait for that.
    await runner.run_checked(["new-session", "-d", "-s", "lbto"], target="loop")
    try:
        result = await asyncio.wait_for(
            runner.run(["wait-for", "lb-never"], target="loop", timeout=0.5), 5
        )
        assert result.exit_code == 124
    finally:
        await runner.run_checked(["wait-for", "-S", "lb-never"], target="loop")
