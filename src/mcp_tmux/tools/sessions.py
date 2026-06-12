"""Session tools."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from ..formats import FIELD_SEP, parse_records
from ._params import Target
from ._util import toolset_gate


def register(mcp, runner, enabled) -> None:
    tool = toolset_gate(mcp, enabled)

    @tool()
    async def tmux_has_session(
        session: Annotated[str, Field(description="Session name or id to check for.")],
        target: Target = None,
    ) -> dict:
        """Check whether a session exists. Returns {"exists": bool}."""
        result = await runner.run(["has-session", "-t", session], target=target)
        return {"exists": result.ok}

    @tool()
    async def tmux_new_session(
        name: Annotated[
            str | None,
            Field(description="Name for the new session (-s)."),
        ] = None,
        start_directory: Annotated[
            str | None,
            Field(description="Working directory for the first pane (-c)."),
        ] = None,
        command: Annotated[
            str | None,
            Field(description="Command to run as the first pane instead of the default shell."),
        ] = None,
        width: Annotated[
            int | None,
            Field(description="Width in columns for the detached session (-x)."),
        ] = None,
        height: Annotated[
            int | None,
            Field(description="Height in rows for the detached session (-y)."),
        ] = None,
        detached: Annotated[
            bool,
            Field(description="Create the session detached (-d); default True."),
        ] = True,
        attach_or_create: Annotated[
            bool,
            Field(description="Reuse an existing session of this name instead of erroring."),
        ] = False,
        env: Annotated[
            dict[str, str] | None,
            Field(description="Session environment variables (-e KEY=VAL; tmux 3.0+)."),
        ] = None,
        target: Target = None,
    ) -> dict:
        """Create a new session (detached by default).

        Optional `command` runs as the first pane's command; `start_directory`
        sets the working dir; `width`/`height` size the detached session.
        `attach_or_create=True` makes the call idempotent: if a session named
        `name` already exists it is reused (its id/name returned) instead of
        erroring, otherwise it is created. tmux's own `new-session -A` *attaches*
        the existing session, which a headless MCP server has no terminal to do;
        so the reuse here is a detached `has-session` check (the create-time
        options below only apply when the session is actually created).
        `env` (-e KEY=VAL, tmux 3.0+) sets session environment variables before
        the first command launches; ignored with a note on older tmux.
        Returns the created (or existing) session's {"id", "name"}.
        """
        if attach_or_create and name:
            exists = await runner.run(["has-session", "-t", name], target=target)
            if exists.ok:
                out = await runner.run_checked(
                    [
                        "display-message",
                        "-p",
                        "-t",
                        name,
                        f"#{{session_id}}{FIELD_SEP}#{{session_name}}",
                    ],
                    target=target,
                )
                rec = parse_records(out, ["id", "name"])
                return rec[0] if rec else {"id": "", "name": name}

        args = ["new-session"]
        if detached:
            args.append("-d")
        if name:
            args += ["-s", name]
        if start_directory:
            args += ["-c", start_directory]
        if width:
            args += ["-x", str(width)]
        if height:
            args += ["-y", str(height)]
        notes = []
        if env:
            caps = await runner.capabilities(target)
            if caps.has("new_session_env"):
                for key, value in env.items():
                    args += ["-e", f"{key}={value}"]
            else:
                notes.append("env ignored: new-session -e requires tmux 3.0+")
        args += ["-P", "-F", f"#{{session_id}}{FIELD_SEP}#{{session_name}}"]
        if command:
            args.append(command)
        out = await runner.run_checked(args, target=target)
        rec = parse_records(out, ["id", "name"])
        result: dict[str, Any] = rec[0] if rec else {"id": "", "name": name or ""}
        if notes:
            result["notes"] = notes
        return result
