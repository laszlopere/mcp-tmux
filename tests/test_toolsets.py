"""Toolset gating tests (P7).

Deterministic — no tmux binary required: build the server with various toolset
selections and assert which tool names get registered. Covers the default set,
core-only, each opt-in group, the ``["all"]`` back-compat surface, the
``MCP_TMUX_TOOLSETS`` env override, and the unknown-name error.
"""

from __future__ import annotations

import pytest

from mcp_tmux.server import build_server
from mcp_tmux.toolsets import (
    CORE,
    DEFAULT_TOOLSETS,
    OPTIONAL,
    TOOLSETS,
    resolve_enabled,
    select_toolsets,
)

BASE = {"defaults": {}, "targets": {}}


def _names(toolsets):
    mcp = build_server(config={**BASE, "toolsets": toolsets})
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_core_only_registers_exactly_core():
    assert _names(["core"]) == set(CORE)


def test_default_is_core_plus_automation():
    assert DEFAULT_TOOLSETS == ["core", "automation"]
    expected = set(CORE) | set(OPTIONAL["automation"])
    assert _names(None) == expected


@pytest.mark.parametrize("name", sorted(OPTIONAL))
def test_each_optin_adds_exactly_its_tools(name):
    assert _names(["core", name]) == set(CORE) | set(OPTIONAL[name])


def test_all_matches_full_surface():
    full = set().union(*TOOLSETS.values())
    assert _names(["all"]) == full
    # The full surface is the 60-tool count from the P6/P7 inventory.
    assert len(full) == 60


def test_optin_groups_are_disjoint_from_core_and_each_other():
    seen: set[str] = set(CORE)
    for tools in OPTIONAL.values():
        assert seen.isdisjoint(tools), tools & seen
        seen |= tools


def test_unknown_toolset_errors():
    with pytest.raises(ValueError, match="unknown toolset 'bogus'"):
        resolve_enabled(["bogus"])


def test_env_var_overrides_config(monkeypatch):
    monkeypatch.setenv("MCP_TMUX_TOOLSETS", "core, stream")
    # config asks for buffers, but the env var wins.
    assert select_toolsets({"toolsets": ["buffers"]}) == ["core", "stream"]


def test_env_var_empty_falls_back_to_config(monkeypatch):
    monkeypatch.setenv("MCP_TMUX_TOOLSETS", "  ")
    assert select_toolsets({"toolsets": ["buffers"]}) is None


def test_config_used_when_no_env(monkeypatch):
    monkeypatch.delenv("MCP_TMUX_TOOLSETS", raising=False)
    assert select_toolsets({"toolsets": ["layout"]}) == ["layout"]
    assert select_toolsets({"toolsets": None}) is None
    assert select_toolsets(None) is None
