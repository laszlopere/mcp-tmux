"""Target resolution: where does the tmux server live?

A :class:`Target` is either the local machine or a remote host reached over
SSH. It also carries the tmux *global* flags (``-L``/``-S``/``-f``) that select
which tmux server/socket to talk to, since multiple tmux servers can run on one
host.

Resolution rules for a target string:

* ``None``, ``""``, or ``"local"``  -> local tmux.
* a name present in ``config['targets']`` -> that named remote profile.
* anything else (e.g. ``"user@host"`` or an ssh alias) -> ad-hoc remote; ssh
  options are left to the user's ``~/.ssh/config``.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Target:
    """An execution target for tmux commands."""

    name: str = "local"
    host: str | None = None  # ssh destination; None => local
    ssh_options: tuple[str, ...] = ()
    socket_name: str | None = None  # tmux -L
    socket_path: str | None = None  # tmux -S
    config_file: str | None = None  # tmux -f

    @property
    def is_remote(self) -> bool:
        return self.host is not None

    @property
    def key(self) -> str:
        """Stable cache key (capabilities are cached per target/socket)."""
        return f"{self.host or 'local'}|{self.socket_name or ''}|{self.socket_path or ''}"

    def _tmux_global_flags(self) -> list[str]:
        flags: list[str] = []
        if self.socket_name:
            flags += ["-L", self.socket_name]
        if self.socket_path:
            flags += ["-S", self.socket_path]
        if self.config_file:
            flags += ["-f", self.config_file]
        return flags

    def build_argv(self, tmux_args: list[str], tmux_bin: str = "tmux") -> list[str]:
        """Build the full argv to execute ``tmux <tmux_args>`` on this target.

        Locally this is a plain argv list (executed without a shell, so no
        quoting hazards). Remotely the tmux invocation is shell-quoted into a
        single string handed to ``ssh``, which runs it via the remote shell.
        """
        tmux_argv = [tmux_bin, *self._tmux_global_flags(), *tmux_args]
        if not self.is_remote:
            return tmux_argv
        assert self.host is not None  # is_remote == (host is not None)
        remote_cmd = shlex.join(tmux_argv)
        return ["ssh", *self.ssh_options, self.host, remote_cmd]


def resolve_target(target: str | None, config: dict[str, Any]) -> Target:
    """Resolve a target string against the loaded config."""
    defaults = config.get("defaults", {}) or {}

    def with_defaults(**over: Any) -> dict[str, Any]:
        base = {
            "socket_name": defaults.get("socket_name"),
            "socket_path": defaults.get("socket_path"),
            "config_file": defaults.get("config_file"),
        }
        base.update({k: v for k, v in over.items() if v is not None})
        return base

    if target in (None, "", "local"):
        return Target(name="local", host=None, **with_defaults())

    targets = config.get("targets", {}) or {}
    if target in targets:
        spec = targets[target]
        opts = spec.get("ssh_options", []) or []
        return Target(
            name=str(target),
            host=str(spec["host"]),
            ssh_options=tuple(str(o) for o in opts),
            **with_defaults(
                socket_name=spec.get("socket_name"),
                socket_path=spec.get("socket_path"),
                config_file=spec.get("config_file"),
            ),
        )

    # Ad-hoc ssh destination (alias or user@host); rely on ~/.ssh/config.
    return Target(name=str(target), host=str(target), **with_defaults())


def list_target_names(config: dict[str, Any]) -> list[str]:
    return ["local", *sorted((config.get("targets", {}) or {}).keys())]
