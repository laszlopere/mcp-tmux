# mcp-tmux

[![CI](https://github.com/laszlopere/mcp-tmux/actions/workflows/ci.yml/badge.svg)](https://github.com/laszlopere/mcp-tmux/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mcp-tmux.svg)](https://pypi.org/project/mcp-tmux/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2.svg)](https://github.com/sponsors/laszlopere)
[![mcp-tmux MCP server](https://glama.ai/mcp/servers/laszlopere/mcp-tmux/badges/score.svg)](https://glama.ai/mcp/servers/laszlopere/mcp-tmux)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-2a6db2.svg)](https://mypy-lang.org/)
[![Last commit](https://img.shields.io/github/last-commit/laszlopere/mcp-tmux.svg)](https://github.com/laszlopere/mcp-tmux/commits)

A comprehensive, universal [MCP](https://modelcontextprotocol.io) server for
driving **tmux** — sessions, windows, panes, sending keystrokes, and reading
pane output — on the local machine or on remote hosts over SSH.

Source: **<https://github.com/laszlopere/mcp-tmux>**

## Shared, visible sessions — pair with the AI

This is the whole point, not a caveat: the agent drives **real** tmux sessions,
not a private sandbox. When you attach to a session the agent is using, you see
its keystrokes and command output **live**, and you can type into the very same
pane. Nothing the agent does is hidden from an attached human — by design.

That makes tmux a natural medium for **pair programming with the AI**: open a
shared session, watch it work, take the keyboard when you want to step in, and
hand it back. The agent and you cooperate in one place instead of the agent
operating out of sight. (Because writes into an attached session are visible and
real, be deliberate with destructive commands — you're both driving the same
terminal.)

## Design goals

- **Comprehensive.** Curated tools cover the common operations ergonomically,
  and a raw `tmux_command` passthrough runs *any* tmux subcommand — so whatever
  your tmux supports, this server supports.
- **Universal.** Works against tmux **1.8+** (≈2013 — covers virtually every
  live distro). The server detects the target's tmux version and only uses
  flags/format variables that version understands.
- **Local + remote.** Any tool can run against the local tmux or a remote host
  over SSH (ad-hoc `user@host` or a named profile from the config file). Old or
  minimal boxes only need `tmux` + `ssh`; the server itself runs on a modern
  host with Python 3.10+.

## Install

```bash
uvx mcp-tmux            # run directly with uv (no install)
# or install it as an isolated, easily-removable tool:
uv tool install mcp-tmux
# or
pipx install mcp-tmux
```

Requires Python 3.10+ on the host running the server, plus the `tmux` binary
(and `ssh` for remote targets).

> **Installing ≠ registering.** Installing the package only puts the `mcp-tmux`
> executable on your PATH — it does **not** tell any MCP client about it. Python
> wheels can't run post-install code, so registration is always a separate step
> (see below). If a client "can't find" the server after install, it just hasn't
> been registered yet.

## Register with Claude Code

The package can register itself — no hand-editing of config files:

```bash
mcp-tmux register        # add to Claude Code at *user* scope
mcp-tmux unregister      # remove it again
```

**User scope matters.** `mcp-tmux register` defaults to `--scope user`, so the
server is visible from **every** directory/session. The plain
`claude mcp add tmux -- mcp-tmux` defaults to `local` (project) scope, which is
the usual reason a server "doesn't show up" in another session — it was only
added for the directory you ran it in. To pick a scope explicitly:

```bash
mcp-tmux register --scope user      # everywhere (default)
mcp-tmux register --scope project   # shared via this repo's .mcp.json
mcp-tmux register --scope local     # just this directory
```

After registering, confirm with `claude mcp list` (you should see
`tmux: mcp-tmux  - ✓ Connected`). A client session already running must be
restarted to pick up a newly registered server.

Equivalent manual form, if you prefer the raw CLI:

```bash
claude mcp add -s user tmux -- mcp-tmux     # for an installed tool
claude mcp add -s user tmux -- uvx mcp-tmux # without installing
```

### One-shot install + register (and clean removal)

From a checkout, the helper scripts do install **and** registration together —
the closest thing to "it happens at install time":

```bash
scripts/install.sh        # build wheel, `uv tool install`, then `mcp-tmux register`
scripts/uninstall.sh      # `mcp-tmux unregister`, then `uv tool uninstall`
```

To remove everything by hand:

```bash
mcp-tmux unregister       # drop it from Claude Code
uv tool uninstall mcp-tmux   # remove the isolated tool (or: pipx uninstall mcp-tmux)
```

## Run from a checkout (development)

```bash
python -m mcp_tmux       # stdio server
```

## Tools (overview)

Tools are grouped into **toolsets** so a session only pays the schema cost of
what it needs. `core` is always loaded; the rest are opt-in (see
[Selecting toolsets](#selecting-toolsets)). A default session loads
`core` + `automation` (~18 tools); `["all"]` loads the full surface (60).

| Toolset | Tools |
|---|---|
| **core** *(always loaded)* | `tmux_command`, `tmux_query`, `tmux_version`, `tmux_list_targets`, `tmux_has_session`, `tmux_new_session`, `tmux_send_keys`, `tmux_capture_pane`, `tmux_new_window`, `tmux_list_panes`, `tmux_split_window`, `tmux_list`, `tmux_kill`, `tmux_rename`, `tmux_select` |
| `automation` *(default)* | `tmux_wait_for_text`, `tmux_wait_for_idle`, `tmux_run` |
| `layout` | `tmux_next_layout`, `tmux_move_window`, `tmux_select_layout`, `tmux_resize_pane`, `tmux_set_pane_title`, `tmux_clear_history`, `tmux_swap`, `tmux_last`, `tmux_respawn`, `tmux_link_window`, `tmux_unlink_window`, `tmux_break_pane`, `tmux_join_pane`, `tmux_find_window`, `tmux_pipe_pane` |
| `buffers` | `tmux_set_buffer`, `tmux_paste_buffer`, `tmux_delete_buffer`, `tmux_save_buffer`, `tmux_load_buffer` |
| `config` | `tmux_set_option`, `tmux_show_options`, `tmux_set_environment`, `tmux_show_environment`, `tmux_set_hook`, `tmux_show_hooks`, `tmux_run_shell`, `tmux_if_shell` |
| `keybindings` | `tmux_list_keys`, `tmux_bind_key`, `tmux_unbind_key` |
| `copymode` | `tmux_copy_mode`, `tmux_copy_scroll`, `tmux_copy_search` |
| `clients` | `tmux_server_info`, `tmux_display_message` |
| `stream` | `tmux_stream_start`, `tmux_stream_resize`, `tmux_stream_read`, `tmux_stream_send`, `tmux_stream_list`, `tmux_stream_stop` |

Every tool accepts an optional `target` (omit / `"local"`, a named profile, or
`user@host`). For anything not covered by a dedicated tool — including anything
gated out of the active toolsets — use `tmux_command(args=[...])`, which is in
`core` and reaches every tmux subcommand.

### Selecting toolsets

Pick toolsets with the `toolsets` config key or the `MCP_TMUX_TOOLSETS`
environment variable (comma-separated; env wins over config). `core` is always
included. The special value `all` loads every toolset. An unknown name is a
startup error listing the valid toolsets.

```toml
# ~/.config/mcp-tmux/config.toml
toolsets = ["core", "automation", "stream"]   # or ["all"] for the full surface
```

```jsonc
// or in the MCP server env, e.g. Claude Code's mcp config:
"env": { "MCP_TMUX_TOOLSETS": "core,layout,stream" }
```

With no setting, the default is `["core", "automation"]`.

The **consolidated** tools take a `kind` discriminator instead of having one
tool per entity — e.g. `tmux_kill(kind="window", id="dev:2")`,
`tmux_kill(kind="server")`, `tmux_swap(kind="pane", src="%1", dst="%2")`,
`tmux_rename(kind="session", id="old", new_name="new")`,
`tmux_list(kind="window", scope="dev")`. Valid kinds: `kill` →
session/window/pane/server; `rename` → session/window; `select`/`last`/`swap` →
window/pane; `respawn` → pane/window; `list` → session/window/client/buffer
(`tmux_list` returns `{items, kind}`; panes have their own `tmux_list_panes`
because they scope by window *or* session).

### Live streaming (opt-in)

The one-shot CLI is the universal default. For *watching* a pane as it
produces output — a build, a `tail`, a long job — `tmux_stream_*` opens a
persistent **control-mode** (`tmux -C`) connection and lets you long-poll its
event stream instead of repeatedly calling `tmux_capture_pane`:

```
tmux_stream_start(session="work")          # -> {"stream_id": "cm-1a2b3c4d", ...}
tmux_stream_read("cm-1a2b3c4d", timeout=10, kinds=["output"])
#   -> blocks until output, then {"events": [{"type":"output","pane":"%0",
#                                             "data":"...","seq":42}], "cursor":42}
tmux_stream_stop("cm-1a2b3c4d")            # detaches; the session keeps running
```

`tmux_stream_read` auto-advances a cursor, so just call it again for the next
batch; filter by `pane` and/or `kinds` (`"output"`, `"window-add"`,
`"layout-change"`, …). One connection is shared per (target, session) and
`tmux_stream_start` is idempotent.

Read-only state is also exposed as MCP **resources**: `tmux://sessions`,
`tmux://{session}/windows`, `tmux://{window}/panes` (local), plus target-aware
variants `tmux://{target}/sessions`, `tmux://{target}/{session}/windows`,
`tmux://{target}/{window}/panes`.

A typical agent flow:

```
tmux_new_session(detached=True)            # -> {"id": "$0", "name": "0"}
tmux_send_keys("0", text="echo hi", enter=True)
tmux_capture_pane("0")                     # -> {"content": "... hi ..."}
```

### Where `send_keys` text is evaluated

`tmux_send_keys` types its `text` **into the pane** — it is not a local shell
command. So any shell syntax in it (`$(...)`, backticks, `$VAR`, `~`, globs, …)
is expanded by the **shell running in that pane**, at the moment the keys are
executed — *not* on the machine running this MCP server. For an SSH target that
means the **remote** pane's shell does the expansion; the server only ships the
literal text across (the SSH layer shell-quotes the tmux argv so it survives the
hop intact).

```
# `$(hostname)` runs in the pane, so it prints the *target's* hostname,
# not the server's:
tmux_send_keys("work", text="echo $(hostname)", enter=True)
```

If you need a value from the server side instead, interpolate it yourself before
calling `send_keys`.

## Configuration

Optional TOML at `~/.config/mcp-tmux/config.toml` (override with
`MCP_TMUX_CONFIG`):

```toml
# toolsets = ["core", "automation"]   # which tool groups to load; ["all"] = full
#                                      # (also via MCP_TMUX_TOOLSETS, env wins)

[defaults]
timeout = 15                 # seconds per tmux invocation
# socket_name = "work"       # default `tmux -L`
# socket_path = "/tmp/sock"  # default `tmux -S`

[targets.prod]
host = "user@prod-db"
ssh_options = ["-J", "bastion", "-p", "2222"]
# socket_name = "work"
```

With no config file the server still works against local tmux and any ad-hoc
`user@host` target (SSH options come from your `~/.ssh/config`).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest            # unit tests always run; integration tests run if tmux exists
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full checklist (ruff, mypy,
pytest) and contribution guidelines.

## Contributing

Bug reports, feature requests, and pull requests are welcome on GitHub:
**<https://github.com/laszlopere/mcp-tmux>**. Please read
[CONTRIBUTING.md](CONTRIBUTING.md) first.

## Sponsor

If this project is useful to you, consider sponsoring its development via
[GitHub Sponsors](https://github.com/sponsors/laszlopere). ❤️

## License

[MIT](LICENSE) © László Pere
