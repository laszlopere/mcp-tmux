"""Entry point: run the mcp-tmux server over stdio."""

from __future__ import annotations

from .server import build_server


def main() -> None:
    server = build_server()
    server.run()  # stdio transport by default


if __name__ == "__main__":
    main()
