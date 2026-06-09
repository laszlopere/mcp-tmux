"""Synchronization helpers — wait for output, wait for quiet, run-and-collect.

Driving a shell blind (send -> sleep -> capture) is fragile. These tools poll
``capture-pane`` so an agent can wait for a specific result, wait for output to
settle, or run a command and get back just its output + exit status.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid

from ._capture import capture_text
from ._util import toolset_gate

# Default polling cadence (seconds) for all waiters.
DEFAULT_POLL = 0.25


def _extract_run_output(content: str, beg: str, end: str) -> tuple[int, str] | None:
    """Pure parser for tmux_run captures.

    Looks for a line that is exactly the BEG marker, then a later line of the
    form ``<END> <exit_code>``. Returns ``(exit_code, output_between)`` or None
    if the bracket isn't complete yet. The command's own echoed input line is
    ignored because it never equals the bare marker.
    """
    end_re = re.compile(rf"^{re.escape(end)} (-?\d+)\s*$")
    lines = content.split("\n")
    beg_idx: int | None = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if beg_idx is None:
            if s == beg:
                beg_idx = i
            continue
        m = end_re.match(s)
        if m:
            return int(m.group(1)), "\n".join(lines[beg_idx + 1 : i])
    return None


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_wait_for_text(
        target_pane: str,
        pattern: str,
        timeout: float = 30.0,
        regex: bool = False,
        history: int | None = None,
        poll_interval: float = DEFAULT_POLL,
        target: str | None = None,
    ) -> dict:
        """Wait until `pattern` appears in a pane, or `timeout` seconds elapse.

        `pattern` is a substring by default, or a regular expression when
        regex=True. `history` optionally includes that many scrollback lines in
        each check (-S -history). Returns {"matched", "elapsed", "content"} where
        content is the last capture taken.

        Caveat: the pane also shows the command you typed, so a pattern can match
        your own echoed input line rather than the command's output. To wait for
        a command to *finish* and get just its output, prefer `tmux_run`.
        """
        if regex:
            rx = re.compile(pattern)
            match = lambda s: rx.search(s) is not None  # noqa: E731
        else:
            match = lambda s: pattern in s  # noqa: E731

        start = -abs(history) if history else None
        t0 = time.monotonic()
        deadline = t0 + timeout
        content = ""
        while True:
            content = await capture_text(runner, target_pane, target=target, start=start)
            if match(content):
                return {
                    "matched": True,
                    "elapsed": round(time.monotonic() - t0, 3),
                    "content": content,
                }
            if time.monotonic() >= deadline:
                return {
                    "matched": False,
                    "elapsed": round(time.monotonic() - t0, 3),
                    "content": content,
                }
            await asyncio.sleep(poll_interval)

    @tool()
    async def tmux_wait_for_idle(
        target_pane: str,
        idle_seconds: float = 1.0,
        timeout: float = 30.0,
        poll_interval: float = DEFAULT_POLL,
        target: str | None = None,
    ) -> dict:
        """Wait until a pane's visible content stops changing.

        Returns once the capture is unchanged for `idle_seconds` (i.e. output
        has settled), or after `timeout`. Returns {"idle", "elapsed", "content"}.
        """
        t0 = time.monotonic()
        deadline = t0 + timeout
        last = await capture_text(runner, target_pane, target=target)
        last_change = time.monotonic()
        while True:
            await asyncio.sleep(poll_interval)
            now = time.monotonic()
            cur = await capture_text(runner, target_pane, target=target)
            if cur != last:
                last = cur
                last_change = now
            if now - last_change >= idle_seconds:
                return {"idle": True, "elapsed": round(now - t0, 3), "content": cur}
            if now >= deadline:
                return {"idle": False, "elapsed": round(now - t0, 3), "content": cur}

    @tool()
    async def tmux_run(
        target_pane: str,
        command: str,
        timeout: float = 30.0,
        history: int = 2000,
        poll_interval: float = DEFAULT_POLL,
        target: str | None = None,
    ) -> dict:
        """Run a shell command in a pane and return just its output + exit code.

        Types `command` into the pane bracketed by unique markers, then polls
        until the command finishes, and returns the text printed between the
        markers. This is the reliable alternative to send-keys + sleep + capture.

        Intended for NON-interactive commands at a shell prompt. Returns
        {"completed", "exit_code", "output", "elapsed"}; on timeout, completed is
        False and output holds the raw capture so far.
        """
        tok = uuid.uuid4().hex[:8]
        beg = f"__MCP_BEG_{tok}__"
        end = f"__MCP_END_{tok}__"
        # printf the BEG marker, run the command, then printf END + its $?.
        line = f"printf '%s\\n' {beg}; {command}; printf '%s %d\\n' {end} \"$?\""
        await runner.run_checked(["send-keys", "-t", target_pane, "-l", line], target=target)
        await runner.run_checked(["send-keys", "-t", target_pane, "Enter"], target=target)

        t0 = time.monotonic()
        deadline = t0 + timeout
        content = ""
        while True:
            content = await capture_text(runner, target_pane, target=target, start=-abs(history))
            found = _extract_run_output(content, beg, end)
            if found is not None:
                exit_code, output = found
                return {
                    "completed": True,
                    "exit_code": exit_code,
                    "output": output,
                    "elapsed": round(time.monotonic() - t0, 3),
                }
            if time.monotonic() >= deadline:
                return {
                    "completed": False,
                    "exit_code": None,
                    "output": content,
                    "elapsed": round(time.monotonic() - t0, 3),
                    "error": "timed out waiting for command completion",
                }
            await asyncio.sleep(poll_interval)
