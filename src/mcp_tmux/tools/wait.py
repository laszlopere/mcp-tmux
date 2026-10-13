"""Synchronization helpers — wait for output, wait for quiet, run-and-collect.

Driving a shell blind (send -> sleep -> capture) is fragile. These tools poll
``capture-pane`` so an agent can wait for a specific result, wait for output to
settle, or run a command and get back just its output + exit status.
``tmux_run`` polls only as a fallback: the typed command line signals a
``tmux wait-for`` channel when it finishes.
"""

from __future__ import annotations

import asyncio
import re
import shlex
import time
import uuid
from typing import Annotated

from pydantic import Field

from ._capture import capture_args, capture_text, trim_padding
from ._params import Target, TargetPane
from ._util import toolset_gate

# Default polling cadence (seconds) for all waiters.
DEFAULT_POLL = 0.25


def _extract_run_output(content: str, beg: str, end: str) -> tuple[int, str] | None:
    """Pure parser for tmux_run captures.

    Looks for a line ending in the BEG marker, then the last later line ending
    in ``<END> <exit_code>``. Returns ``(exit_code, output_between)`` or None
    if the bracket isn't complete yet.

    A marker needn't start its line: ``capture-pane -J`` joins a row that
    wrapped at exactly the pane width with the next one, and output without a
    trailing newline runs into END; text before END is output. The echoed
    command line never ends in a marker (each is followed by ``;`` there), and
    taking the last END skips marker-like text inside the output.
    """
    end_re = re.compile(rf"{re.escape(end)} (-?\d+)\s*$")
    lines = content.split("\n")
    beg_idx = next((i for i, ln in enumerate(lines) if ln.rstrip().endswith(beg)), None)
    if beg_idx is None:
        return None
    for i in range(len(lines) - 1, beg_idx, -1):
        m = end_re.search(lines[i])
        if m:
            output = [*lines[beg_idx + 1 : i], lines[i][: m.start()]]
            if not output[-1].strip():
                output.pop()
            return int(m.group(1)), "\n".join(output)
    return None


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_wait_for_text(
        target_pane: TargetPane,
        pattern: Annotated[
            str,
            Field(description="Text to wait for: a substring, or a regex when regex=True."),
        ],
        timeout: Annotated[
            float,
            Field(description="Max seconds to wait before giving up."),
        ] = 30.0,
        regex: Annotated[
            bool,
            Field(description="Treat pattern as a regular expression."),
        ] = False,
        history: Annotated[
            int | None,
            Field(description="Include this many scrollback lines in each check (-S)."),
        ] = None,
        poll_interval: Annotated[
            float,
            Field(description="Seconds between capture checks."),
        ] = DEFAULT_POLL,
        target: Target = None,
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
        target_pane: TargetPane,
        idle_seconds: Annotated[
            float,
            Field(description="Seconds the content must stay unchanged to count as idle."),
        ] = 1.0,
        timeout: Annotated[
            float,
            Field(description="Max seconds to wait before giving up."),
        ] = 30.0,
        poll_interval: Annotated[
            float,
            Field(description="Seconds between capture checks."),
        ] = DEFAULT_POLL,
        target: Target = None,
    ) -> dict:
        """Wait until a pane's visible content stops changing.

        Returns once the capture is unchanged for `idle_seconds` (i.e. output
        has settled), or after `timeout`.

        Use this when you don't know the exact completion text; if you do, wait
        on it with `tmux_wait_for_text`, and to run a command and get just its
        output + exit code, use `tmux_run`.

        Returns {"idle", "elapsed", "content"}.
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
        target_pane: TargetPane,
        command: Annotated[
            str,
            Field(description="Non-interactive shell command to run at the pane's prompt."),
        ],
        timeout: Annotated[
            float,
            Field(description="Max seconds to wait for the command to finish."),
        ] = 30.0,
        history: Annotated[
            int,
            Field(description="Scrollback lines to scan when locating the output markers."),
        ] = 2000,
        poll_interval: Annotated[
            float,
            Field(
                description=(
                    "Seconds between capture checks, used only when the pane's "
                    "shell can't signal tmux (e.g. it is ssh'd to another host)."
                )
            ),
        ] = DEFAULT_POLL,
        target: Target = None,
    ) -> dict:
        """Run a shell command in a pane and return just its output + exit code.

        Types `command` into the pane bracketed by unique markers, waits for it
        to finish, and returns the text printed between the markers. This is
        the reliable alternative to send-keys + sleep + capture.

        The command line also runs `tmux wait-for -S` before and after the
        command, so completion is signalled rather than polled for. If the
        shell can't reach this tmux server (no `tmux` on its PATH, no $TMUX,
        or the pane is logged in to another host), the pane is polled instead.

        Intended for NON-interactive commands at a shell prompt. Returns
        {"completed", "exit_code", "output", "elapsed"}; on timeout, completed is
        False and output holds the raw capture so far.
        """
        tok = uuid.uuid4().hex[:8]
        beg = f"__MCP_BEG_{tok}__"
        end = f"__MCP_END_{tok}__"
        channel = f"mcp-run-{tok}"
        # The shell finds its own server through $TMUX. Without it (e.g. the
        # pane is ssh'd elsewhere) tmux would signal some other host's or
        # socket's server, so don't; errors are silenced. Either way tmux_run
        # then polls.
        tmux = shlex.quote(runner.tmux_bin)
        signal = f'test -n "$TMUX" && {tmux} wait-for -S {channel} 2>/dev/null'
        # Signal, printf the BEG marker, run the command, printf END + its $?,
        # signal again.
        line = (
            f"{signal}; printf '%s\\n' {beg}; {command}; printf '%s %d\\n' {end} \"$?\"; {signal}"
        )
        capture = await capture_args(runner, target_pane, target=target, start=-abs(history))

        t0 = time.monotonic()
        deadline = t0 + timeout

        def finished(content: str) -> dict | None:
            found = _extract_run_output(content, beg, end)
            if found is None:
                return None
            exit_code, output = found
            return {
                "completed": True,
                "exit_code": exit_code,
                "output": output,
                "elapsed": round(time.monotonic() - t0, 3),
            }

        # Type the line and wait for its first signal in one invocation. Until
        # that arrives, fall back to polling the pane. The loop below owns the
        # deadline and cancels the probe; its own timeout is only a backstop.
        probe = asyncio.create_task(
            runner.run_checked(
                ["send-keys", "-t", target_pane, "-l", line],
                ["send-keys", "-t", target_pane, "Enter"],
                ["wait-for", channel],
                target=target,
                timeout=timeout + 30,
            )
        )
        content = ""
        try:
            while True:
                await asyncio.wait({probe}, timeout=poll_interval)
                if probe.done():
                    probe.result()  # raises if send-keys failed
                    break
                content = await capture_text(
                    runner, target_pane, target=target, start=-abs(history)
                )
                if (result := finished(content)) is not None:
                    return result
                if time.monotonic() >= deadline:
                    return _run_timed_out(content, t0)
        finally:
            if not probe.done():
                probe.cancel()
                await asyncio.gather(probe, return_exceptions=True)
                await _release(runner, channel, target)
            elif not probe.cancelled():
                probe.exception()  # retrieved, so asyncio doesn't log it

        # The shell can signal: block until the second signal, then capture.
        remaining = max(0.0, deadline - time.monotonic())
        waited = await runner.run(["wait-for", channel], capture, target=target, timeout=remaining)
        if waited.ok:
            content = trim_padding(waited.stdout)
            if (result := finished(content)) is not None:
                return result
        else:
            await _release(runner, channel, target)
        # Signalled but the markers aren't in the capture (scrolled out of
        # `history`?), or timed out: one last look.
        content = await capture_text(runner, target_pane, target=target, start=-abs(history))
        if (result := finished(content)) is not None:
            return result
        return _run_timed_out(content, t0)


def _run_timed_out(content: str, t0: float) -> dict:
    return {
        "completed": False,
        "exit_code": None,
        "output": content,
        "elapsed": round(time.monotonic() - t0, 3),
        "error": "timed out waiting for command completion",
    }


async def _release(runner, channel: str, target: str | None) -> None:
    """Wake a ``wait-for`` we gave up on.

    Killing a local waiter removes it from the server, but over SSH the remote
    tmux client keeps waiting after the ssh client dies.
    """
    if runner.resolve(target).is_remote:
        await runner.run(["wait-for", "-S", channel], target=target)
