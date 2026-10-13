"""Private tmux socket names for the tests.

Every name carries the pytest process id, so test runs that overlap on one
machine (another checkout, an agent, a rerun) each get their own tmux servers
instead of creating and killing sessions on a shared one. ``kill-server``
leaves the socket file behind, so ``remove_private_sockets`` (called when the
test session ends, from conftest.py) deletes them along with any server a
failed test left running.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_names: set[str] = set()


def private_socket(name: str) -> str:
    """Return ``name`` made unique to this test run."""
    unique = f"{name}-{os.getpid()}"
    _names.add(unique)
    return unique


def remove_private_sockets() -> None:
    socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
    for name in _names:
        subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True)
        (socket_dir / name).unlink(missing_ok=True)
