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
    "tmux_list",
    "tmux_has_session",
    "tmux_list_panes",
    "tmux_capture_pane",
    "tmux_show_options",
    "tmux_show_environment",
    "tmux_wait_for_text",
    "tmux_wait_for_idle",
    "tmux_server_info",
    "tmux_find_window",
    "tmux_show_hooks",
    "tmux_list_keys",
    "tmux_stream_read",
    "tmux_stream_list",
}

# Tools that destroy state (kill / delete). Clients should confirm these.
DESTRUCTIVE = {
    "tmux_kill",
    "tmux_delete_buffer",
    "tmux_clear_history",
}


def toolset_gate(mcp, enabled):
    """Return a drop-in replacement for ``mcp.tool()`` that gates by toolset.

    Each module registers its tools through ``tool = toolset_gate(mcp, enabled)``
    and ``@tool()`` instead of ``@mcp.tool()``. The decorator registers the
    function as a tool only when its ``__name__`` is in the active ``enabled``
    set; otherwise it leaves the function untouched and unregistered. This keeps
    gating localized to a one-line change per module while supporting modules
    whose tools span several toolsets.
    """

    def gate(*args, **kwargs):
        def deco(fn):
            if fn.__name__ in enabled:
                return mcp.tool(*args, **kwargs)(fn)
            return fn

        return deco

    return gate


def require_kind(kind: str, allowed) -> str:
    """Validate a `kind` discriminator against an allow-list.

    Raises ``ValueError`` (mapped to ``ToolError`` by :func:`_wrap_errors`) on a
    bad value. Returns the validated kind so callers can use it inline.
    """
    if kind not in allowed:
        allowed_str = ", ".join(sorted(allowed))
        raise ValueError(f"kind must be one of: {allowed_str} (got {kind!r})")
    return kind


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
