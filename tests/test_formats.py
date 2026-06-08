from mcp_tmux.capabilities import Capabilities
from mcp_tmux.formats import (
    FIELD_SEP,
    PANE_FIELDS,
    SESSION_FIELDS,
    build_format,
    coerce_records,
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


def test_coerce_records_types_numbers_and_bools():
    caps = Capabilities.from_output("tmux 3.4")
    raw = [{"id": "$0", "name": "main", "windows": "3", "attached": "1", "created": "1780905518"}]
    coerced = coerce_records(raw, SESSION_FIELDS, caps)
    rec = coerced[0]
    assert rec["id"] == "$0"  # str stays str
    assert rec["name"] == "main"
    assert rec["windows"] == 3  # int
    assert rec["attached"] is True  # bool ("1" -> True)
    assert rec["created"] == 1780905518


def test_coerce_records_bool_false_and_bad_int():
    caps = Capabilities.from_output("tmux 3.4")
    raw = [{"id": "$1", "name": "x", "windows": "notanumber", "attached": "0", "created": "0"}]
    rec = coerce_records(raw, SESSION_FIELDS, caps)[0]
    assert rec["attached"] is False
    assert rec["windows"] == "notanumber"  # unparseable int left as-is
    assert rec["created"] == 0


def test_coerce_records_skips_gated_out_field():
    # On 1.8 pane_current_path is gated out, so coercion must not touch/require it.
    caps = Capabilities.from_output("tmux 1.8")
    raw = [{"id": "%0", "index": "0", "active": "1", "pid": "1234", "width": "80", "height": "24"}]
    rec = coerce_records(raw, PANE_FIELDS, caps)[0]
    assert rec["index"] == 0
    assert rec["active"] is True
    assert rec["pid"] == 1234
    assert "current_path" not in rec
