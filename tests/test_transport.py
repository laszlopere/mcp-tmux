"""The control-mode command transport (src/mcp_tmux/transport.py)."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import replace
from typing import Any

import pytest

import mcp_tmux.runner
from mcp_tmux.config import load_config
from mcp_tmux.runner import TmuxRunner, join_commands
from mcp_tmux.server import build_server
from mcp_tmux.targets import Target, resolve_target
from mcp_tmux.transport import command_line, needs_oneshot, quote_arg, split_cli_args

BS = chr(92)  # a backslash

# --- pure helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    "arg, quoted",
    [
        ("send-keys", "send-keys"),
        ("%1", "'%1'"),
        ("dev:1.0", "dev:1.0"),
        ("", "''"),
        ("a b", "'a b'"),
        ("#{pane_id}", "'#{pane_id}'"),
        ("$HOME ~ ; {", "'$HOME ~ ; {'"),
        ("FOO=bar", "'FOO=bar'"),
        ("it's", '"it' + "'" + 's"'),
        ("a\nb", '"a' + BS + '012b"'),
        ('q"' + BS + "$", "'q\"" + BS + "$'"),
        ("it's" + BS + '"$', '"it' + "'s" + BS + BS + BS + '"' + BS + '$"'),
    ],
)
def test_quote_arg(arg, quoted):
    assert quote_arg(arg) == quoted


def test_command_line_joins_commands():
    assert command_line([["a", "x y"], ["b"]]) == "a 'x y' ; b"


@pytest.mark.parametrize(
    "commands",
    [
        [["send-keys", "-t", "%1", "-l", "ls;"], ["send-keys", "-t", "%1", "Enter"]],
        [["set-buffer", ";"], ["set-buffer", "x" + BS + ";"], ["show-buffer"]],
        [["display-message", "-p", "a;b"]],
    ],
)
def test_split_cli_args_inverts_join_commands(commands):
    assert split_cli_args(join_commands(tuple(commands))) == commands


def test_split_cli_args_reads_tmux_separators():
    assert split_cli_args(["a", "1;", "b", ";", "c" + BS + ";"]) == [["a", "1"], ["b"], ["c;"]]


@pytest.mark.parametrize(
    "commands, oneshot",
    [
        ([["capture-pane", "-p", "-t", "%1"]], False),
        ([["send-keys", "-t", "%1", "x"], ["send-keys", "-t", "%1", "Enter"]], False),
        ([["display-message", "-p", "#{pane_id}"]], False),
        ([["display-message", "-t", "%1", "hello"]], True),  # to our status line
        ([["-V"]], True),
        ([["wait-for", "ch"]], True),
        ([["wait", "ch"]], True),
        ([["kill-ser"]], True),  # a prefix tmux accepts
        ([["new-session", "-d"]], True),
        ([["show-buffer"]], True),
        ([["send-keys", "x"], ["run-shell", "true"]], True),
        ([[]], True),
    ],
)
def test_needs_oneshot(commands, oneshot):
    assert needs_oneshot(commands) is oneshot


def test_config_rejects_unknown_transport(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[defaults]\ntransport = "carrier-pigeon"\n')
    with pytest.raises(ValueError, match="defaults.transport"):
        load_config(path)
    path.write_text('[targets.box]\nhost = "box"\ntransport = "fast"\n')
    with pytest.raises(ValueError, match="targets.box.transport"):
        load_config(path)


def test_transport_from_config_and_runtime_switch():
    cfg = {
        "defaults": {"transport": "control"},
        "targets": {"box": {"host": "box", "transport": "oneshot"}},
    }
    r = TmuxRunner(cfg)
    assert (r.transport(None), r.transport("box"), r.transport("u@h")) == (
        "control",
        "oneshot",
        "control",
    )
    asyncio.run(r.set_transport("box", "control"))
    assert r.transport("box") == "control"
    assert r.configured_transport("box") == "oneshot"
    with pytest.raises(ValueError):
        asyncio.run(r.set_transport(None, "fast"))


# --- against real tmux, locally and over loopback SSH -------------------------

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
SOCKET = "mcp-tmux-transport"
CONFIG = {
    "defaults": {"socket_name": SOCKET, "timeout": 10, "transport": "control"},
    "targets": {},
    "toolsets": ["all"],
}


@pytest.fixture(params=["local", "ssh"])
async def runner(request, monkeypatch):
    if request.param == "ssh":
        lb = request.getfixturevalue("loopback_ssh")

        def via_ssh(target: str | None, config: dict[str, Any]) -> Target:
            tgt = resolve_target(target, config)
            return replace(tgt, host=lb.host, ssh_options=lb.ssh_options)

        monkeypatch.setattr(mcp_tmux.runner, "resolve_target", via_ssh)
    r = TmuxRunner(CONFIG)
    yield r
    await r.run(["kill-server"])


def _json(res):
    return json.loads(res.content[0].text)


@needs_tmux
async def test_commands_go_over_the_control_client(runner):
    await runner.run_checked(["new-session", "-d", "-s", "t1"])
    assert not runner.control_connected(None)  # new-session ran one-shot
    out = await runner.run_checked(["display-message", "-p", "-t", "t1", "#{session_name}"])
    assert out == "t1\n"
    assert runner.control_connected(None)
    # The client is attached: the documented side effect.
    attached = await runner.run_checked(
        ["display-message", "-p", "-t", "t1", "#{session_attached}"]
    )
    assert attached.strip() == "1"


@needs_tmux
@pytest.mark.parametrize(
    "arg",
    [
        "plain",
        "a b",
        "it's",
        'dq"',
        "$HOME",
        "~",
        "#{pane_id}",
        "x;",
        ";",
        "{",
        "tab\t",
        "nl\nx",
        "é ✓",
    ],
)
async def test_arguments_survive_the_control_line(runner, arg):
    await runner.run_checked(["new-session", "-d", "-s", "q"])
    await runner.run_checked(["set-buffer", "-b", "q", "--", arg])
    assert runner.control_connected(None)
    assert await runner.run_checked(["show-buffer", "-b", "q"]) == arg  # one-shot


@needs_tmux
async def test_errors_and_batches(runner):
    await runner.run_checked(["new-session", "-d", "-s", "e"])
    bad = await runner.run(["has-session", "-t", "nope"], ["display-message", "-p", "ran"])
    assert runner.control_connected(None)
    assert (bad.exit_code, bad.stdout) == (1, "")
    assert "can't find session: nope" in bad.stderr
    ok = await runner.run(["display-message", "-p", "a"], ["display-message", "-p", "b"])
    assert (ok.exit_code, ok.stdout) == (0, "a\nb\n")


@needs_tmux
async def test_concurrent_calls_get_their_own_replies(runner):
    await runner.run_checked(["new-session", "-d", "-s", "c"])
    outs = await asyncio.gather(
        *(runner.run_checked(["display-message", "-p", f"reply-{i}"]) for i in range(50))
    )
    assert outs == [f"reply-{i}\n" for i in range(50)]


@needs_tmux
async def test_blocking_wait_for_does_not_stall_other_calls(runner):
    await runner.run_checked(["new-session", "-d", "-s", "w"])
    await runner.run_checked(["display-message", "-p", "x"])
    waiter = asyncio.create_task(runner.run(["wait-for", "tr-ch"]))
    await asyncio.sleep(0.2)
    out = await asyncio.wait_for(runner.run_checked(["display-message", "-p", "free"]), 2)
    assert out == "free\n"
    await runner.run_checked(["wait-for", "-S", "tr-ch"])
    assert (await asyncio.wait_for(waiter, 5)).ok


@needs_tmux
async def test_no_server_falls_back_without_starting_one(runner):
    result = await runner.run(["list-sessions"])
    assert not result.ok
    assert not runner.control_connected(None)
    again = await runner.run(["list-sessions"])
    assert "no server running" in again.stderr or "error connecting" in again.stderr


@needs_tmux
async def test_reconnects_after_the_server_restarts(runner):
    await runner.run_checked(["new-session", "-d", "-s", "r1"])
    await runner.run_checked(["display-message", "-p", "x"])
    assert runner.control_connected(None)
    await runner.run_checked(["kill-server"])
    await runner.run_checked(["new-session", "-d", "-s", "r2"])
    out = await runner.run_checked(["display-message", "-p", "-t", "r2", "#{session_name}"])
    assert out == "r2\n"
    assert runner.control_connected(None)


@needs_tmux
async def test_transport_tool_reports_and_switches(runner):
    mcp = build_server(config={**CONFIG, "defaults": {**CONFIG["defaults"], "transport": None}})
    await runner.run_checked(["new-session", "-d", "-s", "tt"])
    rep = _json(await mcp.call_tool("tmux_transport", {}))
    assert rep["transport"] == "oneshot" and rep["configured"] == "oneshot"
    assert rep["connected"] is False
    rep = _json(await mcp.call_tool("tmux_transport", {"transport": "control"}))
    assert rep["transport"] == "control" and rep["configured"] == "oneshot"
    await mcp.call_tool("tmux_list", {"kind": "session"})
    assert _json(await mcp.call_tool("tmux_transport", {}))["connected"] is True
    rep = _json(await mcp.call_tool("tmux_transport", {"transport": "oneshot"}))
    assert rep["connected"] is False


@needs_tmux
async def test_waits_are_woken_by_pane_output(runner):
    mcp = build_server(config=CONFIG)
    await runner.run_checked(["new-session", "-d", "-s", "wo"])
    await mcp.call_tool("tmux_run", {"target_pane": "wo", "command": "true"})

    # A 60s poll would time out: only %output can end these waits in time.
    await mcp.call_tool(
        "tmux_send_keys",
        {"target_pane": "wo", "text": "sleep 0.5; echo WOKE-UP", "enter": True},
    )
    r = _json(
        await mcp.call_tool(
            "tmux_wait_for_text",
            {
                "target_pane": "wo",
                "pattern": r"(?m)^WOKE-UP$",
                "regex": True,
                "timeout": 10,
                "poll_interval": 60,
            },
        )
    )
    assert r["matched"] is True
    assert r["elapsed"] < 5

    await mcp.call_tool(
        "tmux_send_keys",
        {
            "target_pane": "wo",
            "text": "for i in 1 2 3; do echo t$i; sleep 0.2; done",
            "enter": True,
        },
    )
    r = _json(
        await mcp.call_tool(
            "tmux_wait_for_idle",
            {"target_pane": "wo", "idle_seconds": 0.5, "timeout": 10, "poll_interval": 60},
        )
    )
    assert r["idle"] is True
    assert 0.9 <= r["elapsed"] < 5
    assert "t3" in r["content"]

    # The waits' read-only clients detached again; the command clients remain.
    clients = await runner.run_checked(["list-clients", "-F", "#{client_readonly}"])
    assert "1" not in clients.split()
