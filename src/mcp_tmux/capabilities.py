"""tmux version detection and feature gating.

We support tmux 1.8+ (≈2013). Rather than scrape behavior, we parse ``tmux -V``
once per target and consult a small table of "feature -> minimum version" so
curated tools only use flags / format variables that the target's tmux actually
understands. The raw passthrough (``tmux_command``) is never gated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Minimum tmux version this server is designed/tested against.
MIN_SUPPORTED = (1, 8)

# Feature -> (major, minor) it first appeared in. Conservative: when unsure we
# pick the version where the variable/flag is reliably documented.
_FEATURES: dict[str, tuple[int, int]] = {
    # format variables
    "pane_current_path": (1, 9),   # pane_current_path format variable
    "session_activity": (2, 1),
    "window_active_clients": (3, 1),
    "pane_pid": (1, 8),
    "session_created": (1, 8),
    "client_name": (2, 4),         # client_name format var (else use client_tty)
    # flags / commands
    "capture_join": (1, 8),        # capture-pane -J (rejoin wrapped lines)
    "capture_escapes": (1, 8),     # capture-pane -e (include escape sequences)
    "control_mode": (1, 8),        # tmux -C
    "send_keys_X": (2, 4),         # send-keys -X (drive copy-mode commands)
    "key_tables": (2, 1),          # bind/list/unbind-key -T <table> (vs -t)
}

_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def parse_version(raw: str) -> tuple[int, int]:
    """Parse the output of ``tmux -V`` into a ``(major, minor)`` tuple.

    Handles forms like ``"tmux 3.4"``, ``"tmux 1.9a"``, ``"tmux next-3.4"``,
    and ``"tmux master"`` (falls back to a high version so nothing is gated).
    """
    m = _VERSION_RE.search(raw)
    if not m:
        # Unparseable (e.g. "tmux master"); assume newest so we don't
        # needlessly disable features.
        return (999, 0)
    return (int(m.group(1)), int(m.group(2)))


@dataclass(frozen=True)
class Capabilities:
    """Resolved capabilities for one target's tmux."""

    raw: str
    version: tuple[int, int]

    @classmethod
    def from_output(cls, raw: str) -> "Capabilities":
        return cls(raw=raw.strip(), version=parse_version(raw))

    @property
    def version_str(self) -> str:
        return f"{self.version[0]}.{self.version[1]}"

    @property
    def supported(self) -> bool:
        return self.version >= MIN_SUPPORTED

    def has(self, feature: str) -> bool:
        """True if the target's tmux is new enough for ``feature``."""
        need = _FEATURES.get(feature)
        if need is None:
            # Unknown feature name: assume available rather than silently drop.
            return True
        return self.version >= need
