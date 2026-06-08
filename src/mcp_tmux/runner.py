"""TmuxRunner: the single execution funnel for all tmux commands.

Every tmux invocation — curated tool, passthrough, or introspection — goes
through here, so quoting, timeouts, target resolution, version detection, and
error mapping live in exactly one place.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .capabilities import Capabilities
from .config import DEFAULT_TIMEOUT
from .formats import build_format, coerce_records, parse_records
from .targets import Target, resolve_target


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

    def resolve(self, target: str | None) -> Target:
        return resolve_target(target, self.config)

    async def run(
        self,
        tmux_args: list[str],
        *,
        target: str | None = None,
        timeout: float | None = None,
    ) -> TmuxResult:
        """Run ``tmux <tmux_args>`` and return stdout/stderr/exit_code."""
        tgt = self.resolve(target)
        argv = tgt.build_argv(tmux_args, tmux_bin=self.tmux_bin)
        return await self._exec(argv, timeout if timeout is not None else self._default_timeout)

    async def run_checked(
        self,
        tmux_args: list[str],
        *,
        target: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Run and return stdout, raising :class:`TmuxError` on non-zero exit."""
        result = await self.run(tmux_args, target=target, timeout=timeout)
        if not result.ok:
            tgt = self.resolve(target)
            raise TmuxError(
                _map_error(result.stderr, result.exit_code),
                exit_code=result.exit_code,
                stderr=result.stderr,
                argv=tgt.build_argv(tmux_args, tmux_bin=self.tmux_bin),
            )
        return result.stdout

    async def _exec(self, argv: list[str], timeout: float) -> TmuxResult:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:  # tmux or ssh not installed
            missing = argv[0]
            return TmuxResult("", f"{missing}: command not found ({exc})", 127)

        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return TmuxResult("", f"command timed out after {timeout:g}s", 124)

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
