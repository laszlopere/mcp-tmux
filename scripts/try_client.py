"""Drive the mcp-tmux server as a real stdio subprocess — no registration.

Spawns `python -m mcp_tmux` and talks to it over the actual MCP JSON-RPC
protocol using the MCP client SDK. Run from the repo root:

    .venv/bin/python scripts/try_client.py
"""

from __future__ import annotations

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


async def main() -> None:
    # Point the server at an isolated tmux socket so we never touch real sessions.
    env = dict(os.environ)
    env["MCP_TMUX_CONFIG"] = os.path.join(os.path.dirname(__file__), "isolated_config.toml")

    params = StdioServerParameters(command="python", args=["-m", "mcp_tmux"], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server:", init.serverInfo.name, init.serverInfo.version)

            tools = await session.list_tools()
            print(f"\n{len(tools.tools)} tools available\n")

            async def call(name, args):
                print(f"-> {name}({args})")
                res = await session.call_tool(name, args)
                print("   ", _text(res))
                return res

            await call("tmux_version", {})
            await call("tmux_new_session", {"name": "demo", "detached": True})
            await call(
                "tmux_send_keys",
                {"target_pane": "demo", "text": "echo it-works", "enter": True},
            )
            await asyncio.sleep(0.4)
            await call("tmux_capture_pane", {"target_pane": "demo"})
            await call("tmux_list", {"kind": "session"})
            await call("tmux_command", {"args": ["kill-server"]})


if __name__ == "__main__":
    asyncio.run(main())
