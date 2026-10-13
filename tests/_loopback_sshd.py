"""A private, unprivileged sshd on 127.0.0.1 for exercising SSH targets.

Used by the ``loopback_ssh`` fixture (tests/conftest.py) and by
scripts/bench_transport.py. Nothing is read from or written to ``~/.ssh``:
keys, ``authorized_keys`` and ``known_hosts`` live in the given directory.
"""

from __future__ import annotations

import contextlib
import getpass
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_tmux.targets import ssh_control_options

SSHD_CANDIDATES = ("/usr/sbin/sshd", "/usr/bin/sshd", "/usr/local/sbin/sshd")
INSTALL_HINT = (
    "the loopback SSH tests need OpenSSH's sshd, ssh and ssh-keygen "
    "(Debian/Ubuntu: sudo apt-get install openssh-server openssh-client)"
)


class LoopbackSSHError(RuntimeError):
    """sshd is missing, or did not start and accept a connection."""


@dataclass(frozen=True)
class LoopbackSSH:
    """A running loopback sshd and the client options that reach it."""

    host: str  # user@127.0.0.1
    port: int
    ssh_options: tuple[str, ...]

    def target_spec(self, socket_name: str | None = None) -> dict[str, Any]:
        """A named-target profile (``config['targets'][name]``) for this sshd."""
        spec: dict[str, Any] = {"host": self.host, "ssh_options": list(self.ssh_options)}
        if socket_name:
            spec["socket_name"] = socket_name
        return spec


def _find_sshd() -> str | None:
    # sshd refuses to re-exec unless started by absolute path, and it is
    # usually in sbin, which is often missing from a non-root PATH.
    found = shutil.which("sshd")
    if found:
        return found
    return next((p for p in SSHD_CANDIDATES if Path(p).is_file()), None)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _keygen(path: Path) -> None:
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "mcp-tmux-test", "-f", str(path)],
        check=True,
        stdin=subprocess.DEVNULL,
    )


@contextlib.contextmanager
def loopback_sshd(d: Path) -> Iterator[LoopbackSSH]:
    """Start sshd with its files in the empty directory ``d``; stop it on exit."""
    sshd = _find_sshd()
    if sshd is None or shutil.which("ssh") is None or shutil.which("ssh-keygen") is None:
        raise LoopbackSSHError(INSTALL_HINT)

    host_key, client_key = d / "host_key", d / "client_key"
    _keygen(host_key)
    _keygen(client_key)
    (d / "authorized_keys").write_text(Path(f"{client_key}.pub").read_text())
    port = _free_port()
    (d / "known_hosts").write_text(f"[127.0.0.1]:{port} {Path(f'{host_key}.pub').read_text()}")

    user = getpass.getuser()
    # StrictModes off: the temp dir sits under world-writable /tmp, which
    # sshd's ownership walk would otherwise reject.
    (d / "sshd_config").write_text(
        f"Port {port}\n"
        "ListenAddress 127.0.0.1\n"
        f"HostKey {host_key}\n"
        f"PidFile {d / 'sshd.pid'}\n"
        f"AuthorizedKeysFile {d / 'authorized_keys'}\n"
        f"AllowUsers {user}\n"
        "PubkeyAuthentication yes\n"
        "PasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "UsePAM no\n"
        "StrictModes no\n"
        "PrintMotd no\n"
        "LogLevel ERROR\n"
    )

    log = (d / "sshd.log").open("w")
    proc = subprocess.Popen(
        [sshd, "-D", "-e", "-f", str(d / "sshd_config")],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # -F none: ignore the user's ~/.ssh/config (ControlMaster, ProxyJump, ...)
    # so the tests see exactly these options.
    ssh_options = (
        "-F",
        "none",
        "-p",
        str(port),
        "-i",
        str(client_key),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "IdentityAgent=none",
        "-o",
        f"UserKnownHostsFile={d / 'known_hosts'}",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
    )
    lb = LoopbackSSH(host=f"{user}@127.0.0.1", port=port, ssh_options=ssh_options)

    try:
        deadline = time.monotonic() + 10
        while True:
            if proc.poll() is not None:
                raise LoopbackSSHError(
                    f"loopback sshd exited ({proc.returncode}): "
                    f"{(d / 'sshd.log').read_text().strip()}"
                )
            probe = subprocess.run(
                ["ssh", *ssh_options, lb.host, "true"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                break
            if time.monotonic() > deadline:
                raise LoopbackSSHError(
                    f"loopback sshd not reachable: {probe.stderr.strip()}; "
                    f"sshd log: {(d / 'sshd.log').read_text().strip()}"
                )
            time.sleep(0.1)
        yield lb
    finally:
        # Stop the multiplexing master the SSH targets started, rather than
        # leave it idling until ControlPersist runs out.
        subprocess.run(
            ["ssh", *ssh_options, *ssh_control_options(ssh_options), "-O", "exit", lb.host],
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
