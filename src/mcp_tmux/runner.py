"""TmuxRunner: the single execution funnel for all tmux commands.

Every tmux invocation — curated tool, passthrough, or introspection — goes
through here, so quoting, timeouts, target resolution, version detection, and
error mapping live in exactly one place.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from .capabilities import Capabilities
from .config import DEFAULT_TIMEOUT
from .formats import build_format, coerce_records, parse_records
from .targets import Target, resolve_target
from .transport import (
    TRANSPORTS,
    ControlChannel,
    ControlUnavailable,
    needs_oneshot,
    split_cli_args,
)

# After a failed attach (no server, no session yet), run one-shot for this long
# before trying the control client again.
CONTROL_RETRY_DELAY = 1.0


class TmuxError(Exception):
    """A tmux command exited non-zero. Carries the mapped, human message."""

    def __init__(self, message: str, *, exit_code: int, stderr: str, argv: list[str]):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.argv = argv


@dataclass
class TmuxResult:
    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def join_commands(commands: tuple[list[str], ...]) -> list[str]:
    """Join tmux commands into one argv, separated by ``;`` arguments.

    tmux treats any argument ending in ``;`` as a command separator (dropping
    the ``;``), even with a single command, and turns a trailing ``\\;`` back
    into a literal ``;``. So every argument ending in ``;`` gets that escape,
    which makes it reach the command unchanged (``"ls;"`` -> ``"ls\\;"``).
    """
    argv: list[str] = []
    for i, command in enumerate(commands):
        if i:
            argv.append(";")
        argv += [a[:-1] + "\\;" if a.endswith(";") else a for a in command]
    return argv


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill ``proc`` and wait for it to exit.

    Not ``proc.wait()``: that also waits for the pipes to close, and an SSH
    multiplexing client hands its stdio to the ControlMaster, which keeps them
    open until the remote command ends, however long that takes.
    """
    proc.kill()
    while proc.returncode is None:
        await asyncio.sleep(0.005)


def _map_error(stderr: str, exit_code: int) -> str:
    """Translate common tmux stderr into a clearer message."""
    s = stderr.strip().lower()
    if "no server running" in s or ("no such file or directory" in s and "tmux" in s):
        return "No tmux server is running on this target."
    if "can't find session" in s:
        return f"tmux: {stderr.strip()} (the session does not exist)"
    if "can't find pane" in s or "can't find window" in s:
        return f"tmux: {stderr.strip()}"
    if "unknown command" in s:
        return f"tmux: {stderr.strip()}"
    if not stderr.strip():
        return f"tmux exited with status {exit_code}."
    return f"tmux: {stderr.strip()}"


class TmuxRunner:
    """Executes tmux commands locally or over SSH and caches capabilities."""

    def __init__(self, config: dict[str, Any], tmux_bin: str = "tmux"):
        self.config = config
        self.tmux_bin = tmux_bin
        self._default_timeout = float(
            (config.get("defaults", {}) or {}).get("timeout", DEFAULT_TIMEOUT)
        )
        self._caps_cache: dict[str, Capabilities] = {}
        self._transport_override: dict[str, str] = {}  # target name -> transport
        self._channels: dict[str, ControlChannel] = {}  # Target.key -> channel
        self._channel_locks: dict[str, asyncio.Lock] = {}
        self._control_retry_at: dict[str, float] = {}

    def resolve(self, target: str | None) -> Target:
        return resolve_target(target, self.config)

    def configured_transport(self, target: str | None) -> str:
        """The transport the config file selects for ``target``."""
        tgt = self.resolve(target)
        spec = (self.config.get("targets", {}) or {}).get(tgt.name, {}) or {}
        defaults = self.config.get("defaults", {}) or {}
        return str(spec.get("transport") or defaults.get("transport") or "oneshot")

    def transport(self, target: str | None) -> str:
        """The transport in effect for ``target`` (runtime choice, else config)."""
        name = self.resolve(target).name
        return self._transport_override.get(name) or self.configured_transport(target)

    async def set_transport(self, target: str | None, transport: str) -> None:
        """Switch ``target`` to ``transport`` until the server restarts."""
        if transport not in TRANSPORTS:
            raise ValueError(f"transport must be one of: {', '.join(TRANSPORTS)}")
        tgt = self.resolve(target)
        self._transport_override[tgt.name] = transport
        if transport == "oneshot":
            await self._close_channel(tgt.key)
        self._control_retry_at.pop(tgt.key, None)

    def control_connected(self, target: str | None) -> bool:
        channel = self._channels.get(self.resolve(target).key)
        return channel is not None and channel.alive

    async def aclose(self) -> None:
        """Close every control client."""
        for key in list(self._channels):
            await self._close_channel(key)

    async def _close_channel(self, key: str) -> None:
        channel = self._channels.pop(key, None)
        if channel is not None:
            await channel.close()

    async def _control_channel(self, target: str | None) -> ControlChannel | None:
        """The target's control client, attaching one if needed; None to go one-shot."""
        tgt = self.resolve(target)
        channel = self._channels.get(tgt.key)
        if channel is not None and channel.alive:
            return channel
        if time.monotonic() < self._control_retry_at.get(tgt.key, 0.0):
            return None
        if not (await self.capabilities(target)).has("control_transport"):
            return None
        lock = self._channel_locks.setdefault(tgt.key, asyncio.Lock())
        async with lock:
            channel = self._channels.get(tgt.key)
            if channel is not None and channel.alive:
                return channel
            await self._close_channel(tgt.key)  # dead: server restarted, session gone
            channel = ControlChannel(tgt, self.tmux_bin)
            try:
                await channel.connect()
            except ControlUnavailable:
                self._control_retry_at[tgt.key] = time.monotonic() + CONTROL_RETRY_DELAY
                return None
            self._channels[tgt.key] = channel
            return channel

    def _argv(self, commands: tuple[list[str], ...], target: str | None, verbatim: bool):
        if verbatim:
            if len(commands) != 1:
                raise ValueError("verbatim takes exactly one argument list")
            tmux_args = list(commands[0])
        else:
            tmux_args = join_commands(commands)
        return self.resolve(target).build_argv(tmux_args, tmux_bin=self.tmux_bin)

    async def run(
        self,
        *commands: list[str],
        target: str | None = None,
        timeout: float | None = None,
        verbatim: bool = False,
    ) -> TmuxResult:
        """Run ``tmux <command> [; <command> ...]`` and return stdout/stderr/exit_code.

        Several commands run in one tmux invocation (one process, one SSH round
        trip). tmux stops at the first failing command; stdout is the
        concatenated output of the commands that ran. Arguments are passed
        literally (see :func:`join_commands`); ``verbatim=True`` hands a single
        argument list to tmux untouched, so ``;`` there separates commands.

        With the ``control`` transport the commands go to the target's control
        client instead, unless they must run one-shot (see
        :mod:`mcp_tmux.transport`) or no client can attach.
        """
        timeout = timeout if timeout is not None else self._default_timeout
        control = self.transport(target) == "control"
        if control:
            cmds = split_cli_args(list(commands[0])) if verbatim else [list(c) for c in commands]
            if cmds and not needs_oneshot(cmds):
                channel = await self._control_channel(target)
                if channel is not None:
                    return await channel.run(cmds, timeout)
            if any(c and "kill-server".startswith(c[0]) and len(c[0]) >= 5 for c in cmds):
                # Detach first: a server with a client still attached outlives
                # kill-server for a moment, and a new-session right after it
                # would reach the dying server.
                await self._close_channel(self.resolve(target).key)
        result = await self._exec(self._argv(commands, target, verbatim), timeout)
        if (
            not result.ok
            and "server exited unexpectedly" in result.stderr
            and any(c and c[0] == "new-session" for c in commands)
        ):
            # Reached a server that was exiting (kill-server returns before
            # the server is gone while clients are still attached). Nothing was
            # created on it; the retry starts a fresh server.
            await asyncio.sleep(0.1)
            result = await self._exec(self._argv(commands, target, verbatim), timeout)
        if control and result.ok and any(c and c[0] == "new-session" for c in commands):
            # There is a session to attach to now.
            self._control_retry_at.pop(self.resolve(target).key, None)
        return result

    async def run_checked(
        self,
        *commands: list[str],
        target: str | None = None,
        timeout: float | None = None,
        verbatim: bool = False,
    ) -> str:
        """Run and return stdout, raising :class:`TmuxError` on non-zero exit."""
        result = await self.run(*commands, target=target, timeout=timeout, verbatim=verbatim)
        if not result.ok:
            raise TmuxError(
                _map_error(result.stderr, result.exit_code),
                exit_code=result.exit_code,
                stderr=result.stderr,
                argv=self._argv(commands, target, verbatim),
            )
        return result.stdout

    async def _exec(self, argv: list[str], timeout: float) -> TmuxResult:
        try:
            # Our stdin is the MCP stdio channel: keep ssh (and its ControlMaster) off it.
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # tmux or ssh not installed
            missing = argv[0]
            return TmuxResult("", f"{missing}: command not found ({exc})", 127)

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _kill(proc)
            return TmuxResult("", f"command timed out after {timeout:g}s", 124)
        except asyncio.CancelledError:
            # Don't leave the process behind (e.g. a blocked `tmux wait-for`).
            await _kill(proc)
            raise

        return TmuxResult(
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
            proc.returncode if proc.returncode is not None else -1,
        )

    async def capabilities(self, target: str | None = None) -> Capabilities:
        """Detect and cache the target's tmux version/capabilities."""
        tgt = self.resolve(target)
        cached = self._caps_cache.get(tgt.key)
        if cached is not None:
            return cached
        result = await self.run(["-V"], target=target)
        # `tmux -V` writes the version to stdout; fall back to stderr just in case.
        raw = (result.stdout or result.stderr).strip() or "unknown"
        caps = Capabilities.from_output(raw)
        self._caps_cache[tgt.key] = caps
        return caps

    async def list_records(
        self,
        list_cmd: list[str],
        fields: list[tuple],
        *,
        target: str | None = None,
    ) -> list[dict[str, object]]:
        """Run a ``list-*`` command with a gated ``-F`` format, parse it, and
        coerce known numeric/boolean fields to int/bool."""
        caps = await self.capabilities(target)
        fmt, keys = build_format(fields, caps)
        stdout = await self.run_checked([*list_cmd, "-F", fmt], target=target)
        records = parse_records(stdout, keys)
        return coerce_records(records, fields, caps)
