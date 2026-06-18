"""Tolerate LLM-mangled JSON in inbound tool calls.

LLMs sometimes send a tool call's ``arguments`` as broken JSON — most often the
whole object arrives double-encoded as a JSON *string* (``"{\\"text\\": \\"hi\\"}"``),
or with single quotes, trailing commas, or unquoted barewords. By default such a
call dies in the MCP SDK's strict validation before any tool code runs, and the
model gets a cryptic protocol error it cannot act on.

Two fixes live here, at the two layers where the failures happen:

* :func:`run_stdio_repaired` interposes the stdio read stream and repairs a
  stringified/loosely-quoted ``arguments`` blob *before* the session's strict
  ``ClientRequest`` validation (the only place those bytes are still a repairable
  string). Unrepairable junk gets an actionable ``-32700`` reply instead of a
  bare protocol error.
* :class:`TolerantFastMCP` reshapes FastMCP's per-tool pydantic ``ValidationError``
  (well-formed JSON, wrong shape) into a plain-English message naming the field
  and the expected-vs-received type, so the model self-corrects in one turn.

No SDK types are monkeypatched; this relies only on stable public message shapes.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anyio
from json_repair import loads as repair_loads
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage
from mcp.types import (
    PARSE_ERROR,
    ContentBlock,
    ErrorData,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    RequestId,
)
from pydantic import ValidationError

# --------------------------------------------------------------------------- #
# Fix A — tolerant parse of a stringified / loosely-quoted ``arguments`` blob   #
# --------------------------------------------------------------------------- #


def repair_arguments(arguments: Any) -> Any:
    """Return ``arguments`` as a dict, repairing LLM-mangled JSON if needed.

    A non-string (already an object) is returned untouched. A string is tried
    strictly first (cheap, the common case) and only then run through
    ``json-repair``. The original value is handed back on failure so a real
    error is never masked by silent garbage — ``json-repair`` coerces
    unsalvageable junk to ``""``/``{}`` rather than raising.
    """
    if not isinstance(arguments, str):
        return arguments  # already an object -> untouched
    try:
        parsed = json.loads(arguments)  # strict first (cheap, common)
    except ValueError:
        try:
            parsed = repair_loads(arguments)
        except ValueError:
            return arguments
    return parsed if isinstance(parsed, dict) else arguments


def _repair_message(message: SessionMessage) -> SessionMessage:
    """Repair a ``tools/call`` request's stringified ``arguments`` in place.

    Returns the original message unchanged for anything that is not a
    tools/call request carrying a string ``arguments`` field, or when the blob
    cannot be repaired into an object (left for :func:`parse_error_reply`).
    """
    root = message.message.root
    if not isinstance(root, JSONRPCRequest) or root.method != "tools/call":
        return message
    params = root.params
    if not isinstance(params, dict) or not isinstance(params.get("arguments"), str):
        return message
    fixed = repair_arguments(params["arguments"])
    if not isinstance(fixed, dict):
        return message
    new_root = root.model_copy(update={"params": {**params, "arguments": fixed}})
    return SessionMessage(message=JSONRPCMessage(new_root), metadata=message.metadata)


def _needs_parse_error(message: SessionMessage) -> RequestId | None:
    """Return the request id if this is a tools/call whose ``arguments`` is an
    unrepairable JSON string, else ``None``."""
    root = message.message.root
    if not isinstance(root, JSONRPCRequest) or root.method != "tools/call":
        return None
    params = root.params
    if not isinstance(params, dict) or not isinstance(params.get("arguments"), str):
        return None
    if isinstance(repair_arguments(params["arguments"]), dict):
        return None
    return root.id


def parse_error_reply(request_id: RequestId, tool_name: str | None) -> SessionMessage:
    """Build an actionable ``-32700`` reply for an unrepairable ``arguments`` blob."""
    name = repr(tool_name) if tool_name else "the tool"
    msg = (
        f"The 'arguments' for tool {name} were not valid JSON. "
        'Send `arguments` as a JSON object -- e.g. {"a": 1} -- not a quoted '
        "string; check for unbalanced braces, single quotes, or missing quotes "
        "around keys/values."
    )
    err = JSONRPCError(jsonrpc="2.0", id=request_id, error=ErrorData(code=PARSE_ERROR, message=msg))
    return SessionMessage(message=JSONRPCMessage(err))


def _tool_name(message: SessionMessage) -> str | None:
    root = message.message.root
    if isinstance(root, JSONRPCRequest) and isinstance(root.params, dict):
        name = root.params.get("name")
        if isinstance(name, str):
            return name
    return None


async def run_stdio_repaired(mcp: FastMCP) -> None:
    """Run ``mcp`` over stdio, repairing mangled tool-call ``arguments`` inbound.

    Replaces ``FastMCP.run()``'s stdio path with one that interposes the read
    stream: each ``tools/call`` message is repaired before the session's strict
    validation sees it. Unrepairable blobs are answered directly with an
    actionable parse error and never forwarded, so the session never sees that id.
    """
    async with stdio_server() as (read_stream, write_stream):
        send, recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)

        async def pump() -> None:
            async with send:
                async for item in read_stream:
                    if isinstance(item, SessionMessage):
                        bad_id = _needs_parse_error(item)
                        if bad_id is not None:
                            # Answer the client ourselves; don't forward the
                            # request, so the session never double-answers its id.
                            await write_stream.send(parse_error_reply(bad_id, _tool_name(item)))
                            continue
                        item = _repair_message(item)
                    await send.send(item)

        async with anyio.create_task_group() as tg, recv:
            tg.start_soon(pump)
            await mcp._mcp_server.run(
                recv,
                write_stream,
                mcp._mcp_server.create_initialization_options(),
            )
            tg.cancel_scope.cancel()


# --------------------------------------------------------------------------- #
# Fix B — reshape FastMCP's per-tool pydantic ValidationError into plain text   #
# --------------------------------------------------------------------------- #

_EXPECTED = {
    "string_type": "a string",
    "int_type": "an integer",
    "int_parsing": "an integer",
    "float_type": "a number",
    "float_parsing": "a number",
    "bool_type": "a boolean",
    "bool_parsing": "a boolean",
    "list_type": "an array",
    "dict_type": "an object",
}


def format_validation_error(tool_name: str, exc: ValidationError) -> str:
    """Turn a pydantic ``ValidationError`` into a single actionable sentence.

    Built from ``exc.errors()`` (structured) rather than ``str(exc)`` so the
    ``errors.pydantic.dev`` URL and stack noise never leak to the model.
    """
    parts: list[str] = []
    for e in exc.errors():
        field = ".".join(map(str, e.get("loc", ()))) or "arguments"
        if e["type"] == "missing":
            parts.append(f"argument {field!r} is required but was not provided")
            continue
        got = e.get("input")
        want = _EXPECTED.get(e["type"])
        if want:
            parts.append(
                f"argument {field!r} expected {want}, but received {got!r} ({type(got).__name__})"
            )
        else:
            parts.append(f"argument {field!r}: {e.get('msg', 'invalid value')} (received {got!r})")
    return f"Invalid arguments for tool {tool_name!r}: {'; '.join(parts)}."


class TolerantFastMCP(FastMCP):
    """FastMCP whose tool-call validation errors read as plain English.

    Only pydantic ``ValidationError``s (wrong-shape arguments) are rewritten;
    errors raised from inside a tool body have a different ``__cause__`` and pass
    through untouched.
    """

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                raise ToolError(format_validation_error(name, exc.__cause__)) from exc.__cause__
            raise
