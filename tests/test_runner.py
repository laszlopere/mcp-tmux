import os

import pytest

from mcp_tmux.runner import TmuxError, TmuxRunner, _map_error


@pytest.fixture
def stand_in(tmp_path):
    """Make a stand-in "tmux" that drops the global flags and execs ``cmd``."""

    def make(cmd: str) -> str:
        path = tmp_path / f"tmux-{os.path.basename(cmd)}"
        path.write_text(f'#!/bin/sh\n[ "$1" = -u ] && shift\nexec {cmd} "$@"\n')
        path.chmod(0o755)
        return str(path)

    return make


async def test_run_local_echo(stand_in):
    # Use /bin/echo as a stand-in "tmux" binary to exercise the exec path
    # without needing tmux installed.
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin=stand_in("/bin/echo"))
    result = await runner.run(["hello", "world"])
    assert result.ok
    assert result.stdout.strip() == "hello world"


async def test_run_checked_raises_on_failure():
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin="/bin/false")
    with pytest.raises(TmuxError):
        await runner.run_checked(["anything"])


async def test_run_missing_binary_returns_127():
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin="/no/such/binary")
    result = await runner.run(["x"])
    assert result.exit_code == 127


async def test_run_timeout(stand_in):
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin=stand_in("/bin/sleep"))
    result = await runner.run(["5"], timeout=0.2)
    assert result.exit_code == 124
    assert "timed out" in result.stderr


@pytest.mark.skipif(not os.path.exists("/proc/self/fd/0"), reason="needs /proc")
async def test_run_does_not_pass_stdin(stand_in):
    # A pipe on fd 0 stands in for the MCP stdio channel (pytest itself puts
    # /dev/null there); readlink stands in for tmux and reports its stdin.
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin=stand_in("/bin/readlink"))
    saved = os.dup(0)
    r, w = os.pipe()
    os.dup2(r, 0)
    try:
        result = await runner.run(["/proc/self/fd/0"])
    finally:
        os.dup2(saved, 0)
        for fd in (saved, r, w):
            os.close(fd)
    assert result.ok
    assert result.stdout.strip() == "/dev/null"


def test_map_error_no_server():
    assert "No tmux server" in _map_error("no server running on /tmp/x", 1)


def test_map_error_missing_session():
    msg = _map_error("can't find session: foo", 1)
    assert "does not exist" in msg
