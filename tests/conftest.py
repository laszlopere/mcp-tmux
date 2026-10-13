"""Shared fixtures.

``loopback_ssh`` starts a private, unprivileged ``sshd`` on 127.0.0.1 (see
``_loopback_sshd.py``) so the remote (SSH) code paths run against this machine
on every test run, with no external host and nothing read from or written to
``~/.ssh``.

``transport`` runs a test twice: once against local tmux and once with the
local target reached through that sshd. The test code stays the same; only how
``"local"`` resolves changes.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from _loopback_sshd import LoopbackSSH, LoopbackSSHError, loopback_sshd

import mcp_tmux.runner
from mcp_tmux.targets import Target, resolve_target


@pytest.fixture(scope="session")
def loopback_ssh(tmp_path_factory: pytest.TempPathFactory):
    try:
        with loopback_sshd(tmp_path_factory.mktemp("loopback-ssh")) as lb:
            yield lb
    except LoopbackSSHError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(params=["local", "ssh"])
def transport(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the requesting test over local tmux and over loopback SSH.

    For ``ssh``, the local target keeps its name and tmux flags (socket etc.)
    but is executed as ``ssh <loopback> tmux ...``. Named and ad-hoc targets
    resolve as usual.
    """
    if request.param == "ssh":
        lb: LoopbackSSH = request.getfixturevalue("loopback_ssh")

        def via_ssh(target: str | None, config: dict[str, Any]) -> Target:
            tgt = resolve_target(target, config)
            if tgt.is_remote:
                return tgt
            return replace(tgt, host=lb.host, ssh_options=lb.ssh_options)

        monkeypatch.setattr(mcp_tmux.runner, "resolve_target", via_ssh)
    return str(request.param)
