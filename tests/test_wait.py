from mcp_tmux.tools.wait import _extract_run_output

BEG = "__MCP_BEG_abcd1234__"
END = "__MCP_END_abcd1234__"


def test_extract_incomplete_returns_none():
    # BEG present but END not yet -> still running.
    content = f"prompt$ ...\n{BEG}\nsome output so far"
    assert _extract_run_output(content, BEG, END) is None


def test_extract_basic():
    content = "\n".join(
        [
            f"pipas@host:~$ printf '%s\\n' {BEG}; ls; printf '%s %d\\n' {END} \"$?\"",
            BEG,
            "file1",
            "file2",
            f"{END} 0",
            "pipas@host:~$ ",
        ]
    )
    out = _extract_run_output(content, BEG, END)
    assert out == (0, "file1\nfile2")


def test_extract_nonzero_exit():
    content = "\n".join([BEG, "bash: nope: command not found", f"{END} 127"])
    code, output = _extract_run_output(content, BEG, END)
    assert code == 127
    assert "command not found" in output


def test_extract_empty_output():
    content = "\n".join([BEG, f"{END} 0"])
    assert _extract_run_output(content, BEG, END) == (0, "")


def test_extract_ignores_input_echo_line():
    # The echoed command line contains the marker tokens but is not a bare
    # marker, so it must not be mistaken for the BEG marker.
    content = "\n".join(
        [
            f"$ printf '%s\\n' {BEG}; echo hi; printf '%s %d\\n' {END} \"$?\"",
            BEG,
            "hi",
            f"{END} 0",
        ]
    )
    assert _extract_run_output(content, BEG, END) == (0, "hi")
