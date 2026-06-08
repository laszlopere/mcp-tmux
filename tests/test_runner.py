import pytest

from mcp_tmux.runner import TmuxError, TmuxRunner, _map_error


async def test_run_local_echo():
    # Use /bin/echo as a stand-in "tmux" binary to exercise the exec path
    # without needing tmux installed.
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin="/bin/echo")
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


async def test_run_timeout():
    runner = TmuxRunner({"defaults": {}, "targets": {}}, tmux_bin="/bin/sleep")
    result = await runner.run(["5"], timeout=0.2)
    assert result.exit_code == 124
    assert "timed out" in result.stderr


def test_map_error_no_server():
    assert "No tmux server" in _map_error("no server running on /tmp/x", 1)


def test_map_error_missing_session():
    msg = _map_error("can't find session: foo", 1)
    assert "does not exist" in msg
