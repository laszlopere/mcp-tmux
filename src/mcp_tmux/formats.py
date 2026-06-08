"""Structured introspection via tmux format strings.

List/inspect commands emit one record per line using ``-F`` with an explicit
field separator, so we parse stable machine output instead of scraping tmux's
human-readable layout. Field sets are version-gated through
:class:`~mcp_tmux.capabilities.Capabilities`.
"""

from __future__ import annotations

from .capabilities import Capabilities

# Field separator. tmux escapes control bytes (e.g. 0x1f -> "\037") in format
# output but passes a literal TAB through unchanged, and tmux ids/indices/names/
# paths effectively never contain tabs — so TAB is the robust delimiter here.
FIELD_SEP = "\t"

# Each entry: (json_key, tmux_format_var, optional capability gate).
SESSION_FIELDS = [
    ("id", "session_id", None),
    ("name", "session_name", None),
    ("windows", "session_windows", None),
    ("attached", "session_attached", None),
    ("created", "session_created", "session_created"),
]

WINDOW_FIELDS = [
    ("id", "window_id", None),
    ("index", "window_index", None),
    ("name", "window_name", None),
    ("active", "window_active", None),
    ("panes", "window_panes", None),
    ("layout", "window_layout", None),
]

PANE_FIELDS = [
    ("id", "pane_id", None),
    ("index", "pane_index", None),
    ("active", "pane_active", None),
    ("title", "pane_title", None),
    ("pid", "pane_pid", "pane_pid"),
    ("width", "pane_width", None),
    ("height", "pane_height", None),
    ("current_command", "pane_current_command", None),
    ("current_path", "pane_current_path", "pane_current_path"),
]


def _active_fields(
    fields: list[tuple[str, str, str | None]], caps: Capabilities | None
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, var, gate in fields:
        if gate is not None and caps is not None and not caps.has(gate):
            continue
        out.append((key, var))
    return out


def build_format(
    fields: list[tuple[str, str, str | None]], caps: Capabilities | None = None
) -> tuple[str, list[str]]:
    """Return ``(format_string, ordered_keys)`` for the active fields."""
    active = _active_fields(fields, caps)
    keys = [k for k, _ in active]
    fmt = FIELD_SEP.join(f"#{{{var}}}" for _, var in active)
    return fmt, keys


def parse_records(stdout: str, keys: list[str]) -> list[dict[str, str]]:
    """Parse ``-F``-formatted output (one record per line) into dicts."""
    records: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line:
            continue
        parts = line.split(FIELD_SEP)
        # Pad short rows defensively (a field could legitimately be empty).
        if len(parts) < len(keys):
            parts += [""] * (len(keys) - len(parts))
        records.append({k: parts[i] for i, k in enumerate(keys)})
    return records
