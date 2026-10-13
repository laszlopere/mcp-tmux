from mcp_tmux.targets import list_target_names, resolve_target

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
    assert argv[:2] == ["ssh", "box"]
    assert argv[2] == "tmux -u -S '/tmp/my dir/tmux.sock' -f '/home/u/my tmux.conf' ls"


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
    assert t.build_argv(["ls"]) == ["ssh", "user@elsewhere", "tmux -u -L d ls"]
