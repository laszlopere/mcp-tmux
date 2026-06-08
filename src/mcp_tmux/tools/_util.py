"""Post-registration finalizer: tool annotations + structured error mapping.

Applied once after all tools are registered. Rather than threading annotation
flags through 30+ decorators, we tag tools by name here and wrap each tool's
function so a :class:`~mcp_tmux.runner.TmuxError` (or a validation ``ValueError``)
becomes a clean FastMCP ``ToolError`` instead of leaking a traceback.
"""

from __future__ import annotations

import functools

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..runner import TmuxError

# Tools that only read state (no side effects).
READ_ONLY = {
    "tmux_query",
    "tmux_version",
    "tmux_list_targets",
    "tmux_list_sessions",
    "tmux_has_session",
    "tmux_list_windows",
    "tmux_list_panes",
    "tmux_capture_pane",
    "tmux_show_options",
    "tmux_list_buffers",
}

# Tools that destroy state (kill / delete). Clients should confirm these.
DESTRUCTIVE = {
    "tmux_kill_server",
    "tmux_kill_session",
    "tmux_kill_window",
    "tmux_kill_pane",
    "tmux_delete_buffer",
}


def _wrap_errors(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except TmuxError as exc:
            raise ToolError(f"{exc} (tmux exit status {exc.exit_code})") from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _set(obj, name, value) -> None:
    # Tool is a pydantic model; fall back to bypassing validation if needed.
    try:
        setattr(obj, name, value)
    except Exception:  # pragma: no cover - defensive
        object.__setattr__(obj, name, value)


def finalize_tools(mcp) -> None:
    """Apply annotations and error wrapping to every registered tool."""
    for tool in mcp._tool_manager.list_tools():
        _set(tool, "fn", _wrap_errors(tool.fn))
        if tool.name in READ_ONLY:
            _set(tool, "annotations", ToolAnnotations(readOnlyHint=True))
        elif tool.name in DESTRUCTIVE:
            _set(
                tool,
                "annotations",
                ToolAnnotations(readOnlyHint=False, destructiveHint=True),
            )
