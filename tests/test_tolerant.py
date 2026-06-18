"""Tolerant-JSON handling for LLM-mangled tool-call arguments.

Pure-function unit tests for the repair + error-reshaping helpers, plus one
honest raw-stdio end-to-end (the typed ClientSession can't send malformed args —
it serializes a dict — so we speak newline-delimited JSON-RPC by hand).
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys

import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest
from pydantic import BaseModel, ValidationError

from mcp_tmux.tolerant import (
    _needs_parse_error,
    _repair_message,
    format_validation_error,
    parse_error_reply,
    repair_arguments,
)

# --------------------------------------------------------------------------- #
# repair_arguments — the four offenders + the garbage guard                     #
# --------------------------------------------------------------------------- #


def test_repair_passes_through_objects_untouched():
    obj = {"expression": "1+2"}
    assert repair_arguments(obj) is obj


def test_repair_strict_json_string():
    assert repair_arguments('{"expression": "1+2"}') == {"expression": "1+2"}


def test_repair_single_quotes():
    assert repair_arguments("{'expression': '1+2'}") == {"expression": "1+2"}


def test_repair_trailing_comma():
    assert repair_arguments('{"expression": "1+2",}') == {"expression": "1+2"}


def test_repair_bareword_value():
    # json5 would NOT fix this; json-repair quotes the bareword value.
    assert repair_arguments('{"variable": n}') == {"variable": "n"}


def test_repair_unsalvageable_returns_original():
    # json-repair coerces junk to ""/{} rather than raising; we hand the
    # ORIGINAL back so a real error is never masked by silent garbage.
    assert repair_arguments("not json at all %%%") == "not json at all %%%"


def test_repair_non_dict_json_returns_original():
    # A valid JSON array is not a tool-arguments object.
    assert repair_arguments("[1, 2, 3]") == "[1, 2, 3]"


# --------------------------------------------------------------------------- #
# message router — _repair_message / _needs_parse_error                          #
# --------------------------------------------------------------------------- #


def _call_msg(arguments, *, name="tmux_list_targets", req_id=1):
    root = JSONRPCRequest(
        jsonrpc="2.0",
        id=req_id,
        method="tools/call",
        params={"name": name, "arguments": arguments},
    )
    return SessionMessage(message=JSONRPCMessage(root))


def test_repair_message_rewrites_stringified_arguments():
    msg = _call_msg('{"target": "local"}')
    repaired = _repair_message(msg)
    root = repaired.message.root
    assert isinstance(root, JSONRPCRequest)
    assert root.params["arguments"] == {"target": "local"}


def test_repair_message_leaves_object_arguments_untouched():
    msg = _call_msg({"target": "local"})
    assert _repair_message(msg) is msg


def test_repair_message_ignores_non_tool_calls():
    root = JSONRPCRequest(jsonrpc="2.0", id=1, method="tools/list", params={})
    msg = SessionMessage(message=JSONRPCMessage(root))
    assert _repair_message(msg) is msg


def test_needs_parse_error_only_for_unrepairable_strings():
    assert _needs_parse_error(_call_msg("garbage %%%", req_id=7)) == 7
    assert _needs_parse_error(_call_msg('{"a": 1}')) is None
    assert _needs_parse_error(_call_msg({"a": 1})) is None


def test_parse_error_reply_is_actionable_and_clean():
    reply = parse_error_reply(7, "tmux_list_targets")
    err = reply.message.root.error
    assert err.code == -32700
    assert "tmux_list_targets" in err.message
    assert "JSON object" in err.message
    assert "pydantic" not in err.message


# --------------------------------------------------------------------------- #
# format_validation_error — wrong-shape arguments, no pydantic noise            #
# --------------------------------------------------------------------------- #


class _Model(BaseModel):
    expression: str
    count: int


def _validation_error(**kwargs):
    try:
        _Model(**kwargs)
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


def test_format_missing_field():
    exc = _validation_error(count=1)
    msg = format_validation_error("calculate", exc)
    assert "argument 'expression' is required but was not provided" in msg
    assert msg.startswith("Invalid arguments for tool 'calculate':")


def test_format_wrong_type_shows_expected_and_received():
    exc = _validation_error(expression=123, count=1)
    msg = format_validation_error("calculate", exc)
    assert "argument 'expression' expected a string, but received 123 (int)" in msg


def test_format_never_leaks_pydantic_url():
    exc = _validation_error(expression=123, count="nope")
    msg = format_validation_error("calculate", exc)
    assert "pydantic" not in msg
    assert "https://" not in msg


# --------------------------------------------------------------------------- #
# end-to-end — raw newline-delimited JSON-RPC over stdio                        #
# --------------------------------------------------------------------------- #


def _read_message(proc, timeout=10.0):
    """Read one JSON-RPC line from the server's stdout, or fail on timeout."""
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise AssertionError("server produced no output before timeout")
    line = proc.stdout.readline()
    assert line, "server closed stdout"
    return json.loads(line)


@pytest.fixture()
def server(tmp_path):
    env = {
        **os.environ,
        "MCP_TMUX_TOOLSETS": "all",
        # Point at an empty config so the test never reads the user's real one.
        "MCP_TMUX_CONFIG": str(tmp_path / "none.toml"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_tmux"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        bufsize=1,
    )
    # Handshake: initialize -> initialized.
    proc.stdin.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "raw-test", "version": "0"},
                },
            }
        )
        + "\n"
    )
    proc.stdin.flush()
    _read_message(proc)  # initialize result
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    yield proc
    proc.stdin.close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _send_raw_call(proc, req_id, arguments_literal):
    """Send a tools/call whose `arguments` value is injected as a raw JSON
    literal (so we can make it a STRING, which a typed client never would)."""
    line = (
        f'{{"jsonrpc": "2.0", "id": {req_id}, "method": "tools/call", '
        f'"params": {{"name": "tmux_list_targets", "arguments": {arguments_literal}}}}}\n'
    )
    proc.stdin.write(line)
    proc.stdin.flush()


def test_e2e_stringified_arguments_are_repaired(server):
    # arguments arrives as a JSON *string* (double-encoded) — the #1 offender.
    _send_raw_call(server, 1, json.dumps("{}"))
    resp = _read_message(server)
    assert resp["id"] == 1
    assert "error" not in resp, resp
    # The tool ran: its result lists the "local" target.
    text = resp["result"]["content"][0]["text"]
    assert "local" in json.loads(text)["targets"]


def test_e2e_unrepairable_arguments_get_actionable_parse_error(server):
    _send_raw_call(server, 2, json.dumps("not json at all %%%"))
    resp = _read_message(server)
    assert resp["id"] == 2
    assert resp["error"]["code"] == -32700
    assert "tmux_list_targets" in resp["error"]["message"]
    assert "pydantic" not in resp["error"]["message"]
