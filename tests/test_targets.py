import os
import stat
from pathlib import Path

import pytest

import mcp_tmux.targets
from mcp_tmux.targets import list_target_names, resolve_target, ssh_control_options

CTL = ["-o", "ControlMaster=auto", "-o", "ControlPersist=10m", "-o", "ControlPath=/ctl/ssh-%C"]


@pytest.fixture(autouse=True)
def fixed_control_dir(monkeypatch):
    monkeypatch.setattr(mcp_tmux.targets, "_ssh_control_dir", lambda: Path("/ctl"))


CONFIG = {
    "defaults": {"socket_name": "default-sock"},
    "targets": {
        "prod": {
            "host": "user@prod",
            "ssh_options": ["-J", "bastion", "-p", "2222"],
            "socket_name": "work",
        }
    },
}


def test_resolve_local():
    t = resolve_target(None, CONFIG)
    assert not t.is_remote
    assert t.name == "local"
    # default socket applies to local too
    assert t.socket_name == "default-sock"


def test_resolve_named_profile():
    t = resolve_target("prod", CONFIG)
    assert t.is_remote
    assert t.host == "user@prod"
    assert t.ssh_options == ("-J", "bastion", "-p", "2222")
    # per-target socket overrides the default
    assert t.socket_name == "work"


def test_resolve_adhoc():
    t = resolve_target("user@somehost", CONFIG)
    assert t.is_remote
    assert t.host == "user@somehost"
    assert t.ssh_options == ()


def test_build_argv_local():
    t = resolve_target(None, {"defaults": {}, "targets": {}})
    assert t.build_argv(["list-sessions"]) == ["tmux", "-u", "list-sessions"]


def test_build_argv_local_with_socket():
    t = resolve_target(None, CONFIG)
    assert t.build_argv(["kill-server"]) == [
        "tmux",
        "-u",
        "-L",
        "default-sock",
        "kill-server",
    ]


def test_build_argv_remote_quotes_command():
    t = resolve_target("prod", CONFIG)
    argv = t.build_argv(["new-session", "-s", "my sess"])
    assert argv[0] == "ssh"
    assert "-J" in argv and "bastion" in argv
    assert "user@prod" in argv
    # the tmux command is a single, shell-quoted final argument
    remote_cmd = argv[-1]
    assert remote_cmd.startswith("tmux -u -L work new-session")
    assert "'my sess'" in remote_cmd  # space-containing name is quoted


def test_list_target_names():
    assert list_target_names(CONFIG) == ["local", "prod"]


def test_build_argv_socket_path_and_config_file():
    cfg = {"defaults": {"socket_path": "/tmp/s/tmux.sock", "config_file": "/etc/t.conf"}}
    t = resolve_target(None, cfg)
    assert t.build_argv(["ls"]) == [
        "tmux",
        "-u",
        "-S",
        "/tmp/s/tmux.sock",
        "-f",
        "/etc/t.conf",
        "ls",
    ]


def test_build_argv_remote_quotes_socket_path_and_config_file():
    cfg = {
        "targets": {
            "box": {
                "host": "box",
                "socket_path": "/tmp/my dir/tmux.sock",
                "config_file": "/home/u/my tmux.conf",
            }
        }
    }
    argv = resolve_target("box", cfg).build_argv(["ls"])
    assert argv[:-1] == ["ssh", *CTL, "box"]
    assert argv[-1] == "tmux -u -S '/tmp/my dir/tmux.sock' -f '/home/u/my tmux.conf' ls"


def test_profile_flags_override_defaults():
    cfg = {
        "defaults": {"socket_name": "d", "config_file": "/d.conf"},
        "targets": {"box": {"host": "box", "socket_name": "p"}},
    }
    t = resolve_target("box", cfg)
    assert (t.socket_name, t.config_file) == ("p", "/d.conf")


def test_ad_hoc_destination_has_no_ssh_options():
    t = resolve_target("user@elsewhere", {"defaults": {"socket_name": "d"}})
    assert t.is_remote and t.host == "user@elsewhere"
    assert t.ssh_options == ()
    assert t.build_argv(["ls"]) == ["ssh", *CTL, "user@elsewhere", "tmux -u -L d ls"]


def test_multiplexing_injected_after_profile_options():
    argv = resolve_target("prod", CONFIG).build_argv(["ls"])
    assert argv[:-2] == ["ssh", "-J", "bastion", "-p", "2222", *CTL]
    assert argv[-2] == "user@prod"


def test_no_ssh_options_for_local_target():
    argv = resolve_target(None, CONFIG).build_argv(["ls"])
    assert argv[0] == "tmux"
    assert not any(a.startswith("Control") for a in argv)


@pytest.mark.parametrize(
    "opts, kept",
    [
        (["-o", "ControlMaster=no"], ["ControlPersist=10m", "ControlPath=/ctl/ssh-%C"]),
        (["-oControlPersist=30s"], ["ControlMaster=auto", "ControlPath=/ctl/ssh-%C"]),
        (["-o", "controlpath /x/%h"], ["ControlMaster=auto", "ControlPersist=10m"]),
        (["-o", "ControlPath = /x/%h"], ["ControlMaster=auto", "ControlPersist=10m"]),
        (["-S", "/x/sock"], ["ControlMaster=auto", "ControlPersist=10m"]),
        (["-S/x/sock", "-M"], ["ControlPersist=10m"]),
        (
            ["-o", "ControlMaster=yes", "-o", "ControlPersist=no", "-o", "ControlPath=none"],
            [],
        ),
    ],
)
def test_profile_ssh_options_override_multiplexing(opts, kept):
    cfg = {"targets": {"box": {"host": "box", "ssh_options": opts}}}
    argv = resolve_target("box", cfg).build_argv(["ls"])
    injected = argv[1 + len(opts) : -2]
    assert argv[1 : 1 + len(opts)] == opts
    assert injected == [x for o in kept for x in ("-o", o)]


def test_unrelated_options_do_not_suppress_multiplexing():
    opts = ("-p", "22", "-o", "ControlPathX=1", "-oUser=controlmaster")
    assert ssh_control_options(opts) == CTL


def test_control_dir_is_private(monkeypatch, tmp_path):
    monkeypatch.undo()  # the real _ssh_control_dir, not the fixed one
    real = mcp_tmux.targets._ssh_control_dir
    real.cache_clear()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    try:
        d = real()
        assert d == tmp_path / f"mcp-tmux-{os.getuid()}"
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    finally:
        real.cache_clear()


def test_control_dir_not_reused_when_group_accessible(monkeypatch, tmp_path):
    monkeypatch.undo()
    real = mcp_tmux.targets._ssh_control_dir
    real.cache_clear()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    shared = tmp_path / f"mcp-tmux-{os.getuid()}"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    try:
        d = real()
        assert d != shared
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        d.rmdir()
    finally:
        real.cache_clear()
