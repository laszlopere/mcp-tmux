"""Register / unregister this server with Claude Code's MCP config.

Installing the package only puts the ``mcp-tmux`` executable on PATH; it does
*not* tell any MCP client about it (wheels never run post-install code). This
module wraps the supported ``claude mcp`` CLI so a user can do the registration
in one step — ``mcp-tmux register`` — instead of hand-editing ``~/.claude.json``.

Defaults to **user** scope: a local/project-scoped server is only visible from
the directory it was added in, which is the usual reason "it doesn't show up"
in another session.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

DEFAULT_NAME = "tmux"
DEFAULT_SCOPE = "user"


def _claude() -> str:
    path = shutil.which("claude")
    if path is None:
        sys.exit(
            "error: the 'claude' CLI was not found on PATH.\n"
            "Install Claude Code, or register manually with your MCP client "
            "using the command:  mcp-tmux"
        )
    return path


def register(name: str = DEFAULT_NAME, scope: str = DEFAULT_SCOPE) -> int:
    """Add this server to Claude Code via ``claude mcp add``."""
    claude = _claude()
    # `mcp-tmux` is the console script installed alongside this package, so the
    # registered command is exactly what the user runs to start the server.
    cmd = [claude, "mcp", "add", "-s", scope, name, "--", "mcp-tmux"]
    print("+ " + " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd).returncode


def unregister(name: str = DEFAULT_NAME, scope: str = DEFAULT_SCOPE) -> int:
    """Remove this server from Claude Code via ``claude mcp remove``."""
    claude = _claude()
    cmd = [claude, "mcp", "remove", "-s", scope, name]
    print("+ " + " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd).returncode
