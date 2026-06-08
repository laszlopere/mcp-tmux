# mcp-tmux — TODO / Roadmap

Status: **Phases 0–3 + P0 + P1 + P2 + P3 complete and verified** (passthrough +
curated tools, local + SSH remote, resources, P0 polish, the P1 wait/sync
helpers, the P2 broadened tool surface + target-aware resources, and the P3
control-mode streaming layer). Local and remote (pipnode over SSH) tested
end-to-end; 60 passing tests. What follows is the plan for the next steps.

---

## P0 — Correctness & ergonomics gaps found during the build ✅ DONE

- [x] **Tool annotations** (`readOnlyHint` / `destructiveHint`). Applied
      centrally in `tools/_util.py:finalize_tools` via `ToolAnnotations`, tagging
      read-only and destructive tools by name. Verified in `list_tools`.
- [x] **`trim` option on `tmux_capture_pane`.** `trim=True` (default) strips the
      trailing blank padding lines tmux emits; `trim=False` keeps the raw
      capture. Covered by an integration test.
- [x] **Structured errors.** `TmuxError`/`ValueError` are mapped to FastMCP
      `ToolError` (clean message incl. exit status, no traceback) in
      `tools/_util.py:_wrap_errors`.
- [x] **Numeric coercion.** `formats.coerce_records` converts known
      numeric/boolean fields (`windows`, `attached`, `panes`, `width/height`,
      `pid`, `created`, …) to int/bool; applied in `runner.list_records`.

## P1 — The big agent-experience win: wait/synchronize helpers ✅ DONE

Driving a shell blind (send → sleep → capture) is fragile. Implemented in
`tools/wait.py`; verified locally and against pipnode's `testMCP` session.

- [x] **`tmux_wait_for_text(target_pane, pattern, timeout, regex=False, history)`**
      — polls `capture-pane` until `pattern` (substring or regex) appears.
      Returns {matched, elapsed, content}. (Caveat documented: a pattern can
      match the echoed input line; use `tmux_run` to wait for completion.)
- [x] **`tmux_wait_for_idle(target_pane, idle_seconds, timeout)`** — returns once
      the capture is unchanged for `idle_seconds`. Returns {idle, elapsed, content}.
- [x] **`tmux_run(target_pane, command, timeout, history)`** — brackets the
      command with unique markers (`__MCP_BEG/END_<token>__`), polls to
      completion, and returns just the output + exit code: {completed,
      exit_code, output, elapsed}. Pure parser `_extract_run_output` is
      unit-tested; the marker approach avoids the input-echo false match.

## P2 — Broaden the curated tool surface ✅ DONE

Passthrough already covers everything, but first-class tools help discovery.
Implemented across `tools/{clients,plumbing,hooks,keys,copymode}.py`; resources
made target-aware in `resources.py`. 8 new integration tests in `test_p2.py`
(52 passing total).

- [x] Clients/server (`tools/clients.py`): `tmux_list_clients`,
      `tmux_server_info` (pid, socket path, version), `tmux_display_message`
      (shows a message on the client's status line — distinct from
      `tmux_query`'s `display-message -p`).
- [x] Window/pane plumbing (`tools/plumbing.py`): `tmux_link_window`,
      `tmux_unlink_window`, `tmux_break_pane`, `tmux_join_pane`,
      `tmux_find_window` (non-interactive name/title search — tmux's own
      `find-window` opens an interactive chooser), `tmux_pipe_pane`.
- [x] Hooks & scripting (`tools/hooks.py`): `tmux_set_hook`, `tmux_show_hooks`,
      `tmux_run_shell`, `tmux_if_shell`.
- [x] Keys/bindings (`tools/keys.py`): `tmux_list_keys`, `tmux_bind_key`,
      `tmux_unbind_key`. The key-table flag is `-T` (2.1+) vs `-t` (older),
      gated via the `key_tables` capability.
- [x] Copy-mode helpers (`tools/copymode.py`): `tmux_copy_mode` (enter/exit),
      `tmux_copy_scroll`, `tmux_copy_search`. Driven by `send-keys -X` (gated on
      the `send_keys_X` / tmux 2.4+ capability). To *read* scrolled-back content,
      `tmux_capture_pane(start=...)` is simpler than copying a selection.
- [x] **Resources are target-aware**: added `tmux://{target}/sessions`,
      `tmux://{target}/{session}/windows`, `tmux://{target}/{window}/panes`
      alongside the existing local-only ones.

## P3 — Phase 4: streaming via control mode (`tmux -C`) ✅ DONE

Implemented in `control.py` (transport: pure parsers, `ControlConnection`,
`ControlManager` pool) and `tools/stream.py` (agent-facing tools). 8 new tests
in `test_control.py` (deterministic parser/framing unit tests + one real
`tmux -C` end-to-end); 60 passing total.

- [x] Persistent control-mode connection per (target, session) via
      `tmux -C attach -t <session>`. The reader loop parses `%output`,
      `%window-add`/`%window-close`/`%layout-change`, `%session-changed`,
      `%exit`, … and the `%begin`/`%end`/`%error` reply framing. Output is
      un-escaped (`\\ooo` octal + `\\\\`) to bytes → UTF-8, with ANSI stripped by
      default. Events are sequenced into a ring buffer.
- [x] Exposed as a **long-poll tool** (more universal than server-initiated
      notifications): `tmux_stream_read` blocks until new events or timeout and
      auto-advances a cursor; filterable by `pane`/`kinds`. Also
      `tmux_stream_start` (idempotent), `tmux_stream_send` (run a command over
      the connection, get its reply), `tmux_stream_list`, `tmux_stream_stop`.
- [x] Lifecycle: `ControlManager` pool keyed by (target, session); idempotent
      start reuses a live connection and replaces a dead one; `stop`/`stop_all`
      detach the control client (the session keeps running) and cancel tasks;
      gated on `capabilities.has("control_mode")`.
- [x] One-shot CLI remains the universal default; control mode is opt-in.

  Possible follow-ups (not blocking): automatic reconnect on unexpected
  `%exit`; surface `tmux_stream_*` over real MCP notifications for clients that
  support them; wire `ControlManager.stop_all` into a FastMCP shutdown hook.

## P4 — Quality, packaging, CI

- [ ] **Unit tests for the tools layer** (argv assembly per tool via a fake
      runner). Currently covered only indirectly through integration/smoke.
- [ ] **tmux version-matrix CI** — run integration tests in containers against
      tmux 1.8 / 2.x / 3.x to prove the 1.8+ universality claim and catch
      format-var/flag drift.
- [ ] **Lint & types** — add `ruff` + `mypy`; fix the few `# type: ignore`s.
- [ ] **Packaging** — lockfile, `python_requires` smoke on 3.10, publish to PyPI
      so `uvx mcp-tmux` works for real; tag v0.1.0.
- [ ] **CONTRIBUTING / config docs** — document named-target profiles
      (e.g. a `[targets.pipnode]` example with jump host + identity).

## Notes / decisions to revisit

- `send-keys` into an **attached** session (like pipnode's `testMCP`) is
  visible to whoever's attached — by design. Consider a `confirm`/`dry_run`
  flag for destructive or attached-session writes.
- `$(...)` in `send_keys text` is evaluated by the **remote pane's shell**, not
  locally (the SSH layer shell-quotes the tmux argv). Document this clearly to
  avoid surprises.
- Field separator is **TAB** (tmux escapes control bytes like `0x1f` to octal in
  format output, but passes TAB through). Revisit only if a real value ever
  contains a tab.
