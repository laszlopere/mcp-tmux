"""End-to-end integration against a real tmux on a throwaway socket.

Skipped automatically when tmux is not installed. Uses a private socket
(`-L mcp-tmux-test`) so it never touches the user's real tmux server.
"""

from __future__ import annotations

import shutil

import pytest

from mcp_tmux.runner import TmuxRunner

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

# Route every command to an isolated server via a default socket name.
CONFIG = {
    "defaults": {"socket_name": "mcp-tmux-test", "timeout": 10},
    "targets": {},
    "toolsets": ["all"],
}


@pytest.fixture()
async def runner(transport):
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
        payload = res.content
        content = json.loads(payload[0].text)["content"]
        if "trimmed" in content:
            break
        await asyncio.sleep(0.1)

    assert "trimmed" in content
    # Trimmed: must not end with a blank line.
    assert not content.endswith("\n")
    assert content.splitlines()[-1].strip() != ""

    # Untrimmed keeps the padding (ends with blank lines).
    res = await mcp.call_tool("tmux_capture_pane", {"target_pane": "trimtest", "trim": False})
    payload = res.content
    raw = json.loads(payload[0].text)["content"]
    assert raw.endswith("\n")

    await runner.run_checked(["kill-session", "-t", "trimtest"])


def _tool_json(res):
    payload = res.content
    import json

    return json.loads(payload[0].text)


async def test_wait_for_text_via_tool(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "waittest"])

    # Send a command that prints a marker after a short delay.
    await runner.run_checked(["send-keys", "-t", "waittest", "-l", "sleep 0.3; echo READY-XYZ"])
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
        {
            "target_pane": "wtimeout",
            "pattern": "NEVER-APPEARS",
            "timeout": 0.3,
            "poll_interval": 0.1,
        },
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


async def _run(mcp, pane, command, timeout=10):
    return _tool_json(
        await mcp.call_tool(
            "tmux_run", {"target_pane": pane, "command": command, "timeout": timeout}
        )
    )


async def test_run_reports_nonzero_exit_codes(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runexit"])

    r = await _run(mcp, "runexit", "echo partial; sh -c 'exit 42'")
    assert (r["completed"], r["exit_code"]) == (True, 42)
    assert r["output"].splitlines() == ["partial"]

    # stderr lands in the pane too, so it is part of the output.
    r = await _run(mcp, "runexit", "no-such-command-mcp-tmux")
    assert (r["completed"], r["exit_code"]) == (True, 127)
    assert "no-such-command-mcp-tmux" in r["output"]

    await runner.run_checked(["kill-session", "-t", "runexit"])


async def test_run_timeout_while_running_then_recovers(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runto"])
    # Let the shell start, so the C-c below meets the running command and not
    # a shell still starting up (the short timeout would not cover that).
    await _run(mcp, "runto", "true")

    r = await _run(mcp, "runto", "sleep 30", timeout=0.5)
    assert r["completed"] is False
    assert r["exit_code"] is None
    assert "timed out" in r["error"]
    assert r["elapsed"] < 10

    # Interrupt the still-running command; the pane is usable again and the
    # stale markers of the timed-out run don't leak into the next result.
    await runner.run_checked(["send-keys", "-t", "runto", "C-c"])
    r = await _run(mcp, "runto", "echo recovered")
    assert (r["completed"], r["exit_code"]) == (True, 0)
    assert r["output"].splitlines() == ["recovered"]

    await runner.run_checked(["kill-session", "-t", "runto"])


async def test_run_output_containing_marker_text(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runmk"])

    # Output that looks like another run's markers is returned as output, not
    # mistaken for this run's bracket.
    fake = "printf '%s\\n' __MCP_BEG_00000000__ '__MCP_END_00000000__ 7' 'x __MCP_END_ 0'"
    r = await _run(mcp, "runmk", fake)
    assert (r["completed"], r["exit_code"]) == (True, 0)
    assert r["output"].splitlines() == [
        "__MCP_BEG_00000000__",
        "__MCP_END_00000000__ 7",
        "x __MCP_END_ 0",
    ]

    # A second run in the same pane: the earlier markers sit in scrollback.
    r = await _run(mcp, "runmk", "echo second")
    assert r["output"].splitlines() == ["second"]

    await runner.run_checked(["kill-session", "-t", "runmk"])


async def _wait_idle(mcp, pane, **args):
    return _tool_json(await mcp.call_tool("tmux_wait_for_idle", {"target_pane": pane, **args}))


async def test_wait_for_idle_on_quiet_pane(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "idleq"])
    await _run(mcp, "idleq", "echo QUIET-PANE")

    r = await _wait_idle(mcp, "idleq", idle_seconds=0.25, timeout=10)
    assert r["idle"] is True
    assert 0.25 <= r["elapsed"] < 10
    assert "QUIET-PANE" in r["content"]

    await runner.run_checked(["kill-session", "-t", "idleq"])


async def test_wait_for_idle_after_output_settles(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "idles"])
    await _run(mcp, "idles", "true")  # prompt is up

    await runner.run_checked(
        ["send-keys", "-t", "idles", "-l", "for i in $(seq 8); do echo tick$i; sleep 0.1; done"]
    )
    await runner.run_checked(["send-keys", "-t", "idles", "Enter"])
    r = await _wait_idle(mcp, "idles", idle_seconds=0.5, timeout=15, poll_interval=0.1)
    assert r["idle"] is True
    # Not idle while ticking: the last tick arrives at least 0.7s after the
    # first, then 0.5s of quiet. Ignoring the ticks would return after ~0.5s.
    assert r["elapsed"] >= 1.0
    assert "tick8" in r["content"]

    await runner.run_checked(["kill-session", "-t", "idles"])


async def test_wait_for_idle_times_out_on_busy_pane(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "idleb"])
    await _run(mcp, "idleb", "true")

    await runner.run_checked(
        ["send-keys", "-t", "idleb", "-l", "while :; do date +%s%N; sleep 0.1; done"]
    )
    await runner.run_checked(["send-keys", "-t", "idleb", "Enter"])
    # The timeout spans two idle windows, so a missed tick can't pass for idle.
    r = await _wait_idle(mcp, "idleb", idle_seconds=0.5, timeout=1, poll_interval=0.1)
    assert r["idle"] is False
    assert 1 <= r["elapsed"] < 5

    await runner.run_checked(["send-keys", "-t", "idleb", "C-c"])
    await runner.run_checked(["kill-session", "-t", "idleb"])


@pytest.mark.parametrize("arg", ["plain", "ls;", ";", "x" + chr(92) + ";", "a;b"])
async def test_arguments_reach_tmux_literally(runner, arg):
    # A trailing ";" is tmux's command separator; the runner escapes it.
    await runner.run_checked(["new-session", "-d", "-s", "lit"])
    out = await runner.run_checked(
        ["set-buffer", "-b", "lit", "--", arg],
        ["show-buffer", "-b", "lit"],
    )
    assert out == arg


async def test_batch_stops_at_first_failing_command(runner):
    await runner.run_checked(["new-session", "-d", "-s", "bat"])
    result = await runner.run(
        ["has-session", "-t", "no-such-session"],
        ["display-message", "-p", "ran"],
    )
    assert not result.ok
    assert "ran" not in result.stdout
    ok = await runner.run_checked(["has-session", "-t", "bat"], ["display-message", "-p", "ran"])
    assert ok.strip() == "ran"


async def test_run_completion_is_signalled_not_polled(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runsig"])
    await _run(mcp, "runsig", "true")  # let the shell start

    # A poll every 60s would time out; the wait-for signal ends the wait.
    r = _tool_json(
        await mcp.call_tool(
            "tmux_run",
            {
                "target_pane": "runsig",
                "command": "sleep 0.2; echo slept",
                "timeout": 10,
                "poll_interval": 60,
            },
        )
    )
    assert (r["completed"], r["exit_code"], r["output"]) == (True, 0, "slept")
    assert 0.2 <= r["elapsed"] < 5

    await runner.run_checked(["kill-session", "-t", "runsig"])


async def test_run_polls_when_the_shell_cannot_signal(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    # Without $TMUX the shell's `tmux wait-for -S` can't find this server.
    await runner.run_checked(["new-session", "-d", "-s", "runpoll", "env -u TMUX sh"])

    r = _tool_json(
        await mcp.call_tool(
            "tmux_run",
            {
                "target_pane": "runpoll",
                "command": "echo polled; false",
                "timeout": 10,
                "poll_interval": 0.05,
            },
        )
    )
    assert (r["completed"], r["exit_code"], r["output"]) == (True, 1, "polled")

    await runner.run_checked(["kill-session", "-t", "runpoll"])


async def test_run_output_without_trailing_newline(runner):
    from mcp_tmux.server import build_server

    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "runnl"])
    r = await _run(mcp, "runnl", "printf 'a\\nno-newline'")
    assert (r["completed"], r["exit_code"]) == (True, 0)
    assert r["output"].splitlines() == ["a", "no-newline"]
    await runner.run_checked(["kill-session", "-t", "runnl"])
