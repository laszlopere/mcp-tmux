#!/usr/bin/env bash
# Install mcp-tmux as an isolated tool AND register it with Claude Code in one
# step. Wheels can't run post-install code, so this script is the "it happens
# at install time" convenience: install + register together.
#
#   scripts/install.sh             # build from this checkout, then install+register
#   scripts/install.sh <wheel>     # install a prebuilt wheel, then register
#
# Removal is the mirror image: scripts/uninstall.sh
set -euo pipefail

name="tmux"
scope="user"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

wheel="${1:-}"
if [[ -z "$wheel" ]]; then
  echo ">> building wheel from $repo_root"
  uv build --wheel --out-dir "$repo_root/dist" "$repo_root"
  # newest wheel in dist/
  wheel="$(ls -t "$repo_root"/dist/*.whl | head -n1)"
fi

echo ">> installing tool from $wheel"
uv tool install --reinstall "$wheel"

echo ">> registering with Claude Code (scope=$scope, name=$name)"
# Use the package's own subcommand so the registered command matches the binary.
mcp-tmux register --scope "$scope" --name "$name"

echo ">> done. Verify with:  claude mcp list"
