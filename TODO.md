# mcp-tmux — TODO / Roadmap

Status: **Phases 0–3 + P0 + P1 complete and verified** (passthrough + curated
tools, local + SSH remote, resources, P0 polish, and the P1 wait/sync helpers).
Local and remote (pipnode over SSH) both tested end-to-end; 44 passing tests.
What follows is the plan for the next steps, ordered by value.

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

## P2 — Broaden the curated tool surface

Passthrough already covers everything, but first-class tools help discovery:

- [ ] Clients/server: `tmux_list_clients`, `tmux_server_info` (uptime, pid,
      socket), `tmux_display_message` (already partly via `tmux_query`).
- [ ] Window/pane plumbing: `tmux_link_window`, `tmux_unlink_window`,
      `tmux_break_pane`, `tmux_join_pane`, `tmux_find_window`,
      `tmux_pipe_pane` (stream a pane to a command — pairs well with logging).
- [ ] Hooks & scripting: `tmux_set_hook`, `tmux_run_shell`, `tmux_if_shell`.
- [ ] Keys/bindings: `tmux_list_keys`, `tmux_bind_key`, `tmux_unbind_key`.
- [ ] Copy-mode helpers: enter copy-mode, scroll, search, copy selection.
- [ ] Make **resources target-aware** (currently local-only): e.g.
      `tmux://{target}/sessions`.

## P3 — Phase 4: streaming via control mode (`tmux -C`)

- [ ] Persistent control-mode connection per target (`tmux -C attach`/`new`),
      parsing `%output`, `%window-add`, `%layout-change`, etc.
- [ ] Expose as **MCP notifications / a long-poll tool** so a client can watch a
      pane live instead of polling `capture-pane`.
- [ ] Lifecycle: connection pool, reconnect, teardown; gate on
      `capabilities.has("control_mode")`.
- [ ] Keep one-shot CLI as the universal default; control mode is opt-in.

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
