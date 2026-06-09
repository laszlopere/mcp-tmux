"""End-to-end functional test: one full agent workflow through the MCP tools.

A single coherent scenario exercises the whole surface together — sessions,
window/pane plumbing, send-keys + ``tmux_run`` (P1), capture/wait, control-mode
streaming (P3), a target-aware resource, and clean teardown — driven exactly as
a client would, via ``mcp.call_tool`` / ``mcp.read_resource``.

Two entry points run the *same* workflow:

* ``test_functional_local`` — always runs (skipped only if tmux is absent). It
  owns a throwaway session on an isolated ``-L`` socket, so it is hermetic and
  CI-safe.
* ``test_functional_remote`` — opt-in. Set ``MCP_TMUX_FUNC_TARGET`` (an ssh
  destination or named profile, e.g. ``pipnode``) to run it. ``MCP_TMUX_FUNC_SESSION``
  optionally borrows an existing session (e.g. ``testMCP``) instead of creating
  one; a borrowed session is never killed and its active window is restored.

    MCP_TMUX_FUNC_TARGET=pipnode MCP_TMUX_FUNC_SESSION=testMCP \\
        pytest tests/test_functional.py -k remote
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from mcp_tmux.runner import TmuxRunner
from mcp_tmux.server import build_server

HAVE_TMUX = shutil.which("tmux") is not None
LOCAL_CONFIG = {
    "defaults": {"socket_name": "mcp-tmux-func", "timeout": 15},
    "targets": {},
    "toolsets": ["all"],
}

REMOTE_TARGET = os.environ.get("MCP_TMUX_FUNC_TARGET")
REMOTE_SESSION = os.environ.get("MCP_TMUX_FUNC_SESSION")


def _js(res):
    payload = res[0] if isinstance(res, tuple) else res
    return json.loads(payload[0].text)


def _resource_json(res):
    payload = res[0] if isinstance(res, (list, tuple)) else res
    return json.loads(payload.content)


async def _run_full_workflow(mcp, *, target, session, owns_session) -> None:
    """Drive the complete agent workflow within ``session`` on ``target``.

    Creates its own dedicated window for the destructive pane work so that a
    *borrowed* session (``owns_session=False``) is left as it was found.
    """
    tkw = {"target": target} if target is not None else {}

    async def t(name, args):  # target-aware tool call
        return _js(await mcp.call_tool(name, {**args, **tkw}))

    async def t0(name, args):  # tool with no `target` param (the stream_* readers)
        return _js(await mcp.call_tool(name, args))

    # Remember the session's active window so we can restore a borrowed session.
    orig_active = None
    if not owns_session:
        orig_active = (await t("tmux_query", {"format": "#{window_id}", "target_pane": session}))[
            "value"
        ]

    wid = None
    try:
        # --- window / pane plumbing -----------------------------------------
        win = await t("tmux_new_window", {"session": session, "name": "functest", "select": True})
        assert win["name"] == "functest" and win["id"]
        wid = win["id"]

        windows = await t("tmux_list", {"kind": "window", "scope": session})
        assert any(w["id"] == wid for w in windows["items"])

        split = await t("tmux_split_window", {"target_pane": wid})
        assert split["id"]

        panes = await t("tmux_list_panes", {"window": wid})
        assert len(panes["panes"]) >= 2

        # --- run-and-collect (P1): output + exit code -----------------------
        ok = await t(
            "tmux_run",
            {"target_pane": wid, "command": "echo func-OK && echo done", "timeout": 20},
        )
        assert ok["completed"] is True
        assert ok["exit_code"] == 0
        assert ok["output"].splitlines() == ["func-OK", "done"]

        bad = await t("tmux_run", {"target_pane": wid, "command": "false", "timeout": 20})
        assert bad["completed"] is True and bad["exit_code"] == 1

        # --- send-keys + wait-for-text + capture ----------------------------
        await t(
            "tmux_send_keys",
            {"target_pane": wid, "text": "echo WAIT-MARKER-XYZ", "enter": True},
        )
        waited = await t(
            "tmux_wait_for_text",
            {"target_pane": wid, "pattern": "WAIT-MARKER-XYZ", "timeout": 15},
        )
        assert waited["matched"] is True

        cap = await t("tmux_capture_pane", {"target_pane": wid})
        assert "WAIT-MARKER-XYZ" in cap["content"]

        # --- control-mode streaming (P3) ------------------------------------
        # Our functest window must be active for its panes to stream.
        await t("tmux_select", {"kind": "window", "id": wid})
        stream = await t("tmux_stream_start", {"session": session})
        sid = stream["stream_id"]
        try:
            await t0("tmux_stream_read", {"stream_id": sid, "timeout": 3})  # drain
            await t(
                "tmux_send_keys",
                {"target_pane": wid, "text": "echo STREAM-MARKER-99", "enter": True},
            )
            seen = ""
            for _ in range(10):
                r = await t0(
                    "tmux_stream_read",
                    {"stream_id": sid, "timeout": 3, "kinds": ["output"]},
                )
                seen += "".join(e.get("data", "") for e in r["events"])
                if "STREAM-MARKER-99" in seen:
                    break
            assert "STREAM-MARKER-99" in seen

            reply = await t0("tmux_stream_send", {"stream_id": sid, "command": "list-windows"})
            assert "(active)" in reply["reply"]

            listed = await t0("tmux_stream_list", {})
            assert any(s["stream_id"] == sid for s in listed["streams"])
        finally:
            await t0("tmux_stream_stop", {"stream_id": sid})
        gone = await t0("tmux_stream_list", {})
        assert all(s["stream_id"] != sid for s in gone["streams"])

        # --- target-aware resource ------------------------------------------
        uri = f"tmux://{target or 'local'}/{session}/windows"
        data = _resource_json(await mcp.read_resource(uri))
        assert data["target"] == (target or "local")
        assert any(w["id"] == wid for w in data["windows"])
    finally:
        # Remove our window; restore a borrowed session's active window.
        if wid is not None:
            try:
                await t("tmux_kill", {"kind": "window", "id": wid})
            except Exception:
                pass
        if orig_active:
            try:
                await t("tmux_select", {"kind": "window", "id": orig_active})
            except Exception:
                pass


@pytest.mark.skipif(not HAVE_TMUX, reason="tmux not installed")
async def test_functional_local():
    runner = TmuxRunner(LOCAL_CONFIG)
    mcp = build_server(config=LOCAL_CONFIG)
    session = "functest-local"
    try:
        created = _js(await mcp.call_tool("tmux_new_session", {"name": session, "detached": True}))
        assert created["name"] == session
        await _run_full_workflow(mcp, target=None, session=session, owns_session=True)
    finally:
        # Isolated socket: nuking the whole server is safe and complete.
        await runner.run(["kill-server"])


@pytest.mark.skipif(not REMOTE_TARGET, reason="set MCP_TMUX_FUNC_TARGET to run the remote workflow")
async def test_functional_remote():
    config = {"defaults": {"timeout": 25}, "targets": {}, "toolsets": ["all"]}
    runner = TmuxRunner(config)
    mcp = build_server(config=config)
    target = REMOTE_TARGET
    owns_session = REMOTE_SESSION is None
    session = REMOTE_SESSION or f"mcptf-{os.getpid()}"
    created = False
    try:
        if owns_session:
            _js(
                await mcp.call_tool(
                    "tmux_new_session",
                    {"name": session, "detached": True, "target": target},
                )
            )
            created = True
        await _run_full_workflow(mcp, target=target, session=session, owns_session=owns_session)
    finally:
        # SAFETY: never kill the remote server. Only kill a session we created.
        if created:
            await runner.run(["kill-session", "-t", session], target=target)
