"""Shared fixtures.

``loopback_ssh`` starts a private, unprivileged ``sshd`` on 127.0.0.1 (see
``_loopback_sshd.py``) so the remote (SSH) code paths run against this machine
on every test run, with no external host and nothing read from or written to
``~/.ssh``.

``transport`` runs a test four times: against local tmux and with the local
target reached through that sshd, each over one-shot commands and over the
control-mode transport. The test code stays the same; only how ``"local"``
resolves and which transport carries the commands change.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from _loopback_sshd import LoopbackSSH, LoopbackSSHError, loopback_sshd
from _sockets import remove_private_sockets

import mcp_tmux.runner
from mcp_tmux.runner import TmuxRunner
from mcp_tmux.targets import Target, resolve_target


@pytest.fixture(scope="session", autouse=True)
def _remove_private_sockets():
    yield
    remove_private_sockets()


@pytest.fixture(scope="session")
def loopback_ssh(tmp_path_factory: pytest.TempPathFactory):
    try:
        with loopback_sshd(tmp_path_factory.mktemp("loopback-ssh")) as lb:
            yield lb
    except LoopbackSSHError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(params=["local", "ssh", "local-control", "ssh-control"])
def transport(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the requesting test over local tmux and over loopback SSH, each with
    the one-shot and the control-mode transport.

    For ``ssh``, the local target keeps its name and tmux flags (socket etc.)
    but is executed as ``ssh <loopback> tmux ...``. Named and ad-hoc targets
    resolve as usual. For ``control``, every target uses the control transport
    as if the config selected it.
    """
    if request.param.startswith("ssh"):
        lb: LoopbackSSH = request.getfixturevalue("loopback_ssh")

        def via_ssh(target: str | None, config: dict[str, Any]) -> Target:
            tgt = resolve_target(target, config)
            if tgt.is_remote:
                return tgt
            return replace(tgt, host=lb.host, ssh_options=lb.ssh_options)

        monkeypatch.setattr(mcp_tmux.runner, "resolve_target", via_ssh)
    if request.param.endswith("control"):
        monkeypatch.setattr(TmuxRunner, "configured_transport", lambda self, target: "control")
    return str(request.param)


@pytest.fixture(autouse=True)
async def _close_control_clients(monkeypatch: pytest.MonkeyPatch):
    """Close the control clients of every runner a test made, before its event
    loop closes (after the test's own fixtures have torn down)."""
    runners: list[TmuxRunner] = []
    init = TmuxRunner.__init__

    def tracking_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        runners.append(self)

    monkeypatch.setattr(TmuxRunner, "__init__", tracking_init)
    yield
    for runner in runners:
        await runner.aclose()
