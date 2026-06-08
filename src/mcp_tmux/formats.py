"""Structured introspection via tmux format strings.

List/inspect commands emit one record per line using ``-F`` with an explicit
field separator, so we parse stable machine output instead of scraping tmux's
human-readable layout. Field sets are version-gated through
:class:`~mcp_tmux.capabilities.Capabilities`, and known numeric/boolean fields
are coerced from strings to ``int``/``bool``.
"""

from __future__ import annotations

from .capabilities import Capabilities

# Field separator. tmux escapes control bytes (e.g. 0x1f -> "\037") in format
# output but passes a literal TAB through unchanged, and tmux ids/indices/names/
# paths effectively never contain tabs — so TAB is the robust delimiter here.
FIELD_SEP = "\t"

# Each entry: (json_key, tmux_format_var, capability_gate, kind).
# kind is one of "str", "int", "bool" and drives coercion in coerce_records().
SESSION_FIELDS = [
    ("id", "session_id", None, "str"),
    ("name", "session_name", None, "str"),
    ("windows", "session_windows", None, "int"),
    ("attached", "session_attached", None, "bool"),
    ("created", "session_created", "session_created", "int"),
]

WINDOW_FIELDS = [
    ("id", "window_id", None, "str"),
    ("index", "window_index", None, "int"),
    ("name", "window_name", None, "str"),
    ("active", "window_active", None, "bool"),
    ("panes", "window_panes", None, "int"),
    ("layout", "window_layout", None, "str"),
]

CLIENT_FIELDS = [
    ("name", "client_name", "client_name", "str"),
    ("tty", "client_tty", None, "str"),
    ("session", "client_session", None, "str"),
    ("width", "client_width", None, "int"),
    ("height", "client_height", None, "int"),
]

PANE_FIELDS = [
    ("id", "pane_id", None, "str"),
    ("index", "pane_index", None, "int"),
    ("active", "pane_active", None, "bool"),
    ("title", "pane_title", None, "str"),
    ("pid", "pane_pid", "pane_pid", "int"),
    ("width", "pane_width", None, "int"),
    ("height", "pane_height", None, "int"),
    ("current_command", "pane_current_command", None, "str"),
    ("current_path", "pane_current_path", "pane_current_path", "str"),
]

# A field definition tuple as used above.
Field = tuple[str, str, "str | None", str]


def _active_fields(fields: list[Field], caps: Capabilities | None) -> list[tuple[str, str, str]]:
    """Return active ``(key, var, kind)`` triples, dropping gated-out fields."""
    out: list[tuple[str, str, str]] = []
    for key, var, gate, kind in fields:
        if gate is not None and caps is not None and not caps.has(gate):
            continue
        out.append((key, var, kind))
    return out


def build_format(fields: list[Field], caps: Capabilities | None = None) -> tuple[str, list[str]]:
    """Return ``(format_string, ordered_keys)`` for the active fields."""
    active = _active_fields(fields, caps)
    keys = [k for k, _, _ in active]
    fmt = FIELD_SEP.join(f"#{{{var}}}" for _, var, _ in active)
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


def _to_int(value: str) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value  # leave unparseable values untouched


def coerce_records(
    records: list[dict[str, str]], fields: list[Field], caps: Capabilities | None = None
) -> list[dict[str, object]]:
    """Coerce known numeric/boolean fields from strings to int/bool."""
    kinds = {k: kind for k, _, kind in _active_fields(fields, caps)}
    out: list[dict[str, object]] = []
    for rec in records:
        new: dict[str, object] = dict(rec)
        for key, kind in kinds.items():
            if key not in new:
                continue
            if kind == "int":
                new[key] = _to_int(rec[key])
            elif kind == "bool":
                new[key] = rec[key] == "1"
        out.append(new)
    return out
