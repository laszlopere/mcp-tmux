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
    assert t.build_argv(["list-sessions"]) == ["tmux", "list-sessions"]


def test_build_argv_local_with_socket():
    t = resolve_target(None, CONFIG)
    assert t.build_argv(["kill-server"]) == [
        "tmux",
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
    assert remote_cmd.startswith("tmux -L work new-session")
    assert "'my sess'" in remote_cmd  # space-containing name is quoted


def test_list_target_names():
    assert list_target_names(CONFIG) == ["local", "prod"]
