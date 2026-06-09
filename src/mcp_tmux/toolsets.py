"""Toolset definitions — gate the curated surface into opt-in groups.

The curated surface is ~60 tools; loading all of them costs ~35k schema tokens
per session and degrades tool-selection accuracy. Toolsets let a session pay
only for what it needs: a lean always-on ``core`` (the create -> send -> read
loop plus the ``tmux_command`` escape hatch, so nothing gated out is ever hard-
blocked) plus opt-in groups selected via the ``toolsets`` config key or the
``MCP_TMUX_TOOLSETS`` env var.

Selection precedence: ``MCP_TMUX_TOOLSETS`` (comma-separated) overrides the
config ``toolsets`` key, which overrides the built-in default
(``core`` + ``automation``). ``["all"]`` keeps the full surface (back-compat).
"""

from __future__ import annotations

import os
from typing import Any

# Always loaded: the create -> send -> read loop + passthrough escape hatch.
CORE: frozenset[str] = frozenset(
    {
        # passthrough
        "tmux_command",
        "tmux_query",
        "tmux_version",
        "tmux_list_targets",
        # sessions
        "tmux_has_session",
        "tmux_new_session",
        # io
        "tmux_send_keys",
        "tmux_capture_pane",
        # windows
        "tmux_new_window",
        # panes
        "tmux_list_panes",
        "tmux_split_window",
        # merged
        "tmux_list",
        "tmux_kill",
        "tmux_rename",
        "tmux_select",
    }
)

# Opt-in groups. A single tool lives in exactly one toolset (or in CORE).
OPTIONAL: dict[str, frozenset[str]] = {
    "automation": frozenset(
        {
            "tmux_wait_for_text",
            "tmux_wait_for_idle",
            "tmux_run",
        }
    ),
    "layout": frozenset(
        {
            "tmux_next_layout",
            "tmux_move_window",
            "tmux_select_layout",
            "tmux_resize_pane",
            "tmux_set_pane_title",
            "tmux_clear_history",
            "tmux_swap",
            "tmux_last",
            "tmux_respawn",
            "tmux_link_window",
            "tmux_unlink_window",
            "tmux_break_pane",
            "tmux_join_pane",
            "tmux_find_window",
            "tmux_pipe_pane",
        }
    ),
    "buffers": frozenset(
        {
            "tmux_set_buffer",
            "tmux_paste_buffer",
            "tmux_delete_buffer",
            "tmux_save_buffer",
            "tmux_load_buffer",
        }
    ),
    "config": frozenset(
        {
            "tmux_set_option",
            "tmux_show_options",
            "tmux_set_environment",
            "tmux_show_environment",
            "tmux_set_hook",
            "tmux_show_hooks",
            "tmux_run_shell",
            "tmux_if_shell",
        }
    ),
    "keybindings": frozenset(
        {
            "tmux_list_keys",
            "tmux_bind_key",
            "tmux_unbind_key",
        }
    ),
    "copymode": frozenset(
        {
            "tmux_copy_mode",
            "tmux_copy_scroll",
            "tmux_copy_search",
        }
    ),
    "clients": frozenset(
        {
            "tmux_server_info",
            "tmux_display_message",
        }
    ),
    "stream": frozenset(
        {
            "tmux_stream_start",
            "tmux_stream_resize",
            "tmux_stream_read",
            "tmux_stream_send",
            "tmux_stream_list",
            "tmux_stream_stop",
        }
    ),
}

# Full registry including core, keyed by toolset name.
TOOLSETS: dict[str, frozenset[str]] = {"core": CORE, **OPTIONAL}

# Default when nothing is configured: core essentials + agent automation.
DEFAULT_TOOLSETS: list[str] = ["core", "automation"]


def select_toolsets(config: dict[str, Any] | None = None) -> list[str] | None:
    """Resolve the *selected* toolset names from env then config.

    ``MCP_TMUX_TOOLSETS`` (comma-separated) wins; then the config ``toolsets``
    key; then ``None`` so :func:`resolve_enabled` applies the built-in default.
    """
    env = os.environ.get("MCP_TMUX_TOOLSETS")
    if env is not None:
        parts = [p.strip() for p in env.split(",") if p.strip()]
        return parts or None
    if config:
        selected = config.get("toolsets")
        if selected is not None:
            return list(selected)
    return None


def resolve_enabled(selected: list[str] | None) -> set[str]:
    """Map selected toolset names to the concrete set of active tool names.

    ``core`` is always included. ``None`` (or empty) applies the default set.
    ``"all"`` (anywhere in the list) enables every toolset. An unknown name
    raises ``ValueError`` listing the valid toolsets.
    """
    names = list(selected) if selected else list(DEFAULT_TOOLSETS)
    if "all" in names:
        return set().union(*TOOLSETS.values())
    enabled: set[str] = set(CORE)
    for name in names:
        if name not in TOOLSETS:
            valid = ", ".join(sorted(TOOLSETS) + ["all"])
            raise ValueError(f"unknown toolset {name!r}; valid toolsets: {valid}")
        enabled |= TOOLSETS[name]
    return enabled
