#!/usr/bin/env bash
# Mirror of install.sh: unregister from Claude Code, then remove the tool.
#
#   scripts/uninstall.sh
set -euo pipefail

name="tmux"
scope="user"

# Unregister first, while the binary still exists (best-effort; ignore if the
# tool or the claude CLI is already gone).
if command -v mcp-tmux >/dev/null 2>&1; then
  echo ">> unregistering from Claude Code (scope=$scope, name=$name)"
  mcp-tmux unregister --scope "$scope" --name "$name" || true
fi

echo ">> removing tool"
uv tool uninstall mcp-tmux || true

echo ">> done."
