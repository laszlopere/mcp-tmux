"""Unit tests for the tools layer: exact tmux argv assembly per tool.

These tests need no tmux binary. A :class:`FakeRunner` stands in for
:class:`~mcp_tmux.runner.TmuxRunner`, recording every ``run`` / ``run_checked``
/ ``list_records`` call so we can assert the precise argument vector each tool
hands to tmux — flags, ordering, version-gated spellings, the ``-P -F`` format
strings, target plumbing, and the validation/error branches. The real
end-to-end behavior is covered by the integration suites (test_p5, test_p2,
test_integration); this layer pins the argv contract cheaply and deterministically.

wait/stream/control tools have dedicated test files (timing/polling driven) and
are not re-covered here.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mcp_tmux.capabilities import Capabilities
from mcp_tmux.formats import FIELD_SEP as SEP
from mcp_tmux.runner import TmuxResult
from mcp_tmux.tools import register_all


class FakeRunner:
    """Records tmux invocations instead of executing them.

    `version` drives the (real) Capabilities so version-gating branches are
    exercised faithfully. `output` is returned by every ``run_checked``;
    `records` by every ``list_records``; ``run`` returns a result whose
    exit code is 0 unless `run_ok=False`.
    """

    def __init__(self, version="3.4", *, output="", records=None, run_ok=True):
        self.config = {"targets": {}}
        self.calls: list[tuple[str, list[str] | None, str | None]] = []
        self._caps = Capabilities.from_output(f"tmux {version}")
        self.output = output
        self.records = records or []
        self.run_result = TmuxResult("", "", 0 if run_ok else 1)

    async def capabilities(self, target=None):
        self.calls.append(("capabilities", None, target))
        return self._caps

    async def run(self, tmux_args, *, target=None, timeout=None):
        self.calls.append(("run", list(tmux_args), target))
        return self.run_result

    async def run_checked(self, tmux_args, *, target=None, timeout=None):
        self.calls.append(("run_checked", list(tmux_args), target))
        return self.output

    async def list_records(self, list_cmd, fields, *, target=None):
        self.calls.append(("list_records", list(list_cmd), target))
        return list(self.records)

    # -- assertion helpers ------------------------------------------------
    @property
    def argvs(self) -> list[list[str]]:
        """Every tmux argv passed to run/run_checked, in call order."""
        return [c[1] for c in self.calls if c[0] in ("run", "run_checked")]

    @property
    def checked(self) -> list[list[str]]:
        return [c[1] for c in self.calls if c[0] == "run_checked"]

    @property
    def listed(self) -> list[list[str]]:
        return [c[1] for c in self.calls if c[0] == "list_records"]

    @property
    def last(self) -> list[str]:
        return self.argvs[-1]

    @property
    def targets(self) -> list[str | None]:
        return [c[2] for c in self.calls if c[0] in ("run", "run_checked", "list_records")]


def _tools(fr: FakeRunner) -> dict:
    """Register all tools against a fake runner; return name -> wrapped fn."""
    mcp = FastMCP("tmux")
    register_all(mcp, fr)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


# ---------------------------------------------------------------------------
# passthrough.py
# ---------------------------------------------------------------------------


async def test_command_passes_args_verbatim():
    fr = FakeRunner()
    await _tools(fr)["tmux_command"](args=["new-window", "-n", "logs"], target="pipnode")
    assert fr.calls[-1] == ("run", ["new-window", "-n", "logs"], "pipnode")


async def test_query_builds_display_message():
    fr = FakeRunner(output="/tmp\n")
    res = await _tools(fr)["tmux_query"](format="#{pane_current_path}", target_pane="%2")
    assert fr.checked == [["display-message", "-p", "-t", "%2", "#{pane_current_path}"]]
    assert res == {"value": "/tmp"}


async def test_query_without_pane_omits_target_flag():
    fr = FakeRunner(output="x")
    await _tools(fr)["tmux_query"](format="#{host}")
    assert fr.checked == [["display-message", "-p", "#{host}"]]


async def test_kill_server_argv():
    fr = FakeRunner()
    assert await _tools(fr)["tmux_kill"](kind="server") == {
        "killed": True,
        "kind": "server",
        "id": None,
    }
    assert fr.checked == [["kill-server"]]


async def test_list_targets_reads_config_not_tmux():
    fr = FakeRunner()
    fr.config = {"targets": {"pipnode": {}, "box": {}}}
    res = await _tools(fr)["tmux_list_targets"]()
    assert res["targets"][0] == "local"
    assert set(res["targets"]) == {"local", "pipnode", "box"}
    assert fr.calls == []  # purely config-driven


# ---------------------------------------------------------------------------
# sessions.py
# ---------------------------------------------------------------------------


async def test_has_session_argv_and_bool():
    fr = FakeRunner(run_ok=True)
    res = await _tools(fr)["tmux_has_session"](session="dev")
    assert fr.calls[-1] == ("run", ["has-session", "-t", "dev"], None)
    assert res == {"exists": True}

    fr = FakeRunner(run_ok=False)
    res = await _tools(fr)["tmux_has_session"](session="nope")
    assert res == {"exists": False}


async def test_new_session_full_argv():
    fr = FakeRunner(version="3.4", output=f"$1{SEP}dev")
    res = await _tools(fr)["tmux_new_session"](
        name="dev",
        start_directory="/srv",
        command="htop",
        width=120,
        height=40,
        env={"FOO": "bar"},
    )
    assert fr.checked == [
        [
            "new-session",
            "-d",
            "-s",
            "dev",
            "-c",
            "/srv",
            "-x",
            "120",
            "-y",
            "40",
            "-e",
            "FOO=bar",
            "-P",
            "-F",
            f"#{{session_id}}{SEP}#{{session_name}}",
            "htop",
        ]
    ]
    assert res == {"id": "$1", "name": "dev"}


async def test_new_session_env_gated_on_old_tmux():
    fr = FakeRunner(version="2.9", output=f"$1{SEP}dev")
    res = await _tools(fr)["tmux_new_session"](name="dev", env={"FOO": "bar"})
    assert "-e" not in fr.checked[0]
    assert res["notes"] == ["env ignored: new-session -e requires tmux 3.0+"]


async def test_new_session_attach_or_create_reuses_existing():
    fr = FakeRunner(run_ok=True, output=f"$7{SEP}dev")
    res = await _tools(fr)["tmux_new_session"](name="dev", attach_or_create=True)
    # has-session probe via run, then display-message via run_checked; no new-session.
    assert fr.calls[0] == ("run", ["has-session", "-t", "dev"], None)
    assert fr.checked == [
        [
            "display-message",
            "-p",
            "-t",
            "dev",
            f"#{{session_id}}{SEP}#{{session_name}}",
        ]
    ]
    assert not any("new-session" in a for a in fr.argvs)
    assert res == {"id": "$7", "name": "dev"}


async def test_new_session_attach_or_create_creates_when_absent():
    fr = FakeRunner(run_ok=False, output=f"$2{SEP}dev")
    await _tools(fr)["tmux_new_session"](name="dev", attach_or_create=True)
    assert fr.checked[-1][0] == "new-session"


async def test_rename_and_kill_session_argv():
    fr = FakeRunner()
    tools = _tools(fr)
    await tools["tmux_rename"](kind="session", id="old", new_name="new")
    await tools["tmux_kill"](kind="session", id="dead")
    assert fr.checked == [
        ["rename-session", "-t", "old", "new"],
        ["kill-session", "-t", "dead"],
    ]


# ---------------------------------------------------------------------------
# windows.py
# ---------------------------------------------------------------------------


async def test_new_window_argv():
    fr = FakeRunner(output=f"@3{SEP}2{SEP}logs")
    res = await _tools(fr)["tmux_new_window"](
        session="dev",
        name="logs",
        start_directory="/var/log",
        command="tail -f x",
        select=False,
    )
    assert fr.checked == [
        [
            "new-window",
            "-d",
            "-t",
            "dev",
            "-n",
            "logs",
            "-c",
            "/var/log",
            "-P",
            "-F",
            f"#{{window_id}}{SEP}#{{window_index}}{SEP}#{{window_name}}",
            "tail -f x",
        ]
    ]
    assert res == {"id": "@3", "index": "2", "name": "logs"}


async def test_next_layout_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_next_layout"](window="dev:1")
    assert fr.checked == [["next-layout", "-t", "dev:1"]]


async def test_move_window_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_move_window"](src="a:1", dst="b:2")
    assert fr.checked == [["move-window", "-s", "a:1", "-t", "b:2"]]


# ---------------------------------------------------------------------------
# panes.py
# ---------------------------------------------------------------------------


async def test_list_panes_scopes():
    fr = FakeRunner()
    await _tools(fr)["tmux_list_panes"](window="dev:1")
    fr2 = FakeRunner()
    await _tools(fr2)["tmux_list_panes"](session="dev")
    fr3 = FakeRunner()
    await _tools(fr3)["tmux_list_panes"]()
    assert fr.listed == [["list-panes", "-t", "dev:1"]]
    assert fr2.listed == [["list-panes", "-s", "-t", "dev"]]
    assert fr3.listed == [["list-panes", "-a"]]


async def test_split_window_argv():
    fr = FakeRunner(output=f"%5{SEP}1")
    res = await _tools(fr)["tmux_split_window"](
        target_pane="%1",
        vertical=True,
        start_directory="/srv",
        command="bash",
        percentage=30,
        select=False,
    )
    assert fr.checked == [
        [
            "split-window",
            "-v",
            "-d",
            "-t",
            "%1",
            "-c",
            "/srv",
            "-p",
            "30",
            "-P",
            "-F",
            f"#{{pane_id}}{SEP}#{{pane_index}}",
            "bash",
        ]
    ]
    assert res == {"id": "%5", "index": "1"}


async def test_split_window_horizontal_default():
    fr = FakeRunner()
    await _tools(fr)["tmux_split_window"]()
    argv = fr.checked[0]
    assert argv[:2] == ["split-window", "-h"]
    assert "-d" not in argv  # select=True default


async def test_set_pane_title_gated():
    fr = FakeRunner(version="2.6")
    res = await _tools(fr)["tmux_set_pane_title"](title="agent-1", target_pane="%2")
    assert fr.checked == [["select-pane", "-t", "%2", "-T", "agent-1"]]
    assert res == {"pane": "%2", "title": "agent-1"}

    old = FakeRunner(version="2.5")
    res = await _tools(old)["tmux_set_pane_title"](title="x", target_pane="%2")
    assert old.checked == []  # no-op on old tmux
    assert "notes" in res


async def test_resize_pane_only_nonzero_directions():
    fr = FakeRunner()
    await _tools(fr)["tmux_resize_pane"](target_pane="%1", left=5, down=3)
    assert fr.checked == [["resize-pane", "-t", "%1", "-L", "5", "-D", "3"]]


async def test_clear_history_argv():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_clear_history"](target_pane="%1")
    assert fr.checked == [["clear-history", "-t", "%1"]]
    assert res == {"cleared": True, "pane": "%1"}


async def test_select_layout_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_select_layout"](layout="tiled", window="dev:1")
    assert fr.checked == [["select-layout", "-t", "dev:1", "tiled"]]


# ---------------------------------------------------------------------------
# io.py
# ---------------------------------------------------------------------------


async def test_send_keys_literal_text():
    fr = FakeRunner()
    await _tools(fr)["tmux_send_keys"](target_pane="%1", text="ls -la", enter=True)
    assert fr.checked == [
        ["send-keys", "-t", "%1", "-l", "ls -la"],
        ["send-keys", "-t", "%1", "Enter"],
    ]


async def test_send_keys_non_literal_text():
    fr = FakeRunner()
    await _tools(fr)["tmux_send_keys"](target_pane="%1", text="C-c", literal=False)
    assert fr.checked == [["send-keys", "-t", "%1", "C-c"]]


async def test_send_keys_key_tokens():
    fr = FakeRunner()
    await _tools(fr)["tmux_send_keys"](target_pane="%1", keys=["Up", "Up", "Enter"])
    assert fr.checked == [["send-keys", "-t", "%1", "Up", "Up", "Enter"]]


async def test_send_keys_requires_exactly_one_mode():
    fr = FakeRunner()
    tools = _tools(fr)
    with pytest.raises(ToolError):
        await tools["tmux_send_keys"](target_pane="%1")
    with pytest.raises(ToolError):
        await tools["tmux_send_keys"](target_pane="%1", text="x", keys=["y"])


async def test_capture_pane_full_argv_and_trim():
    fr = FakeRunner(version="3.4", output="line1\nline2\n\n   \n")
    res = await _tools(fr)["tmux_capture_pane"](
        target_pane="%1", start=-100, end=-1, escapes=True, join=True
    )
    assert fr.checked == [
        [
            "capture-pane",
            "-p",
            "-t",
            "%1",
            "-e",
            "-J",
            "-S",
            "-100",
            "-E",
            "-1",
        ]
    ]
    assert res == {"content": "line1\nline2"}  # trailing blank lines trimmed


async def test_capture_pane_gates_escape_and_join_on_old_tmux():
    fr = FakeRunner(version="1.7", output="x")
    await _tools(fr)["tmux_capture_pane"](target_pane="%1", escapes=True, join=True)
    assert fr.checked == [["capture-pane", "-p", "-t", "%1"]]


# ---------------------------------------------------------------------------
# options.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,flag",
    [("server", "-s"), ("session", None), ("window", "-w"), ("pane", "-p")],
)
async def test_set_option_scope_flags(scope, flag):
    fr = FakeRunner()
    await _tools(fr)["tmux_set_option"](name="status", value="on", scope=scope, target_entity="dev")
    expected = ["set-option"]
    if flag:
        expected.append(flag)
    expected += ["-t", "dev", "status", "on"]
    assert fr.checked == [expected]


async def test_set_option_bad_scope_raises():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_set_option"](name="x", value="y", scope="bogus")


async def test_show_options_global_and_parse():
    fr = FakeRunner(output='status on\nstatus-left "left"\n')
    res = await _tools(fr)["tmux_show_options"](scope="server", global_=True)
    assert fr.checked == [["show-options", "-s", "-g"]]
    assert res == {"options": {"status": "on", "status-left": "left"}}


async def test_buffer_tools_argv():
    fr = FakeRunner()
    tools = _tools(fr)
    await tools["tmux_set_buffer"](data="hello", name="b1")
    await tools["tmux_paste_buffer"](target_pane="%1", name="b1", delete=True)
    await tools["tmux_delete_buffer"](name="b1")
    assert fr.checked == [
        ["set-buffer", "-b", "b1", "hello"],
        ["paste-buffer", "-t", "%1", "-b", "b1", "-d"],
        ["delete-buffer", "-b", "b1"],
    ]


async def test_save_load_buffer_argv():
    fr = FakeRunner()
    tools = _tools(fr)
    r1 = await tools["tmux_save_buffer"](path="/tmp/x", name="b1", append=True)
    r2 = await tools["tmux_load_buffer"](path="/tmp/y", name="b2")
    assert fr.checked == [
        ["save-buffer", "-a", "-b", "b1", "/tmp/x"],
        ["load-buffer", "-b", "b2", "/tmp/y"],
    ]
    assert r1 == {"saved": "/tmp/x", "name": "b1", "appended": True}
    assert r2 == {"loaded": "/tmp/y", "name": "b2"}


# ---------------------------------------------------------------------------
# environment.py
# ---------------------------------------------------------------------------


async def test_set_environment_value():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_set_environment"](name="FOO", value="bar", session="dev")
    assert fr.checked == [["set-environment", "-t", "dev", "FOO", "bar"]]
    assert res == {"set": "FOO", "value": "bar", "global": False}


async def test_set_environment_global_unset_remove():
    fr = FakeRunner()
    r1 = await _tools(fr)["tmux_set_environment"](name="FOO", global_=True, unset=True)
    assert fr.checked[-1] == ["set-environment", "-g", "-u", "FOO"]
    assert r1 == {"unset": "FOO", "global": True}

    fr = FakeRunner()
    r2 = await _tools(fr)["tmux_set_environment"](name="FOO", remove=True)
    assert fr.checked[-1] == ["set-environment", "-r", "FOO"]
    assert r2 == {"removed": "FOO", "global": False}


async def test_set_environment_remove_and_unset_conflict():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_set_environment"](name="X", remove=True, unset=True)


async def test_show_environment_argv_and_parse():
    fr = FakeRunner(output="FOO=bar\nBARE\n-GONE\n")
    res = await _tools(fr)["tmux_show_environment"](session="dev")
    assert fr.checked == [["show-environment", "-t", "dev"]]
    assert res == {
        "environment": {"FOO": "bar", "BARE": None, "GONE": None},
        "removed": ["GONE"],
    }


# ---------------------------------------------------------------------------
# clients.py
# ---------------------------------------------------------------------------


async def test_server_info_argv_and_fields():
    fr = FakeRunner(version="3.4", output=f"4242{SEP}/tmp/tmux-1000/default")
    res = await _tools(fr)["tmux_server_info"]()
    assert fr.checked == [["display-message", "-p", f"#{{pid}}{SEP}#{{socket_path}}"]]
    assert res["pid"] == 4242
    assert res["socket_path"] == "/tmp/tmux-1000/default"
    assert res["version"] == "3.4"
    assert res["supported"] is True


async def test_display_message_argv():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_display_message"](
        message="hi", target_client="/dev/pts/0", target_pane="%1"
    )
    assert fr.checked == [["display-message", "-c", "/dev/pts/0", "-t", "%1", "hi"]]
    assert res == {"displayed": True}


# ---------------------------------------------------------------------------
# plumbing.py
# ---------------------------------------------------------------------------


async def test_link_unlink_window_argv():
    fr = FakeRunner()
    tools = _tools(fr)
    await tools["tmux_link_window"](src="a:1", dst="b:", select=False)
    await tools["tmux_unlink_window"](window="b:1")
    assert fr.checked == [
        ["link-window", "-s", "a:1", "-t", "b:", "-d"],
        ["unlink-window", "-t", "b:1"],
    ]


async def test_break_pane_argv():
    fr = FakeRunner(output=f"@9{SEP}3{SEP}out")
    res = await _tools(fr)["tmux_break_pane"](target_pane="%1", window_name="out", select=False)
    assert fr.checked == [
        [
            "break-pane",
            "-s",
            "%1",
            "-d",
            "-n",
            "out",
            "-P",
            "-F",
            f"#{{window_id}}{SEP}#{{window_index}}{SEP}#{{window_name}}",
        ]
    ]
    assert res == {"id": "@9", "index": "3", "name": "out"}


async def test_join_pane_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_join_pane"](
        src="%1", dst="%2", vertical=True, percentage=40, select=False
    )
    assert fr.checked == [
        [
            "join-pane",
            "-v",
            "-s",
            "%1",
            "-t",
            "%2",
            "-d",
            "-p",
            "40",
        ]
    ]


async def test_find_window_filters_records():
    records = [
        {"name": "logs", "title": "tailing"},
        {"name": "shell", "title": "bash"},
        {"name": "build", "title": "make LOGS"},
    ]
    fr = FakeRunner(records=records)
    res = await _tools(fr)["tmux_find_window"](pattern="log")
    assert fr.listed == [["list-windows", "-a"]]
    # matches name "logs" and title "make LOGS" (case-insensitive substring)
    assert res["matches"] == [records[0], records[2]]


async def test_pipe_pane_argv():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_pipe_pane"](
        target_pane="%1", command="cat >> /tmp/log", only_new=True
    )
    assert fr.checked == [["pipe-pane", "-o", "-t", "%1", "cat >> /tmp/log"]]
    assert res == {"piping": True, "pane": "%1"}

    fr = FakeRunner()
    res = await _tools(fr)["tmux_pipe_pane"](target_pane="%1")
    assert fr.checked == [["pipe-pane", "-t", "%1"]]
    assert res == {"piping": False, "pane": "%1"}


# ---------------------------------------------------------------------------
# keys.py  (key-table flag spelling is version-gated: -T on 2.1+, -t below)
# ---------------------------------------------------------------------------


async def test_list_keys_table_flag_modern_vs_old():
    fr = FakeRunner(version="2.1", output="")
    await _tools(fr)["tmux_list_keys"](table="copy-mode")
    assert fr.checked == [["list-keys", "-T", "copy-mode"]]

    old = FakeRunner(version="2.0", output="")
    await _tools(old)["tmux_list_keys"](table="copy-mode")
    assert old.checked == [["list-keys", "-t", "copy-mode"]]


async def test_bind_key_argv():
    fr = FakeRunner(version="2.1")
    res = await _tools(fr)["tmux_bind_key"](
        key="C-l",
        command=["new-window", "-n", "logs"],
        table="prefix",
        repeat=True,
        root=True,
    )
    assert fr.checked == [
        [
            "bind-key",
            "-r",
            "-n",
            "-T",
            "prefix",
            "C-l",
            "new-window",
            "-n",
            "logs",
        ]
    ]
    assert res == {"bound": "C-l"}


async def test_bind_key_requires_command():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_bind_key"](key="C-l", command=[])


async def test_unbind_key_argv_and_all():
    fr = FakeRunner(version="2.1")
    await _tools(fr)["tmux_unbind_key"](key="C-l", root=True)
    assert fr.checked == [["unbind-key", "-n", "C-l"]]

    fr = FakeRunner(version="2.1")
    res = await _tools(fr)["tmux_unbind_key"](all=True, table="prefix")
    assert fr.checked == [["unbind-key", "-a", "-T", "prefix"]]
    assert res == {"unbound": "all"}


async def test_unbind_key_requires_key_when_not_all():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_unbind_key"]()


# ---------------------------------------------------------------------------
# copymode.py  (driven by send-keys -X, needs tmux 2.4+)
# ---------------------------------------------------------------------------


async def test_copy_mode_enter_and_exit():
    fr = FakeRunner(version="2.4")
    await _tools(fr)["tmux_copy_mode"](target_pane="%1", page_up=True)
    assert fr.checked == [["copy-mode", "-u", "-t", "%1"]]

    fr = FakeRunner(version="2.4")
    await _tools(fr)["tmux_copy_mode"](target_pane="%1", exit=True)
    assert fr.checked == [["send-keys", "-X", "-t", "%1", "cancel"]]


async def test_copy_scroll_repeats_command():
    fr = FakeRunner(version="2.4")
    res = await _tools(fr)["tmux_copy_scroll"](target_pane="%1", direction="page-up", amount=3)
    assert fr.checked == [
        ["copy-mode", "-t", "%1"],
        ["send-keys", "-X", "-t", "%1", "page-up"],
        ["send-keys", "-X", "-t", "%1", "page-up"],
        ["send-keys", "-X", "-t", "%1", "page-up"],
    ]
    assert res == {"scrolled": "page-up", "amount": 3, "pane": "%1"}


async def test_copy_scroll_top_ignores_amount():
    fr = FakeRunner(version="2.4")
    res = await _tools(fr)["tmux_copy_scroll"](target_pane="%1", direction="top", amount=9)
    assert fr.checked == [
        ["copy-mode", "-t", "%1"],
        ["send-keys", "-X", "-t", "%1", "history-top"],
    ]
    assert res["amount"] == 1


async def test_copy_scroll_bad_direction():
    fr = FakeRunner(version="2.4")
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_copy_scroll"](target_pane="%1", direction="sideways")


async def test_copy_search_argv():
    fr = FakeRunner(version="2.4")
    await _tools(fr)["tmux_copy_search"](target_pane="%1", pattern="error", backward=True)
    assert fr.checked == [
        ["copy-mode", "-t", "%1"],
        ["send-keys", "-X", "-t", "%1", "search-backward", "error"],
    ]


async def test_copy_mode_requires_2_4():
    fr = FakeRunner(version="2.3")
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_copy_scroll"](target_pane="%1")


# ---------------------------------------------------------------------------
# hooks.py
# ---------------------------------------------------------------------------


async def test_set_hook_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_set_hook"](hook="pane-died", command='display "gone"', global_=True)
    assert fr.checked == [["set-hook", "-g", "pane-died", 'display "gone"']]


async def test_set_hook_unset_argv():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_set_hook"](hook="pane-died", unset=True)
    assert fr.checked == [["set-hook", "-u", "pane-died"]]
    assert res == {"hook": "pane-died", "unset": True}


async def test_set_hook_requires_command():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_set_hook"](hook="pane-died")


async def test_show_hooks_parse():
    fr = FakeRunner(output='pane-died display "x"\nsession-created run y\n')
    res = await _tools(fr)["tmux_show_hooks"](global_=True, target_entity="dev")
    assert fr.checked == [["show-hooks", "-g", "-t", "dev"]]
    assert res == {"hooks": {"pane-died": 'display "x"', "session-created": "run y"}}


async def test_run_shell_argv():
    fr = FakeRunner()
    fr.run_result = TmuxResult("out", "", 0)
    res = await _tools(fr)["tmux_run_shell"](command="echo hi", background=True, target_pane="%1")
    assert fr.calls[-1] == ("run", ["run-shell", "-b", "-t", "%1", "echo hi"], None)
    assert res == {"output": "out", "exit_code": 0}


async def test_if_shell_argv():
    fr = FakeRunner()
    await _tools(fr)["tmux_if_shell"](
        condition="test -f /x",
        if_command="display yes",
        else_command="display no",
        background=True,
        is_format=True,
    )
    assert fr.checked == [
        [
            "if-shell",
            "-b",
            "-F",
            "test -f /x",
            "display yes",
            "display no",
        ]
    ]


# ---------------------------------------------------------------------------
# target plumbing: the `target` kwarg is threaded through unchanged
# ---------------------------------------------------------------------------


async def test_target_threaded_through():
    fr = FakeRunner()
    await _tools(fr)["tmux_kill"](kind="pane", id="%1", target="pipnode")
    assert fr.targets == ["pipnode"]


# ---------------------------------------------------------------------------
# merged.py  (consolidated kind-discriminated tools — P6 §6.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("session", ["kill-session", "-t", "x"]),
        ("window", ["kill-window", "-t", "x"]),
        ("pane", ["kill-pane", "-t", "x"]),
    ],
)
async def test_kill_argv_per_kind(kind, expected):
    fr = FakeRunner()
    res = await _tools(fr)["tmux_kill"](kind=kind, id="x")
    assert fr.checked == [expected]
    assert res == {"killed": True, "kind": kind, "id": "x"}


async def test_kill_server_takes_no_id():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_kill"](kind="server")
    assert fr.checked == [["kill-server"]]
    assert res == {"killed": True, "kind": "server", "id": None}


async def test_kill_requires_id_for_non_server():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_kill"](kind="pane")


async def test_kill_bad_kind_raises():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_kill"](kind="bogus", id="x")
    assert fr.checked == []


@pytest.mark.parametrize("kind", ["session", "window"])
async def test_rename_argv_per_kind(kind):
    fr = FakeRunner()
    res = await _tools(fr)["tmux_rename"](kind=kind, id="old", new_name="new")
    assert fr.checked == [[f"rename-{kind}", "-t", "old", "new"]]
    assert res == {"renamed": True, "kind": kind, "name": "new"}


@pytest.mark.parametrize("kind", ["window", "pane"])
async def test_select_argv_per_kind(kind):
    fr = FakeRunner()
    res = await _tools(fr)["tmux_select"](kind=kind, id="t")
    assert fr.checked == [[f"select-{kind}", "-t", "t"]]
    assert res == {"selected": "t", "kind": kind}


async def test_last_argv_with_and_without_scope():
    fr = FakeRunner()
    res = await _tools(fr)["tmux_last"](kind="window", scope="dev")
    assert fr.checked == [["last-window", "-t", "dev"]]
    assert res == {"selected": "last", "kind": "window"}

    fr = FakeRunner()
    await _tools(fr)["tmux_last"](kind="pane")
    assert fr.checked == [["last-pane"]]


@pytest.mark.parametrize("kind", ["window", "pane"])
async def test_swap_argv_per_kind(kind):
    fr = FakeRunner()
    res = await _tools(fr)["tmux_swap"](kind=kind, src="a", dst="b")
    assert fr.checked == [[f"swap-{kind}", "-s", "a", "-t", "b"]]
    assert res == {"swapped": True, "kind": kind, "src": "a", "dst": "b"}


@pytest.mark.parametrize("kind", ["pane", "window"])
async def test_respawn_argv_per_kind_with_env(kind):
    fr = FakeRunner(version="3.0")
    res = await _tools(fr)["tmux_respawn"](
        kind=kind, id="t", command="svc", kill=True, start_directory="/srv", env={"A": "1"}
    )
    assert fr.checked == [[f"respawn-{kind}", "-k", "-c", "/srv", "-e", "A=1", "-t", "t", "svc"]]
    assert res == {"respawned": True, "kind": kind, "id": "t"}


async def test_respawn_env_gated_on_old_tmux():
    fr = FakeRunner(version="2.9")
    res = await _tools(fr)["tmux_respawn"](kind="pane", id="%1", env={"K": "V"})
    assert "-e" not in fr.checked[0]
    assert res["notes"] == ["env ignored: respawn -e requires tmux 3.0+"]


async def test_list_session_argv():
    fr = FakeRunner(records=[{"id": "$0"}])
    res = await _tools(fr)["tmux_list"](kind="session", target="t")
    assert fr.listed == [["list-sessions"]]
    assert fr.targets == ["t"]
    assert res == {"items": [{"id": "$0"}], "kind": "session"}


async def test_list_window_scopes():
    fr = FakeRunner()
    await _tools(fr)["tmux_list"](kind="window", scope="dev")
    assert fr.listed == [["list-windows", "-t", "dev"]]

    fr = FakeRunner()
    await _tools(fr)["tmux_list"](kind="window")
    assert fr.listed == [["list-windows", "-a"]]


async def test_list_client_scopes():
    fr = FakeRunner()
    await _tools(fr)["tmux_list"](kind="client", scope="dev")
    assert fr.listed == [["list-clients", "-t", "dev"]]

    fr = FakeRunner()
    await _tools(fr)["tmux_list"](kind="client")
    assert fr.listed == [["list-clients"]]


async def test_list_buffer_parse():
    fr = FakeRunner(output=f"b1{SEP}11\nb2{SEP}22\n")
    res = await _tools(fr)["tmux_list"](kind="buffer")
    assert fr.checked == [["list-buffers", "-F", f"#{{buffer_name}}{SEP}#{{buffer_size}}"]]
    assert res == {
        "items": [{"name": "b1", "size": "11"}, {"name": "b2", "size": "22"}],
        "kind": "buffer",
    }


async def test_list_bad_kind_raises():
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)["tmux_list"](kind="pane")
    assert fr.listed == []


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("tmux_rename", {"id": "x", "new_name": "y"}),
        ("tmux_select", {"id": "x"}),
        ("tmux_last", {}),
        ("tmux_swap", {"src": "a", "dst": "b"}),
        ("tmux_respawn", {"id": "x"}),
    ],
)
async def test_merged_tools_reject_bad_kind(tool, kwargs):
    fr = FakeRunner()
    with pytest.raises(ToolError):
        await _tools(fr)[tool](kind="bogus", **kwargs)
