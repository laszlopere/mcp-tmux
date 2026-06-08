"""Control-mode (`tmux -C`) streaming: a persistent connection per target.

The one-shot CLI in :mod:`~mcp_tmux.runner` is the universal default. Control
mode is the opt-in upgrade: a long-lived ``tmux -C attach`` subprocess that
streams asynchronous notifications — ``%output`` (pane wrote data),
``%window-add``/``%window-close``/``%layout-change``, etc. — so a client can
*watch* a pane live instead of polling ``capture-pane``.

This module is transport: parsing (pure, unit-tested helpers), one
:class:`ControlConnection` (the subprocess + reader loop + an event ring buffer
read via long-poll), and a :class:`ControlManager` pool keyed by target+session.
The agent-facing tools live in :mod:`mcp_tmux.tools.stream`.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass

from .runner import TmuxError, TmuxRunner
from .targets import Target

# Reply-block / notification line prefixes in the control protocol.
_BEGIN = "%begin"
_END = "%end"
_ERROR = "%error"

# Strip ANSI/VT escape sequences from decoded pane output: CSI (incl. the
# bracketed-paste ?2004h/l), OSC (terminated by BEL or ST), and lone two-byte
# escapes. Pane output is otherwise plain text once unescaped.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI ... final byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[@-Z\\-_]"  # two-byte escapes
)


def unescape_output_bytes(s: str) -> bytes:
    """Reverse tmux's ``%output`` escaping (``\\\\`` and 3-digit octal) to bytes.

    tmux escapes a literal backslash as ``\\\\`` and any other non-literal byte
    as ``\\ooo`` (three octal digits, e.g. ESC -> ``\\033``). Decoding to bytes
    (not chars) lets a later UTF-8 decode reassemble multibyte characters.
    """
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "\\":
                out.append(0x5C)
                i += 2
                continue
            trip = s[i + 1 : i + 4]
            if len(trip) == 3 and all(ch in "01234567" for ch in trip):
                out.append(int(trip, 8))
                i += 4
                continue
            out.append(ord(c))
            i += 1
            continue
        out += c.encode("utf-8")
        i += 1
    return bytes(out)


def decode_output(s: str, *, strip_ansi: bool = True) -> str:
    """Unescape a ``%output`` payload to text, optionally stripping ANSI codes
    and normalising CRLF to LF."""
    text = unescape_output_bytes(s).decode("utf-8", "replace")
    if strip_ansi:
        text = _ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "")


@dataclass
class Event:
    """One control-mode notification, sequenced for cursor-based reads."""

    seq: int
    type: str  # directive name without the leading '%', e.g. "output"
    raw: str  # the original line (escapes intact)
    pane: str | None = None  # tmux pane id (%N)
    window: str | None = None  # tmux window id (@N)
    session: str | None = None  # tmux session id ($N)
    data: str | None = None  # payload (raw/escaped for output; text otherwise)

    def as_dict(self, *, strip_ansi: bool = True) -> dict:
        d: dict = {"seq": self.seq, "type": self.type}
        if self.pane:
            d["pane"] = self.pane
        if self.window:
            d["window"] = self.window
        if self.session:
            d["session"] = self.session
        if self.type == "output" and self.data is not None:
            d["data"] = decode_output(self.data, strip_ansi=strip_ansi)
        elif self.data:
            d["data"] = self.data
        return d


def parse_event(line: str, seq: int) -> Event:
    """Parse one ``%notification`` line into an :class:`Event`.

    ``%output %<pane> <data>`` is special-cased (data is kept escaped and decoded
    lazily). For every other directive we record the type and pull out the first
    window (``@``), pane (``%``), or session (``$``) id token we see.
    """
    body = line[1:] if line.startswith("%") else line
    directive, _, rest = body.partition(" ")
    if directive == "output":
        pane, _, data = rest.partition(" ")
        return Event(seq=seq, type="output", raw=line, pane=pane or None, data=data)
    ev = Event(seq=seq, type=directive, raw=line, data=rest or None)
    for tok in rest.split():
        if tok.startswith("@") and ev.window is None:
            ev.window = tok
        elif tok.startswith("%") and ev.pane is None:
            ev.pane = tok
        elif tok.startswith("$") and ev.session is None:
            ev.session = tok
    return ev


class ControlConnection:
    """A single ``tmux -C attach -t <session>`` subprocess and its event stream."""

    def __init__(
        self,
        runner: TmuxRunner,
        target: Target,
        session: str,
        *,
        buffer_size: int = 5000,
        reconnect: bool = True,
        max_reconnects: int = 10,
    ):
        self.runner = runner
        self.target = target
        self.session = session
        self.stream_id = f"cm-{uuid.uuid4().hex[:8]}"
        self.alive = False
        self.cursor = 0  # server-side auto-advance read cursor
        self.reconnect = reconnect
        self.reconnects = 0  # successful auto-reconnects so far
        self._max_reconnects = max_reconnects
        self._reconnect_base_delay = 0.5  # seconds; doubles per attempt, capped
        self._stability_window = 2.0  # a connection alive this long resets flaps
        self._flaps = 0  # consecutive reconnects that died almost immediately
        self._last_spawn_at = 0.0
        self._closing = False  # set by close(): suppresses auto-reconnect
        self._size: tuple[int, int] | None = None  # last requested client size
        self._buffer_size = buffer_size
        self._events: deque[Event] = deque(maxlen=buffer_size)
        self._last_seq = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._cond = asyncio.Condition()
        self._reply: list[str] | None = None  # accumulating a %begin..%end block
        self._pending: deque[asyncio.Future] = deque()  # send_command waiters
        self._stderr_tail = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.target.key, self.session)

    async def start(self) -> None:
        """Spawn the initial control client. Errors propagate to the caller."""
        await self._spawn()

    async def _spawn(self) -> None:
        argv = self.target.build_argv(
            ["-C", "attach", "-t", self.session], tmux_bin=self.runner.tmux_bin
        )
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.alive = True
        self._last_spawn_at = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr_reader = asyncio.create_task(self._drain_stderr())
        if self._size is not None:
            # Re-apply the client size on (re)connect, in the background so the
            # reply doesn't block the reader/reconnect path.
            w, h = self._size
            asyncio.create_task(self._bg_refresh(w, h))

    async def _bg_refresh(self, width: int, height: int) -> None:
        try:
            await self.send_command(f"refresh-client -C {width}x{height}")
        except Exception:  # pragma: no cover - best-effort
            pass

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        try:
            data = await self._proc.stderr.read()
            if data:
                self._stderr_tail = data.decode("utf-8", "replace")[-2000:]
        except Exception:  # pragma: no cover - defensive
            pass

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                await self._handle_line(raw.decode("utf-8", "replace").rstrip("\n"))
        finally:
            async with self._cond:
                self.alive = False
                while self._pending:
                    fut = self._pending.popleft()
                    if not fut.done():
                        fut.set_exception(
                            TmuxError(
                                "control connection closed",
                                exit_code=-1,
                                stderr=self._stderr_tail,
                                argv=[],
                            )
                        )
                self._cond.notify_all()
        # The stream dropped (process exited / EOF). Unless we're closing on
        # purpose, try to transparently reconnect. This task drives the attempt
        # and, on success, hands off to a fresh reader task spawned by _spawn().
        if self._closing or not self.reconnect:
            return
        # Flap guard: a connection that stayed up a while gets a fresh budget;
        # one that dies almost immediately counts as a flap, so a permanently
        # broken target (e.g. the session is gone) stops after max_reconnects.
        if time.monotonic() - self._last_spawn_at >= self._stability_window:
            self._flaps = 0
        else:
            self._flaps += 1
        if self._flaps > self._max_reconnects:
            await self._emit_synthetic(
                "disconnected", "connection unstable; giving up"
            )
            return
        await self._reconnect()

    async def _reconnect(self) -> None:
        """Re-establish a dropped connection with capped exponential backoff.

        Preserves the stream_id, event buffer, and sequence numbers so existing
        cursors keep working. Emits a synthetic ``reconnected`` event on success
        or ``disconnected`` after giving up, so long-pollers notice either way.
        """
        delay = self._reconnect_base_delay
        for _ in range(self._max_reconnects):
            if self._closing:
                return
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                return
            delay = min(delay * 2, 5.0)
            if self._closing:
                return
            try:
                await self._spawn()
            except Exception:
                continue
            self.reconnects += 1
            await self._emit_synthetic("reconnected", f"attempt {self.reconnects}")
            return
        await self._emit_synthetic("disconnected", "reconnect attempts exhausted")

    async def _emit_synthetic(self, kind: str, data: str) -> None:
        async with self._cond:
            self._last_seq += 1
            self._events.append(
                Event(seq=self._last_seq, type=kind, raw="", data=data)
            )
            self._cond.notify_all()

    async def _handle_line(self, line: str) -> None:
        # --- command-reply framing (%begin .. %end/%error) -------------------
        if line.startswith(_BEGIN):
            self._reply = []
            return
        if line.startswith(_END) or line.startswith(_ERROR):
            body = "\n".join(self._reply or [])
            is_error = line.startswith(_ERROR)
            self._reply = None
            if self._pending:
                fut = self._pending.popleft()
                if not fut.done():
                    if is_error:
                        fut.set_exception(
                            TmuxError(
                                body or "control command failed",
                                exit_code=1,
                                stderr=body,
                                argv=[],
                            )
                        )
                    else:
                        fut.set_result(body)
            return
        if self._reply is not None:
            self._reply.append(line)
            return
        # --- asynchronous notification ---------------------------------------
        if not line.startswith("%"):
            return  # stray line outside any block; ignore
        async with self._cond:
            self._last_seq += 1
            self._events.append(parse_event(line, self._last_seq))
            if line.startswith("%exit"):
                self.alive = False
            self._cond.notify_all()

    async def send_command(self, command: str, timeout: float = 10.0) -> str:
        """Run a tmux command through the control connection; return its reply
        text. Raises :class:`TmuxError` on ``%error`` or if the connection is
        closed."""
        if not self.alive or not self._proc or not self._proc.stdin:
            raise TmuxError(
                "control connection is not alive", exit_code=-1, stderr="", argv=[]
            )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending.append(fut)
        self._proc.stdin.write((command + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        return await asyncio.wait_for(fut, timeout)

    async def read(
        self,
        *,
        cursor: int | None = None,
        timeout: float = 10.0,
        max_events: int = 500,
        pane: str | None = None,
        kinds: list[str] | None = None,
        strip_ansi: bool = True,
    ) -> dict:
        """Long-poll new events past ``cursor`` (default: the auto cursor).

        Blocks up to ``timeout`` seconds for at least one new event, then returns
        ``{events, cursor, alive, lagged}``. ``cursor`` advances over the
        contiguous range consumed regardless of ``pane``/``kinds`` filtering, so
        no events are silently skipped.
        """
        auto = cursor is None
        async with self._cond:
            if auto:
                cursor = self.cursor

            def ready() -> bool:
                return (not self.alive) or self._last_seq > cursor  # type: ignore[operator]

            if not ready():
                try:
                    await asyncio.wait_for(self._cond.wait_for(ready), timeout)
                except asyncio.TimeoutError:
                    pass

            fresh = [e for e in self._events if e.seq > cursor]  # type: ignore[operator]
            lagged = bool(fresh) and fresh[0].seq > cursor + 1  # type: ignore[operator]
            window = fresh[:max_events]
            new_cursor = window[-1].seq if window else cursor
            if auto:
                self.cursor = new_cursor  # type: ignore[assignment]

        out = [
            e
            for e in window
            if (pane is None or e.pane == pane)
            and (kinds is None or e.type in kinds)
        ]
        return {
            "events": [e.as_dict(strip_ansi=strip_ansi) for e in out],
            "cursor": new_cursor,
            "alive": self.alive,
            "lagged": lagged,
        }

    async def refresh_size(self, width: int, height: int, timeout: float = 10.0) -> None:
        """Set this control client's size (refresh-client -C WxH).

        Records the size so it is re-applied automatically after a reconnect.
        Without this a control client defaults to 80x24 and wraps ``%output``
        oddly for wider panes.
        """
        self._size = (width, height)
        await self.send_command(f"refresh-client -C {width}x{height}", timeout=timeout)

    def info(self) -> dict:
        panes = sorted({e.pane for e in self._events if e.pane})
        return {
            "stream_id": self.stream_id,
            "session": self.session,
            "target": self.target.name,
            "alive": self.alive,
            "buffered": len(self._events),
            "last_seq": self._last_seq,
            "panes": panes,
            "reconnects": self.reconnects,
            "size": list(self._size) if self._size else None,
        }

    async def close(self) -> None:
        """Detach the control client (does not kill the session) and clean up."""
        self._closing = True  # suppress auto-reconnect from the reader loop
        self.alive = False
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:  # pragma: no cover - already gone
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:  # pragma: no cover - defensive
                self._proc.kill()
                await self._proc.wait()
        for task in (self._reader, self._stderr_reader):
            if task:
                task.cancel()


class ControlManager:
    """Pool of control connections, keyed by (target, session); idempotent."""

    def __init__(self, runner: TmuxRunner):
        self.runner = runner
        self._by_id: dict[str, ControlConnection] = {}
        self._by_key: dict[tuple[str, str], ControlConnection] = {}

    async def start(
        self,
        session: str,
        target: str | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> ControlConnection:
        caps = await self.runner.capabilities(target)
        if not caps.has("control_mode"):
            raise ValueError(
                f"target tmux {caps.version_str} does not support control mode (-C)"
            )
        if (width is None) != (height is None):
            raise ValueError("Provide both width and height, or neither.")
        size_ok = caps.has("refresh_client_size")

        async def apply_size(conn: ControlConnection) -> None:
            if width and height and size_ok:
                await conn.refresh_size(width, height)

        tgt = self.runner.resolve(target)
        key = (tgt.key, session)
        existing = self._by_key.get(key)
        if existing is not None and existing.alive:
            await apply_size(existing)  # honor a resize on an idempotent reuse
            return existing
        if existing is not None:  # stale/dead entry; drop it
            self._forget(existing)
        conn = ControlConnection(self.runner, tgt, session)
        await conn.start()
        await apply_size(conn)
        self._by_id[conn.stream_id] = conn
        self._by_key[key] = conn
        return conn

    def get(self, stream_id: str) -> ControlConnection:
        conn = self._by_id.get(stream_id)
        if conn is None:
            raise ValueError(f"no such stream: {stream_id}")
        return conn

    def list(self) -> list[dict]:
        return [c.info() for c in self._by_id.values()]

    def _forget(self, conn: ControlConnection) -> None:
        self._by_id.pop(conn.stream_id, None)
        self._by_key.pop(conn.key, None)

    async def stop(self, stream_id: str) -> None:
        conn = self.get(stream_id)
        self._forget(conn)
        await conn.close()

    async def stop_all(self) -> None:
        for conn in list(self._by_id.values()):
            self._forget(conn)
            await conn.close()
