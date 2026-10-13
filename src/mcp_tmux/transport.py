"""Control mode as the command transport (opt-in, tmux 3.2+).

By default every tmux command is a one-shot ``tmux ...`` process (``ssh host
tmux ...`` for a remote target). With ``transport = "control"`` a target keeps
one ``tmux -C attach -f no-output,ignore-size`` client open and writes commands
to it as lines, reading each reply from its ``%begin``/``%end``/``%error``
block. That skips a process start (and an SSH session) per command.

The price: the control client is a real client attached to a real session. That
session reports itself attached, attaching makes it tmux's most recent session
(what a plain ``tmux attach`` or a command without ``-t`` picks), and commands
sent without ``-t`` default to it. Commands that block the client's command
queue or act on the client itself never go over the channel (see
:func:`needs_oneshot`), nor does anything when the channel can't attach (no
server, no sessions); those run one-shot.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import TmuxResult
    from .targets import Target

TRANSPORTS = ("oneshot", "control")

# Commands that must not go over the shared control client: they block its
# command queue (and so every later call), are interactive, or act on the
# client itself (attach/detach/switch, its size, the server under it).
# new-session runs one-shot too: without -d it would attach our client.
# show-buffer/save-buffer print raw data, and a reply block's lines can't tell
# whether it ended in a newline.
_ONESHOT_COMMANDS = (
    "attach-session",
    "choose-buffer",
    "choose-client",
    "choose-tree",
    "command-prompt",
    "confirm-before",
    "customize-mode",
    "detach-client",
    "display-menu",
    "display-panes",
    "display-popup",
    "find-window",
    "if-shell",
    "kill-server",
    "lock-client",
    "lock-server",
    "lock-session",
    "new-session",
    "refresh-client",
    "run-shell",
    "save-buffer",
    "show-buffer",
    "source-file",
    "start-server",
    "suspend-client",
    "switch-client",
    "wait-for",
)
_ONESHOT_ALIASES = {
    "attach",
    "confirm",
    "detach",
    "displayp",
    "if",
    "lock",
    "lockc",
    "locks",
    "menu",
    "new",
    "popup",
    "refresh",
    "run",
    "saveb",
    "showb",
    "source",
    "start",
    "suspendc",
    "switchc",
    "wait",
}

# Arguments that need no quoting on a tmux command line. Not "=" (a leading
# NAME=value is an environment assignment), "~" (home directory) or "#".
_SAFE_ARG = re.compile(r"[A-Za-z0-9_@%+,./:-]+")


def needs_oneshot(commands: list[list[str]]) -> bool:
    """True if any command must run as a one-shot process."""
    for command in commands:
        if not command or command[0].startswith("-"):
            return True  # global flags (e.g. -V) or an empty command
        name = command[0]
        # tmux accepts any unambiguous prefix of a command name.
        if name in _ONESHOT_ALIASES or any(c.startswith(name) for c in _ONESHOT_COMMANDS):
            return True
        if (name == "display" or "display-message".startswith(name)) and not any(
            a.startswith("-") and not a.startswith("--") and "p" in a for a in command[1:]
        ):
            return True  # without -p the message goes to our client's status line
    return False


def split_cli_args(argv: list[str]) -> list[list[str]]:
    """Split a tmux command-line argv into commands, as tmux itself does.

    An argument ending in ``;`` ends a command (the ``;`` is dropped); one
    ending in ``\\;`` keeps a literal ``;``. Inverse of
    :func:`mcp_tmux.runner.join_commands`.
    """
    commands: list[list[str]] = [[]]
    for arg in argv:
        if arg.endswith(";"):
            arg = arg[:-1]
            if arg.endswith("\\"):
                commands[-1].append(arg[:-1] + ";")
                continue
            if arg:
                commands[-1].append(arg)
            commands.append([])
        else:
            commands[-1].append(arg)
    if not commands[-1]:
        commands.pop()
    return commands


def quote_arg(arg: str) -> str:
    """Quote one argument for a tmux command line (tmux 3.0+ parser)."""
    if _SAFE_ARG.fullmatch(arg) and not arg.startswith("%"):
        return arg
    if "'" not in arg and not any(ord(c) < 0x20 or c == "\x7f" for c in arg):
        return f"'{arg}'"  # nothing is special inside single quotes
    # Double quotes: escape \ " $ and write control characters as octal (a
    # control line can't contain a raw newline).
    out = []
    for c in arg:
        if c in '\\"$':
            out.append("\\" + c)
        elif ord(c) < 0x20 or c == "\x7f":
            out.append(f"\\{ord(c):03o}")
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def command_line(commands: list[list[str]]) -> str:
    """One control-mode input line running ``commands`` in order."""
    return " ; ".join(" ".join(quote_arg(a) for a in command) for command in commands)


class ControlUnavailable(Exception):
    """The control client could not attach (no server, no session, old tmux)."""


@dataclass
class _Request:
    expected: int  # commands in the line; one reply block each
    future: asyncio.Future
    output: list[str] = field(default_factory=list)
    done: int = 0


class ControlChannel:
    """One ``tmux -C`` client carrying commands for one target and socket."""

    CONNECT_TIMEOUT = 10.0

    def __init__(self, target: Target, tmux_bin: str = "tmux"):
        self.target = target
        self.tmux_bin = tmux_bin
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._ready: asyncio.Future | None = None
        self._requests: deque[_Request] = deque()
        self._block: tuple[str, list[str]] | None = None  # (end-line tokens, lines)
        self.connected_at: float | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self._ready_ok()

    def _ready_ok(self) -> bool:
        return (
            self._ready is not None
            and self._ready.done()
            and not self._ready.cancelled()
            and self._ready.exception() is None
        )

    async def connect(self) -> None:
        """Attach; raises :class:`ControlUnavailable` if tmux refuses."""
        # -N: attach must not start a server when none is running (tmux would,
        # then fail with "no sessions" and leave that server holding our pipes).
        argv = self.target.build_argv(
            ["-N", "-C", "attach", "-f", "no-output,ignore-size"], tmux_bin=self.tmux_bin
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=64 * 1024 * 1024,  # one reply line can be a whole joined pane row
            )
        except OSError as exc:
            raise ControlUnavailable(str(exc)) from exc
        self._ready = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read_loop())
        try:
            await asyncio.wait_for(asyncio.shield(self._ready), self.CONNECT_TIMEOUT)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise ControlUnavailable("timed out attaching the control client") from exc
        except ControlUnavailable:
            await self.close()
            raise
        self.connected_at = time.monotonic()

    async def run(self, commands: list[list[str]], timeout: float) -> TmuxResult:
        """Run ``commands`` (one line, stopping at the first error)."""
        from .runner import TmuxResult

        assert self._proc is not None and self._proc.stdin is not None
        request = _Request(len(commands), asyncio.get_running_loop().create_future())
        self._requests.append(request)
        self._proc.stdin.write((command_line(commands) + "\n").encode("utf-8"))
        try:
            await self._proc.stdin.drain()
            return await asyncio.wait_for(asyncio.shield(request.future), timeout)
        except asyncio.TimeoutError:
            # Something is stuck in the client's queue; every later call would
            # wait behind it. Start over on the next call.
            await self.close()
            return TmuxResult("", f"command timed out after {timeout:g}s", 124)
        except (ConnectionError, BrokenPipeError):
            await self.close()
            return TmuxResult("", "control connection closed before the reply", 1)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                self._handle_line(raw.decode("utf-8", "replace").rstrip("\n"))
        except (ValueError, asyncio.LimitOverrunError):  # pragma: no cover - huge line
            pass
        finally:
            self._fail_all("control connection closed before the reply")

    def _handle_line(self, line: str) -> None:
        if self._block is not None:
            end_tokens, lines = self._block
            kind, _, rest = line.partition(" ")
            if kind in ("%end", "%error") and rest == end_tokens:
                self._block = None
                self._finish_block(rest.rsplit(" ", 1)[-1] == "1", kind == "%error", lines)
            else:
                lines.append(line)
            return
        if line.startswith("%begin "):
            self._block = (line[len("%begin ") :], [])
        elif line.startswith("%exit"):
            self._fail_all("control client exited")
        # Any other notification (%session-changed, %window-add, ...) is noise.

    def _finish_block(self, from_client: bool, is_error: bool, lines: list[str]) -> None:
        from .runner import TmuxResult

        if not from_client:
            # Flag 0: our own attach (the first block) or a hook's commands.
            if self._ready is not None and not self._ready.done():
                if is_error:
                    self._ready.set_exception(ControlUnavailable("\n".join(lines)))
                else:
                    self._ready.set_result(None)
            return
        if not self._requests:  # pragma: no cover - a reply nobody asked for
            return
        request = self._requests[0]
        if not is_error:
            request.output += lines
            request.done += 1
            if request.done < request.expected:
                return
        self._requests.popleft()
        if request.future.done():  # its caller gave up
            return
        stdout = "".join(f"{ln}\n" for ln in request.output)
        if is_error:
            stderr = "".join(f"{ln}\n" for ln in lines) or "tmux command failed\n"
            request.future.set_result(TmuxResult(stdout, stderr, 1))
        else:
            request.future.set_result(TmuxResult(stdout, "", 0))

    def _fail_all(self, why: str) -> None:
        from .runner import TmuxResult

        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(ControlUnavailable(why))
        while self._requests:
            request = self._requests.popleft()
            if not request.future.done():
                request.future.set_result(TmuxResult("", why, 1))

    CLOSE_TIMEOUT = 2.0

    async def close(self) -> None:
        """Detach: end our stdin, so tmux drops the client; kill it if it hangs.

        Killing alone isn't enough over SSH: the multiplexing master keeps the
        session open, so the remote ``tmux -C`` would stay attached (and a
        ``kill-server`` would wait for it). EOF reaches it through the master.
        """
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                proc.stdin.close()
            deadline = time.monotonic() + self.CLOSE_TIMEOUT
            while proc.returncode is None and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            if proc.returncode is None:
                proc.kill()
                while proc.returncode is None:  # see runner._kill
                    await asyncio.sleep(0.005)
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
        self._fail_all("control connection closed before the reply")
