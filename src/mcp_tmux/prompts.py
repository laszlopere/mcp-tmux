"""User-initiated MCP prompts for common tmux workflows.

Prompts are *user-controlled*: hosts surface them as slash commands / menu
picks the human deliberately triggers (vs. tools, which the model calls, and
resources, which the app reads). Each one expands into a short instruction the
model then carries out with the regular tmux tools.
"""

from __future__ import annotations


def register(mcp) -> None:
    @mcp.prompt(title="Set up a tmux dev layout")
    def dev_layout(
        session: str = "dev",
        command: str = "",
        target: str = "local",
    ) -> str:
        """Create a detached session split into editor + shell panes.

        Args:
            session: name for the new session (default "dev").
            command: optional command to run in the top/editor pane.
            target: "local", a named profile, or an ssh destination.
        """
        run = f' Then run `{command}` in the editor pane.' if command else ""
        return (
            f"Set up a tmux dev layout on target {target!r}:\n"
            f"1. Create a detached session named {session!r} "
            f"(reuse it if it already exists).\n"
            f"2. Split its window into two panes: a tall top 'editor' pane and "
            f"a short bottom 'shell' pane.\n"
            f"3. Leave the shell pane selected and idle at the prompt.\n"
            f"4. Report the session, window, and pane ids you created.{run}"
        )
