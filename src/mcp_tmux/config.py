"""Configuration loading for mcp-tmux.

The config file is TOML and lives at ``~/.config/mcp-tmux/config.toml`` by
default, overridable with the ``MCP_TMUX_CONFIG`` environment variable. It is
entirely optional: with no config file, the server still works against the
local tmux and any ad-hoc ``user@host`` SSH target.

Schema::

    [defaults]
    timeout = 15          # seconds per tmux invocation
    socket_name = "..."   # optional default `tmux -L` socket
    socket_path = "..."   # optional default `tmux -S` socket path
    config_file = "..."   # optional default `tmux -f` config file

    [targets.<name>]
    host = "user@host"            # required; passed to ssh
    ssh_options = ["-J", "bast"]  # optional extra ssh args (jump, port, key...)
    socket_name = "work"          # optional per-target tmux -L
    socket_path = "..."           # optional per-target tmux -S
    config_file = "..."           # optional per-target tmux -f
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib


DEFAULT_TIMEOUT = 15.0


def default_config_path() -> Path:
    """Return the config path, honoring ``MCP_TMUX_CONFIG`` then XDG."""
    override = os.environ.get("MCP_TMUX_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "mcp-tmux" / "config.toml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load and lightly validate the config file.

    Returns an empty config (``{"defaults": {}, "targets": {}}``) when the file
    does not exist, so callers never have to special-case "no config".
    """
    path = path or default_config_path()
    if not path.exists():
        return {"defaults": {}, "targets": {}}

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    defaults = raw.get("defaults", {}) or {}
    targets = raw.get("targets", {}) or {}
    if not isinstance(targets, dict):
        raise ValueError("config 'targets' must be a table of named targets")
    for name, spec in targets.items():
        if not isinstance(spec, dict) or "host" not in spec:
            raise ValueError(f"target '{name}' must be a table with a 'host' key")

    return {"defaults": defaults, "targets": targets}
