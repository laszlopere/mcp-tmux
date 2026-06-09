"""I/O tools: sending keystrokes and reading pane contents.

These are the highest-value tools — the ones an agent uses to drive a shell and
observe results.
"""

from __future__ import annotations

from ._capture import capture_text
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_send_keys(
        target_pane: str,
        text: str | None = None,
        keys: list[str] | None = None,
        enter: bool = False,
        literal: bool = True,
        target: str | None = None,
    ) -> dict:
        """Send input to a pane.

        Two modes (provide exactly one of `text` / `keys`):

        * `text`: a literal string. With literal=True (default) it is sent
          exactly as written via `send-keys -l` (so "C-c" types those three
          characters). Set literal=False to let tmux interpret key names inside
          the string.
        * `keys`: a list of tmux key tokens interpreted by tmux, e.g.
          ["C-c"], ["Escape"], ["Up", "Up", "Enter"].

        `enter=True` appends an Enter keypress after the input — the usual way to
        "run" a typed command. Returns {"sent": True}.
        """
        if (text is None) == (keys is None):
            raise ValueError("Provide exactly one of 'text' or 'keys'.")

        if text is not None:
            args = ["send-keys", "-t", target_pane]
            if literal:
                args.append("-l")
            args.append(text)
            await runner.run_checked(args, target=target)
        else:
            assert keys is not None  # exactly one of text/keys is set (checked above)
            args = ["send-keys", "-t", target_pane, *keys]
            await runner.run_checked(args, target=target)

        if enter:
            # Enter is a key name, sent in its own (non-literal) call so it works
            # regardless of the literal flag used above.
            await runner.run_checked(["send-keys", "-t", target_pane, "Enter"], target=target)
        return {"sent": True, "pane": target_pane}

    @tool()
    async def tmux_capture_pane(
        target_pane: str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        escapes: bool = False,
        join: bool = True,
        trim: bool = True,
        target: str | None = None,
    ) -> dict:
        """Capture (read) the visible contents and/or scrollback of a pane.

        This is the primary way to observe output. `start`/`end` select a
        scrollback line range (-S/-E): negative or "-" for the start of history,
        e.g. start=-100 grabs the last 100+ lines. `escapes=True` keeps ANSI
        color/escape sequences (-e). `join=True` rejoins wrapped lines (-J).
        `trim=True` (default) drops the empty padding lines tmux emits below the
        last line of real content; set trim=False to keep the raw capture.

        Returns {"content": <captured text>}.
        """
        content = await capture_text(
            runner,
            target_pane,
            target=target,
            start=start,
            end=end,
            escapes=escapes,
            join=join,
            trim=trim,
        )
        return {"content": content}
