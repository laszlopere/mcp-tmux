"""Shared pane-capture helper used by the io and wait tools."""

from __future__ import annotations


async def capture_text(
    runner,
    target_pane: str | None,
    *,
    target: str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    escapes: bool = False,
    join: bool = True,
    trim: bool = True,
) -> str:
    """Capture a pane's contents as text, honoring version-gated flags.

    `start`/`end` map to capture-pane -S/-E (scrollback range). `trim` drops the
    trailing blank padding lines tmux emits below the last line of real content.
    """
    args = await capture_args(
        runner, target_pane, target=target, start=start, end=end, escapes=escapes, join=join
    )
    content = await runner.run_checked(args, target=target)
    return trim_padding(content) if trim else content


async def capture_args(
    runner,
    target_pane: str | None,
    *,
    target: str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    escapes: bool = False,
    join: bool = True,
) -> list[str]:
    """The ``capture-pane -p`` command for :func:`capture_text`."""
    caps = await runner.capabilities(target)
    args = ["capture-pane", "-p"]
    if target_pane:
        args += ["-t", target_pane]
    if escapes and caps.has("capture_escapes"):
        args.append("-e")
    if join and caps.has("capture_join"):
        args.append("-J")
    if start is not None:
        args += ["-S", str(start)]
    if end is not None:
        args += ["-E", str(end)]
    return args


def trim_padding(content: str) -> str:
    """Drop the blank lines tmux pads a capture with below the real content."""
    lines = content.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)
