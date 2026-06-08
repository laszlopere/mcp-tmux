import pytest
from mcp.server.fastmcp.exceptions import ToolError

from mcp_tmux.runner import TmuxError
from mcp_tmux.server import build_server
from mcp_tmux.tools._util import _wrap_errors


def _tools_by_name(mcp):
    return {t.name: t for t in mcp._tool_manager.list_tools()}


def test_read_only_tools_annotated():
    mcp = build_server(config={"defaults": {}, "targets": {}})
    tools = _tools_by_name(mcp)
    assert tools["tmux_capture_pane"].annotations.readOnlyHint is True
    assert tools["tmux_list_sessions"].annotations.readOnlyHint is True


def test_destructive_tools_annotated():
    mcp = build_server(config={"defaults": {}, "targets": {}})
    tools = _tools_by_name(mcp)
    ann = tools["tmux_kill_session"].annotations
    assert ann.destructiveHint is True
    assert ann.readOnlyHint is False


def test_non_annotated_tool_has_no_hints():
    mcp = build_server(config={"defaults": {}, "targets": {}})
    tools = _tools_by_name(mcp)
    # new_session is neither read-only nor destructive -> left unannotated.
    assert tools["tmux_new_session"].annotations is None


async def test_wrap_errors_maps_tmux_error():
    async def boom():
        raise TmuxError("can't find session", exit_code=1, stderr="x", argv=["tmux"])

    with pytest.raises(ToolError) as ei:
        await _wrap_errors(boom)()
    assert "can't find session" in str(ei.value)
    assert "exit status 1" in str(ei.value)


async def test_wrap_errors_maps_value_error():
    async def bad():
        raise ValueError("provide exactly one of text/keys")

    with pytest.raises(ToolError):
        await _wrap_errors(bad)()


async def test_wrap_errors_passes_through_success():
    async def ok(x):
        return x * 2

    assert await _wrap_errors(ok)(21) == 42
