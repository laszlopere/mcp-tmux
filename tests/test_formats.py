from mcp_tmux.capabilities import Capabilities
from mcp_tmux.formats import (
    FIELD_SEP,
    PANE_FIELDS,
    SESSION_FIELDS,
    build_format,
    parse_records,
)


def test_build_format_gates_new_fields():
    old = Capabilities.from_output("tmux 1.8")
    fmt_old, keys_old = build_format(PANE_FIELDS, old)
    # current_path is gated to 1.9 and must be dropped on 1.8
    assert "current_path" not in keys_old
    assert "pane_current_path" not in fmt_old

    new = Capabilities.from_output("tmux 3.4")
    _, keys_new = build_format(PANE_FIELDS, new)
    assert "current_path" in keys_new


def test_build_format_uses_field_separator():
    fmt, keys = build_format(SESSION_FIELDS, Capabilities.from_output("tmux 3.4"))
    assert FIELD_SEP in fmt
    assert keys[0] == "id"
    assert fmt.startswith("#{session_id}")


def test_parse_records_roundtrip():
    keys = ["id", "name", "windows"]
    line1 = FIELD_SEP.join(["$0", "main", "3"])
    line2 = FIELD_SEP.join(["$1", "work", "1"])
    records = parse_records(f"{line1}\n{line2}\n", keys)
    assert records == [
        {"id": "$0", "name": "main", "windows": "3"},
        {"id": "$1", "name": "work", "windows": "1"},
    ]


def test_parse_records_pads_short_rows():
    keys = ["a", "b", "c"]
    records = parse_records(f"x{FIELD_SEP}y\n", keys)
    assert records == [{"a": "x", "b": "y", "c": ""}]


def test_parse_records_ignores_blank_lines():
    assert parse_records("\n\n", ["a"]) == []
