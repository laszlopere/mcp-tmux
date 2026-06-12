# mcp-tmux — TODO / Roadmap

Status: **Phases 0–3 + P0 + P1 + P2 + P3 complete and verified** (passthrough +
curated tools, local + SSH remote, resources, P0 polish, the P1 wait/sync
helpers, the P2 broadened tool surface + target-aware resources, and the P3
control-mode streaming layer). Local and remote (pipnode over SSH) tested
end-to-end; 60 passing tests. What follows is the plan for the next steps.

---

## P0 — Correctness & ergonomics gaps found during the build ✅ DONE

[x] 0.1. **Tool annotations** (`readOnlyHint` / `destructiveHint`). Applied
      centrally in `tools/_util.py:finalize_tools` via `ToolAnnotations`, tagging
      read-only and destructive tools by name. Verified in `list_tools`.
[x] 0.2. **`trim` option on `tmux_capture_pane`.** `trim=True` (default) strips the
      trailing blank padding lines tmux emits; `trim=False` keeps the raw
      capture. Covered by an integration test.
[x] 0.3. **Structured errors.** `TmuxError`/`ValueError` are mapped to FastMCP
      `ToolError` (clean message incl. exit status, no traceback) in
      `tools/_util.py:_wrap_errors`.
[x] 0.4. **Numeric coercion.** `formats.coerce_records` converts known
      numeric/boolean fields (`windows`, `attached`, `panes`, `width/height`,
      `pid`, `created`, …) to int/bool; applied in `runner.list_records`.

## P1 — The big agent-experience win: wait/synchronize helpers ✅ DONE

Driving a shell blind (send → sleep → capture) is fragile. Implemented in
`tools/wait.py`; verified locally and against pipnode's `testMCP` session.

[x] 1.1. **`tmux_wait_for_text(target_pane, pattern, timeout, regex=False, history)`**
      — polls `capture-pane` until `pattern` (substring or regex) appears.
      Returns {matched, elapsed, content}. (Caveat documented: a pattern can
      match the echoed input line; use `tmux_run` to wait for completion.)
[x] 1.2. **`tmux_wait_for_idle(target_pane, idle_seconds, timeout)`** — returns once
      the capture is unchanged for `idle_seconds`. Returns {idle, elapsed, content}.
[x] 1.3. **`tmux_run(target_pane, command, timeout, history)`** — brackets the
      command with unique markers (`__MCP_BEG/END_<token>__`), polls to
      completion, and returns just the output + exit code: {completed,
      exit_code, output, elapsed}. Pure parser `_extract_run_output` is
      unit-tested; the marker approach avoids the input-echo false match.

## P2 — Broaden the curated tool surface ✅ DONE

Passthrough already covers everything, but first-class tools help discovery.
Implemented across `tools/{clients,plumbing,hooks,keys,copymode}.py`; resources
made target-aware in `resources.py`. 8 new integration tests in `test_p2.py`
(52 passing total).

[x] 2.1. Clients/server (`tools/clients.py`): `tmux_list_clients`,
      `tmux_server_info` (pid, socket path, version), `tmux_display_message`
      (shows a message on the client's status line — distinct from
      `tmux_query`'s `display-message -p`).
[x] 2.2. Window/pane plumbing (`tools/plumbing.py`): `tmux_link_window`,
      `tmux_unlink_window`, `tmux_break_pane`, `tmux_join_pane`,
      `tmux_find_window` (non-interactive name/title search — tmux's own
      `find-window` opens an interactive chooser), `tmux_pipe_pane`.
[x] 2.3. Hooks & scripting (`tools/hooks.py`): `tmux_set_hook`, `tmux_show_hooks`,
      `tmux_run_shell`, `tmux_if_shell`.
[x] 2.4. Keys/bindings (`tools/keys.py`): `tmux_list_keys`, `tmux_bind_key`,
      `tmux_unbind_key`. The key-table flag is `-T` (2.1+) vs `-t` (older),
      gated via the `key_tables` capability.
[x] 2.5. Copy-mode helpers (`tools/copymode.py`): `tmux_copy_mode` (enter/exit),
      `tmux_copy_scroll`, `tmux_copy_search`. Driven by `send-keys -X` (gated on
      the `send_keys_X` / tmux 2.4+ capability). To *read* scrolled-back content,
      `tmux_capture_pane(start=...)` is simpler than copying a selection.
[x] 2.6. **Resources are target-aware**: added `tmux://{target}/sessions`,
      `tmux://{target}/{session}/windows`, `tmux://{target}/{window}/panes`
      alongside the existing local-only ones.

## P3 — Phase 4: streaming via control mode (`tmux -C`) ✅ DONE

Implemented in `control.py` (transport: pure parsers, `ControlConnection`,
`ControlManager` pool) and `tools/stream.py` (agent-facing tools). 8 new tests
in `test_control.py` (deterministic parser/framing unit tests + one real
`tmux -C` end-to-end); 60 passing total.

[x] 3.1. Persistent control-mode connection per (target, session) via
      `tmux -C attach -t <session>`. The reader loop parses `%output`,
      `%window-add`/`%window-close`/`%layout-change`, `%session-changed`,
      `%exit`, … and the `%begin`/`%end`/`%error` reply framing. Output is
      un-escaped (`\\ooo` octal + `\\\\`) to bytes → UTF-8, with ANSI stripped by
      default. Events are sequenced into a ring buffer.
[x] 3.2. Exposed as a **long-poll tool** (more universal than server-initiated
      notifications): `tmux_stream_read` blocks until new events or timeout and
      auto-advances a cursor; filterable by `pane`/`kinds`. Also
      `tmux_stream_start` (idempotent), `tmux_stream_send` (run a command over
      the connection, get its reply), `tmux_stream_list`, `tmux_stream_stop`.
[x] 3.3. Lifecycle: `ControlManager` pool keyed by (target, session); idempotent
      start reuses a live connection and replaces a dead one; `stop`/`stop_all`
      detach the control client (the session keeps running) and cancel tasks;
      gated on `capabilities.has("control_mode")`.
[x] 3.4. One-shot CLI remains the universal default; control mode is opt-in.

  Possible follow-ups (not blocking): automatic reconnect on unexpected
  `%exit`; surface `tmux_stream_*` over real MCP notifications for clients that
  support them; wire `ControlManager.stop_all` into a FastMCP shutdown hook.

## P5 — Curated tool gaps (agent shell ergonomics)

Everything here is already reachable via the `tmux_command` passthrough — these
are curated wrappers that make the common agent-driving-a-shell flows ergonomic
and discoverable. Higher value-per-effort than P4; ordered within by value. Each
follows the existing `tools/*.py` patterns (thin wrapper, structured return,
version-gated flags) and needs a test.

High value:

[x] 5.1. **`tmux_clear_history`** (`clear-history -t pane`) — wipe a pane's
      scrollback so a subsequent `tmux_capture_pane` / `tmux_run` starts from a
      clean slate. Pairs directly with the capture/run flow. Implemented in
      `tools/panes.py`, tagged destructive in `_util.py`; 2 tests in
      `test_p5.py` (history_size goes >0 → 0; annotation check). 63 passing.
[x] 5.2. **`tmux_respawn_pane`** (`respawn-pane [-k] [-c dir] [-e KEY=VAL] [cmd]`)
      and **`tmux_respawn_window`** (`respawn-window`) — restart the command in a
      dead/finished pane/window without recreating layout. For supervising
      services or retrying a crashed command. `kill=True` (-k) force-restarts a
      live one; `env` (-e) gated on the new `respawn_env` capability (3.0+),
      ignored-with-note on older tmux. In `tools/{panes,windows}.py`; 3 tests in
      `test_p5.py` (pane/window restart via `#{pane_current_command}`, env
      injection round-trip). 66 passing.
[x] 5.3. **`tmux_set_environment` / `tmux_show_environment`**
      (`set-environment [-gru]` / `show-environment [-g]`) — set/inspect session
      (or global `-g`) env vars *before* launching commands, instead of a racy
      `export` via `send_keys`. New `tools/environment.py`; `set` supports
      `global_`/`remove`(-r)/`unset`(-u); `show` parses `NAME=value` and the
      `-NAME` removal marker into {environment, removed}, tagged read-only. 4
      tests in `test_p5.py` (set→show→inherited-by-respawned-command, unset,
      read-only annotation). 69 passing.
[x] 5.4. **`tmux_save_buffer` / `tmux_load_buffer`** (`save-buffer -b name path` /
      `load-buffer -b name path`) — the file bridge for paste buffers (the
      set/list/paste/delete set in `options.py` is otherwise complete). The
      file path is resolved on the **target** (remote for SSH targets) —
      documented in both docstrings. `save` supports `append` (-a). In
      `tools/options.py`; 2 tests in `test_p5.py` (save→read file, load→
      show-buffer round-trip). 71 passing.

Streaming robustness:

[x] 5.5. **Client size on `tmux_stream_start`** — `width`/`height` options issue
      `refresh-client -C <w>x<h>` (gated on the new `refresh_client_size`
      capability, 2.4+) right after attach, so a control client doesn't default
      to 80×24 and wrap `%output` oddly. Added a standalone `tmux_stream_resize`
      too; the size is recorded and re-applied automatically across reconnects,
      and surfaced in `tmux_stream_list`. E2e test widens/narrows a window via
      both paths.
[x] 5.6. Automatic reconnect on unexpected `%exit` — `ControlConnection` now
      auto-reconnects on an unexpected drop (EOF/`%exit`) with capped
      exponential backoff, preserving stream_id / event buffer / sequence so
      cursors keep working. Emits synthetic `reconnected` / `disconnected`
      events; a flap guard (stability window + `max_reconnects`) stops retrying
      a permanently-dead target; `close()` sets `_closing` to suppress it. 3
      deterministic unit tests (success, give-up, close-suppresses) + the size
      e2e. 75 passing.

Small ergonomics:

[x] 5.7. **`tmux_new_session` `attach_or_create`** and **`env`** → `-e KEY=VAL`
      (3.0+, gated on the new `new_session_env` capability). tmux's own
      `new-session -A` *attaches* an existing session, which a headless MCP
      server has no terminal for (verified: `new-session -A -d` still errors
      "open terminal failed" on the second call); so `attach_or_create` is a
      detached `has-session` check that reuses the session (returns its
      id/name) or creates it — the idempotent create-or-reuse that's actually
      useful headless. In `tools/sessions.py`; 2 tests in `test_p5.py`
      (idempotency: same id, single session; env round-trip via a launched
      command). 77 passing.
[x] 5.8. **`tmux_set_pane_title`** (`select-pane -T <title>`, gated on the new
      `pane_title` capability, 2.6+; no-op-with-note on older tmux). Labels a
      pane for agents driving several at once; the title is already surfaced as
      `#{pane_title}` by `tmux_list_panes`. In `tools/panes.py`; 1 test in
      `test_p5.py` (set → read back via display-message and via the curated
      listing). 78 passing.
[x] 5.9. Navigation convenience: `tmux_last_window`, `tmux_last_pane`,
      `tmux_next_layout` (thin wrappers over `last-window`/`last-pane`/
      `next-layout`, all 1.8+ baseline so ungated). Each takes an optional
      `session`/`window` target and defaults to the current one. In
      `tools/{windows,panes}.py`; 3 tests in `test_p5.py` (switch-back via
      `#{window_index}`/`#{pane_index}`, layout string changes). 81 passing.

## P4 — Quality, packaging, CI

[x] 4.1. **Unit tests for the tools layer** (argv assembly per tool via a fake
      runner). `tests/test_tools_argv.py`: a `FakeRunner` records every
      `run`/`run_checked`/`list_records` call (driving real `Capabilities` from a
      version string) and tools are registered against it via `register_all`, so
      each tool's exact tmux argv is asserted with no tmux binary — flags,
      ordering, `-P -F` format strings, version-gated spellings (key-table
      `-T`/`-t`, `respawn`/`new-session` `-e`, `select-pane -T`, copy-mode
      `send-keys -X`, `capture-pane -e/-J`), target threading, and the
      validation/error branches (ValueError→ToolError). 71 new tests (152
      passing total). wait/stream/control keep their timing-driven suites.
[ ] 4.2. **tmux version-matrix CI** — run integration tests in containers against
      tmux 1.8 / 2.x / 3.x to prove the 1.8+ universality claim and catch
      format-var/flag drift.
[x] 4.3. **Lint & types** — `ruff` (E,F,I,W,UP,B; line-length 100) and `mypy`
      (`python_version=3.10`, `warn_unused_ignores`, `no_implicit_optional`,
      `files=["src"]`) configured in `pyproject.toml`; both in the `dev` extra.
      All 7 `# type: ignore`s in `src/` removed by real narrowing (`assert` after
      `is_remote`/keys/cursor guards; `sys.version_info` tomllib branch; typed the
      session result dict) — mypy is clean on 3.10 and 3.12 with no ignores. Repo
      `ruff format`ted; `ruff check .` and `mypy` both pass.
[x] 4.4. **Packaging** — `uv.lock` committed (uv 0.11.19); `uv build` produces
      sdist+wheel; `python_requires` smoke on a clean 3.10 env passes (import,
      `build_server`, tomli conditional dep, console script start/exit);
      `uvx --from <wheel> mcp-tmux` verified on 3.10 and 3.12; tagged `v0.1.0`.
[ ] 4.5. **CONTRIBUTING / config docs** — document named-target profiles
      (e.g. a `[targets.pipnode]` example with jump host + identity).
[ ] 4.6. **Publish to PyPI** — `uv publish` (needs token; irreversible — claims the
      `mcp-tmux` name permanently) so `uvx mcp-tmux` resolves from the index, then
      `git push origin master --tags`. Build is reproducible from `uv.lock` (4.4).

## P6 — Consolidate same-signature tools

**Problem.** The curated surface has grown to **71 tools**. Many are
near-duplicates that differ only in *which entity kind* they act on
(session / window / pane / server) while sharing the **exact same argument
shape and return shape**. A client that loads all 71 pays a real
discovery/selection cost. tmux itself separates `kill-session` / `kill-window`
/ `kill-pane`; we don't have to mirror that 1:1 — an MCP client picks a tool by
name+description, and a single `tmux_kill(kind=...)` is just as discoverable as
three.

**Goal.** Merge each *signature-equivalence class* into one tool that takes a
`kind` discriminator. We merge ONLY where arguments AND return value match (or
trivially normalize); anything with a distinct signature stays standalone.

Discriminator is named **`kind`** (not `type` — shadows a builtin), validated
against an explicit allow-list, raising `ValueError` (→ `ToolError`) on a bad
value. Return dicts gain a `"kind"` key so callers can tell what acted.

### 6.0 — Inventory: signature-equivalence classes

Columns are the *normalized* signature (the universal `target` is omitted; the
entity-id param is shown generically as `id`).

**Class K — destroy** `(id, )` → `{killed, <noun>}` — all DESTRUCTIVE:
| tool | id param | return |
|---|---|---|
| `tmux_kill_session` | `session` | `{killed, session}` |
| `tmux_kill_window` | `window` | `{killed, window}` |
| `tmux_kill_pane` | `target_pane` | `{killed, pane}` |
| `tmux_kill_server` | *(none)* | `{killed}` |

**Class R — rename** `(id, new_name)` → `{renamed, name}`:
| `tmux_rename_session` | `session` | | `tmux_rename_window` | `window` |

**Class SEL — activate** `(id, )` → `{selected: id}`:
| `tmux_select_window` | `window` | | `tmux_select_pane` | `target_pane` |

**Class SWAP** `(src, dst)` → `{swapped, src, dst}` — *byte-identical*:
| `tmux_swap_window` | | `tmux_swap_pane` |

**Class RESPAWN** `(id, command=None, kill=False, start_directory=None, env=None)`
→ `{respawned, <noun>, notes?}` — *byte-identical* but for the id-param/noun:
| `tmux_respawn_pane` | `target_pane` | | `tmux_respawn_window` | `window` |

**Class LAST — nav-to-previous** `(scope=None, )` → `{selected: "last"}`:
| `tmux_last_window` | scope=`session` | | `tmux_last_pane` | scope=`window` |

**Class LIST — enumerate** `(scope?, )` → `{<plural>: [...]}` — return key
differs, scope args differ; normalizes to `{items, kind}` — all READ_ONLY:
| `tmux_list_sessions` | `()` | `{sessions}` |
| `tmux_list_windows` | `(session)` | `{windows}` |
| `tmux_list_panes` | `(window, session)` | `{panes}` |
| `tmux_list_clients` | `(session)` | `{clients}` |
| `tmux_list_buffers` | `()` | `{buffers}` |
| `tmux_list_keys` | `(table)` | `{keys, lines}` ← *not* a record list; **exclude** |

### 6.1 — Strong merges (identical arg + return; do these first) ✅ DONE

All six live in `tools/merged.py`, registered after sessions/windows/panes.
Each validates `kind` via `require_kind()` (in `_util.py`) and returns a `kind`
key. Clean break — old per-kind tools removed (pre-PyPI, see 6.4 decision).

[x] **`tmux_kill(kind, id=None)`** ← kill_session/window/pane/server (4→1, −3).
      `kind ∈ {session, window, pane, server}`; `id` required for all but
      `server` (validate). Maps to `kill-<kind> [-t id]`. Returns
      `{killed: True, kind, id}`. Stays in `DESTRUCTIVE`.
[x] **`tmux_respawn(kind, id, command=None, kill=False, start_directory=None,
      env=None)`** ← respawn_pane/window (2→1, −1). Already byte-identical; the
      `env`/`respawn_env` capability gate is shared. `kind ∈ {pane, window}`.
[x] **`tmux_swap(kind, src, dst)`** ← swap_window/pane (2→1, −1). Byte-identical
      return today. `kind ∈ {window, pane}` → `swap-<kind> -s src -t dst`.
[x] **`tmux_rename(kind, id, new_name)`** ← rename_session/window (2→1, −1).
[x] **`tmux_select(kind, id)`** ← select_window/pane (2→1, −1).
[x] **`tmux_last(kind, scope=None)`** ← last_window/last_pane (2→1, −1). `scope`
      is the optional `-t` (a session for windows, a window for panes).

Subtotal: 14 tools → 6. **Net −8.**

### 6.2 — Soft merge (return key normalizes; arguments vary — decide explicitly) ✅ DONE

[x] **`tmux_list(kind, scope=None)`** ← list_sessions/windows/clients/buffers
      (4→1, −3). Chose option **(b)**: merge only the single-scope kinds and
      keep `tmux_list_panes` standalone (it has *two* scope axes, window vs
      session). `tmux_list_keys` excluded too (its return is text+lines, not
      records). Lives in `tools/merged.py`; `kind ∈ {session, window, client,
      buffer}` validated via `require_kind()`; `scope` is the optional `-t`
      session for `window`/`client` (windows fall back to `-a`, clients to
      all), ignored for `session`/`buffer`. Return normalized to
      `{items: [...], kind}`. Tagged READ_ONLY in `_util.py`; old per-kind list
      tools removed (clean break, pre-PyPI). Tests in `test_tools_argv.py`
      (per-kind argv + bad-kind ToolError), plus `test_p2`/`test_functional`
      call the merged name. 171 passing. Tool count 63 → 60.

### 6.3 — Explicitly NOT merged (distinct signatures / high-traffic primitives)

Keep standalone — different args, different returns, or core I/O where an
abstract `kind` would hurt clarity: `tmux_command`, `tmux_query`,
`tmux_send_keys`, `tmux_capture_pane`, `tmux_run`, `tmux_wait_for_text`,
`tmux_wait_for_idle`, `tmux_new_session`/`new_window`/`split_window`
(creation args diverge widely), `tmux_resize_pane`, `tmux_select_layout`,
`tmux_next_layout`, `tmux_set_pane_title`, `tmux_clear_history`, the buffer
set (`set/paste/delete/save/load_buffer`), the option/environment/hook
set+show pairs, `tmux_bind/unbind/list_keys`, the `copy_*` trio, the
`stream_*` six, `tmux_link/unlink/break/join/find/pipe`, `tmux_version`,
`tmux_list_targets`, `tmux_server_info`, `tmux_display_message`.

### 6.4 — Cross-cutting implementation notes

[x] **Annotations stay clean.** `tmux_kill` added to `DESTRUCTIVE`; old
      per-kind kill names dropped. (Class LIST / `tmux_list` is the 6.2 merge,
      not done here.) No per-`kind` annotation branching needed.
[x] **Validation pattern.** `require_kind(kind, allowed)` helper in `_util.py`
      (raises `ValueError`); reused across all six merged tools. `tmux_kill`
      additionally validates `id` presence for non-server kinds.
[x] **Tests.** `tests/test_tools_argv.py` now has a `merged.py` section with
      parametrized argv assertions per `kind` plus bad-`kind` / missing-`id`
      ToolError cases; the integration suites (test_p5, test_functional) call
      the merged names.
[x] **Back-compat decision (made).** (a) **clean break** — old per-kind names
      dropped, no aliases. Done pre-PyPI-publish (4.6), version already
      0.2.0.dev0, so no existing client configs to break.
[x] **Docs.** README tool table + the `## tmux` server instructions
      (`server.py` INSTRUCTIONS) updated to describe the `kind`-discriminated
      tools. Count drops from 71 to 63 (strong merges only; the 6.2 list merge
      would take it to ~59).

---

## P7 — Toolsets

**Problem.** Even after P6, the curated surface is ~60 tools — high for an MCP
server (most ship 5–25; only large API-wrappers like GitHub's reach this tier,
and they manage it with *toolsets*). The cost is twofold: ~35k tokens of schema
resident per session, and selection accuracy degrading as near-identical tools
pile up. P6 consolidation shrinks the count linearly; toolset gating instead
shrinks what any one session pays for, which is what actually drives both costs.

**Goal.** Ship a lean always-on `core` plus opt-in toolsets, selected via a
`toolsets = [...]` config key (or `MCP_TMUX_TOOLSETS` env var). A default
session loads ~16 tools instead of 63 (~75% cut; ~26k schema tokens deferred),
with the heavy families loading only on request. The escape hatch matters:
`tmux_command` is always in `core`, so anything gated out is still reachable via
raw passthrough — a lean core never hard-blocks a task.

### 7.0 — Toolset inventory (60 tools → 1 core + 8 opt-in)

> **Note (post-6.2):** the per-kind list tools are now the single merged
> `tmux_list(kind=session/window/client/buffer)`. A single tool can't be split
> across toolsets, so `tmux_list` goes in **core** (it subsumes the old
> `list_sessions`/`list_windows` core picks *and* the `list_buffers`/
> `list_clients` opt-in picks). Drop those four names from the lists below and
> add `tmux_list` to core; `buffers`/`clients` toolsets shrink by one each.

**`core`** (always loaded) — the create → send → read loop + escape hatch:
- passthrough: `tmux_command`, `tmux_query`, `tmux_version`, `tmux_list_targets`
- sessions: `tmux_has_session`, `tmux_new_session`
- io: `tmux_send_keys`, `tmux_capture_pane`
- windows: `tmux_new_window`
- panes: `tmux_list_panes`, `tmux_split_window`
- merged: `tmux_list`, `tmux_kill`, `tmux_rename`, `tmux_select`

Opt-in (47 across 8 groups):

| Toolset | # | Tools |
|---|---|---|
| `automation` | 3 | `wait_for_text`, `wait_for_idle`, `run` — **candidate to promote into core** for an agent server |
| `layout` | 15 | `next_layout`, `move_window`, `select_layout`, `resize_pane`, `set_pane_title`, `clear_history`, `swap`, `last`, `respawn`, `link_window`, `unlink_window`, `break_pane`, `join_pane`, `find_window`, `pipe_pane` |
| `buffers` | 6 | `list_buffers`, `set_buffer`, `paste_buffer`, `delete_buffer`, `save_buffer`, `load_buffer` |
| `config` | 8 | `set_option`, `show_options`, `set_environment`, `show_environment`, `set_hook`, `show_hooks`, `run_shell`, `if_shell` |
| `keybindings` | 3 | `list_keys`, `bind_key`, `unbind_key` |
| `copymode` | 3 | `copy_mode`, `copy_scroll`, `copy_search` |
| `clients` | 3 | `list_clients`, `server_info`, `display_message` |
| `stream` | 6 | `stream_start`, `stream_resize`, `stream_read`, `stream_send`, `stream_list`, `stream_stop` |

### 7.1 — Implementation ✅ DONE

New `toolsets.py` holds the inventory (`CORE` + 8 `OPTIONAL` groups = 60 tools),
`select_toolsets()` (env > config > default precedence) and `resolve_enabled()`
(maps selected names → active tool-name set; `core` always in; `"all"` = full;
unknown → `ValueError`). Gating is one `toolset_gate(mcp, enabled)` helper in
`_util.py` — a drop-in for `mcp.tool()` that registers a tool only if its name
is enabled; each module took a one-line change (`tool = toolset_gate(...)`,
`@mcp.tool()` → `@tool()`) and gained an `enabled` param, so within-module
splits (`options`, `windows`, `panes`, `merged`) fall out for free with no file
split. 16 new tests in `test_toolsets.py`; 187 passing; ruff + mypy clean.

[x] **Config plumbing.** `toolsets: list[str]` in config (top-level, validated)
      + `MCP_TMUX_TOOLSETS` env var (comma-separated, wins over config). Default
      `["core", "automation"]`; `["all"]` = full surface; unknown name →
      `ValueError` listing valid toolsets.
[x] **Gating in `register_all`.** `register_all(mcp, runner, enabled)` threads
      the active tool-name set into every module's `register(mcp, runner,
      enabled)`; `server.build_server` resolves it via
      `resolve_enabled(select_toolsets(cfg))`.
[x] **Module split.** Not needed — `toolset_gate` gates per-tool by name, so
      `options.py` keeps `set/show_options` (`config`) and the buffer family
      (`buffers`) in one file with no split.
[x] **Within-module core picks.** `windows`/`panes`/`merged` contribute some
      tools to `core` and the rest to `layout`; the per-tool gate handles this
      with no all-or-nothing branching.
[x] **Tests.** `test_toolsets.py` parametrizes over selections: `core`-only
      registers exactly `CORE` (15), default = core+automation (18), each opt-in
      adds exactly its names, groups are disjoint, `["all"]` = full 60, env
      overrides config, unknown name errors.
[x] **Docs.** README tool table regrouped by toolset + a "Selecting toolsets"
      section + config-schema comment; `server.py` INSTRUCTIONS note the default
      set and how to widen it.

### 7.2 — Optional: dynamic mode (defer unless static proves too rigid)

[ ] **`tmux_enable_toolset(name)` meta-tool** (GitHub's approach) — lets a client
      starting with only `core` pull in `stream`/`layout`/… mid-session without a
      restart. More plumbing (runtime re-registration); only worth it if static
      config is too inflexible in practice.

---

## 6. Notes / decisions to revisit

[x] 6.1. `send-keys` into an **attached** session is visible to whoever's
  attached — by design, and documented as a feature (shared sessions for
  pairing; see the README). Decided **against** a `confirm`/`dry_run` flag on
  `send_keys`/`run`: it stays a low-level primitive, and the shared-terminal
  caution about destructive writes is handled in the README instead. The explicit
  `kill_*`/`delete_buffer`/`clear_history` tools remain flagged via the
  `DESTRUCTIVE` set (`tools/_util.py`) so clients can prompt.
[x] 6.2. `$(...)` in `send_keys text` is evaluated by the **remote pane's shell**, not
  locally (the SSH layer shell-quotes the tmux argv). Documented in the README
  ("Where `send_keys` text is evaluated") to avoid surprises.
[ ] 6.3. Field separator is **TAB** (tmux escapes control bytes like `0x1f` to octal in
  format output, but passes TAB through). Revisit only if a real value ever
  contains a tab.

---

## P8 — Glama Tool Definition Quality critiques (2026-06-12 scorecard)

From Glama's automated score (overall **C / 58%**): Server Coherence **A** and
Maintenance **A**, but **Tool Definition Quality** averaged only **3.6/5** across
the 71 tools (all toolsets enabled) and dragged the overall down. Problems flagged
below — critiques only, no fixes yet.

### Lowest-scoring tools

[x] 8.1. **`tmux_swap_window` — 1.9/5.** Extremely terse description ("Swap two
      windows"); zero parameter documentation, no behavioral disclosure, no usage
      guidance. → DONE: rewrote the `tmux_swap` docstring (the merged successor)
      with behavioral disclosure (positions swap, contents travel, geometry
      preserved), per-parameter docs incl. target syntax, and when-to-use guidance
      vs `tmux_move_window` / `tmux_select_layout`.
[x] 8.2. **`tmux_kill_pane` — 2.2/5.** Single-sentence description; lacks essential
      context about its parameters (`target_pane`, `target`) and under-documents
      the destructive behavior beyond the annotation. → DONE: enriched the merged
      `tmux_kill` docstring (no-confirm/SIGHUP irreversibility, last-pane/last-window
      cascade, `id`/`target` params, pointer to `tmux_respawn`).
[x] 8.3. **`tmux_select_pane` — 2.3/5.** Two inputs, both undocumented; no
      behavioral context and no usage scenarios. → DONE: `tmux_select` docstring now
      explains focus-only semantics (selecting a pane selects its window; not needed
      before targeting by id), documents `id`/`target`, points to `tmux_last`.
[x] 8.4. **`tmux_resize_pane` — 2.4/5.** Six parameters (only one required), yet the
      description doesn't clarify how the parameters interact or explain the
      directional-resizing mechanics. → DONE: docstring now explains per-direction
      cell pushes, adjacent-pane shrink, combining directions, absolute-cell clamping,
      `target_pane`/`target`, and `tmux_select_layout` as the bulk alternative.
[x] 8.5. **`tmux_swap_pane` — 2.4/5.** No explanation of what "swap" means in
      practice or of the parameter semantics (`src`, `dst`, `target` left
      undefined). → DONE in 8.1: pane swap is the `kind="pane"` half of the merged
      `tmux_swap`, whose rewritten docstring covers swap semantics and `src`/`dst`/`target`.

### Cross-cutting patterns

[x] 8.6. **Schema coverage is 0%** (flagged repeatedly): `inputSchema` parameters
      carry no `description` fields, and the tool descriptions don't compensate by
      explaining parameter semantics, valid formats, or behavioral side effects.
      → DONE: the MCP SDK builds inputSchema from the signature and ignores docstring
      prose, so descriptions now ride on `Annotated[type, Field(description=...)]`.
      Added a shared `tools/_params.py` (Target / TargetPane / TargetPaneOpt aliases)
      reused across the surface, plus inline Field descriptions for tool-specific
      params. Coverage is now 100% (236/236 params across all 60 tools); ruff + mypy
      clean, 187 tests pass.
[ ] 8.7. **No "when to use this vs. alternatives" guidance** — systematically
      absent across the low scorers.
[ ] 8.8. **Low scorers cluster in the layout toolset** (window/pane
      swap/select/resize/kill) — the curated core tools (capture, send-keys, wait
      helpers) are richly documented and are what earned the coherence A.
