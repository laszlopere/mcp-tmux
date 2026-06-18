"""Entry point.

``mcp-tmux``                 run the MCP server over stdio (default)
``mcp-tmux register``       add the server to Claude Code (user scope)
``mcp-tmux unregister``     remove it from Claude Code
"""

from __future__ import annotations

import argparse
import sys

from .register import DEFAULT_NAME, DEFAULT_SCOPE, register, unregister


def _serve() -> None:
    import anyio

    from .server import build_server
    from .tolerant import run_stdio_repaired

    server = build_server()
    # Like server.run() (stdio), but interposes the read stream to repair
    # LLM-mangled tool-call `arguments` before strict validation rejects them.
    anyio.run(lambda: run_stdio_repaired(server))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mcp-tmux",
        description="MCP server for driving tmux (local and over SSH).",
    )
    sub = parser.add_subparsers(dest="cmd")

    for verb, help_text in (
        ("register", "add this server to Claude Code"),
        ("unregister", "remove this server from Claude Code"),
    ):
        p = sub.add_parser(verb, help=help_text)
        p.add_argument("--name", default=DEFAULT_NAME, help="server name (default: %(default)s)")
        p.add_argument(
            "--scope",
            default=DEFAULT_SCOPE,
            choices=["local", "user", "project"],
            help="Claude Code config scope (default: %(default)s)",
        )

    args = parser.parse_args()

    if args.cmd == "register":
        sys.exit(register(name=args.name, scope=args.scope))
    if args.cmd == "unregister":
        sys.exit(unregister(name=args.name, scope=args.scope))

    # No subcommand: run the server (the normal MCP-client invocation).
    _serve()


if __name__ == "__main__":
    main()
