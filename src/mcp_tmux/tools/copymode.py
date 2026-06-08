"""Copy-mode helpers: enter, scroll, search, and exit.

Copy mode is how tmux lets you scroll back and select text. The scroll/search
helpers drive it with ``send-keys -X <command>``, which needs tmux 2.4+; on
older tmux use the raw ``tmux_command`` passthrough with literal copy-mode keys.
To read scrolled-back content, `tmux_capture_pane` with a `start` line range is
usually simpler than copying a selection.
"""

from __future__ import annotations

# direction -> the send-keys -X copy-mode command it maps to.
_SCROLL = {
    "up": "scroll-up",
    "down": "scroll-down",
    "page-up": "page-up",
    "page-down": "page-down",
    "halfpage-up": "halfpage-up",
    "halfpage-down": "halfpage-down",
    "top": "history-top",
    "bottom": "history-bottom",
}


async def _require_X(runner, target: str | None) -> None:
    caps = await runner.capabilities(target)
    if not caps.has("send_keys_X"):
        raise ValueError(
            "copy-mode scroll/search needs tmux 2.4+ (send-keys -X); "
            "use tmux_command with literal copy-mode keys on this version."
        )


def register(mcp, runner) -> None:
    @mcp.tool()
    async def tmux_copy_mode(
        target_pane: str,
        page_up: bool = False,
        exit: bool = False,
        target: str | None = None,
    ) -> dict:
        """Enter (or exit) copy mode in a pane.

        page_up=True (-u) scrolls up one page on entry. exit=True leaves copy
        mode instead (sends the copy-mode `cancel` command, tmux 2.4+).
        Returns {"copy_mode": bool}.
        """
        if exit:
            await _require_X(runner, target)
            await runner.run_checked(
                ["send-keys", "-X", "-t", target_pane, "cancel"], target=target
            )
            return {"copy_mode": False, "pane": target_pane}
        args = ["copy-mode"]
        if page_up:
            args.append("-u")
        args += ["-t", target_pane]
        await runner.run_checked(args, target=target)
        return {"copy_mode": True, "pane": target_pane}

    @mcp.tool()
    async def tmux_copy_scroll(
        target_pane: str,
        direction: str = "up",
        amount: int = 1,
        target: str | None = None,
    ) -> dict:
        """Scroll within copy mode.

        `direction` is one of: up, down, page-up, page-down, halfpage-up,
        halfpage-down, top, bottom. `amount` repeats the step (ignored for
        top/bottom). Enters copy mode first if needed. Requires tmux 2.4+.
        """
        cmd = _SCROLL.get(direction)
        if cmd is None:
            raise ValueError("direction must be one of: " + ", ".join(_SCROLL))
        await _require_X(runner, target)
        await runner.run_checked(["copy-mode", "-t", target_pane], target=target)
        reps = 1 if direction in ("top", "bottom") else max(1, amount)
        for _ in range(reps):
            await runner.run_checked(["send-keys", "-X", "-t", target_pane, cmd], target=target)
        return {"scrolled": direction, "amount": reps, "pane": target_pane}

    @mcp.tool()
    async def tmux_copy_search(
        target_pane: str,
        pattern: str,
        backward: bool = True,
        target: str | None = None,
    ) -> dict:
        """Search the scrollback in copy mode for `pattern`.

        backward=True searches towards the top of history (the usual direction
        for finding recent output); set backward=False to search forward. Enters
        copy mode first if needed. Requires tmux 2.4+. Returns {"searched": ...}.
        """
        await _require_X(runner, target)
        await runner.run_checked(["copy-mode", "-t", target_pane], target=target)
        cmd = "search-backward" if backward else "search-forward"
        await runner.run_checked(
            ["send-keys", "-X", "-t", target_pane, cmd, pattern], target=target
        )
        return {"searched": pattern, "backward": backward, "pane": target_pane}
