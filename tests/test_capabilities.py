from mcp_tmux.capabilities import Capabilities, parse_version


def test_parse_version_plain():
    assert parse_version("tmux 3.4") == (3, 4)


def test_parse_version_letter_suffix():
    assert parse_version("tmux 1.9a") == (1, 9)


def test_parse_version_next():
    assert parse_version("tmux next-3.5") == (3, 5)


def test_parse_version_unparseable_assumes_newest():
    # "master" has no digits -> assume newest so nothing is gated.
    assert parse_version("tmux master") == (999, 0)


def test_capabilities_gating():
    old = Capabilities.from_output("tmux 1.8")
    new = Capabilities.from_output("tmux 3.4")
    # pane_current_path arrived in 1.9.
    assert not old.has("pane_current_path")
    assert new.has("pane_current_path")
    # 1.8 is the supported floor.
    assert old.supported
    assert Capabilities.from_output("tmux 1.6").supported is False


def test_capabilities_unknown_feature_assumed_available():
    caps = Capabilities.from_output("tmux 1.8")
    assert caps.has("totally_made_up_feature") is True
