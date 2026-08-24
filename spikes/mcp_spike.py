"""
MCP integration spike (throwaway script, not part of the real app).

Goal: stand up a minimal MCP server with one dummy tool, connect to it with
a minimal client over stdio, call the tool, and confirm the round trip
works. Uses the official `mcp` Python SDK (pinned to 1.26.0, see
requirements.txt).

The server and client both live in this one file: the client launches the
server as a subprocess over stdio (the standard local-MCP setup — no
network/auth involved), talks to it, then shuts it down.

Run: python spikes/mcp_spike.py
"""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server: one dummy tool that echoes its input back.
#
# This block only runs when the file is executed as the *server* subprocess
# (see the `if __name__ == "__main__" and "--serve" in sys.argv` branch at
# the bottom) — the client launches `python mcp_spike.py --serve` itself.
# ---------------------------------------------------------------------------

mcp_server = FastMCP("PingSpike")


@mcp_server.tool()
def ping(message: str) -> str:
    """Echo the given message back."""
    return f"pong: {message}"


def run_server() -> None:
    mcp_server.run(transport="stdio")


# ---------------------------------------------------------------------------
# Client: launches the server above as a subprocess, connects over stdio,
# calls ping(), and confirms the round trip.
# ---------------------------------------------------------------------------

async def run_client() -> bool:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[__file__, "--serve"],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"Server exposes tools: {tool_names}")

            result = await session.call_tool("ping", {"message": "hello from client"})
            text = result.content[0].text if result.content else None

            print(f"Called ping(message='hello from client') -> {text!r}")

            success = text == "pong: hello from client"
            return success


def main() -> None:
    print("=== MCP Spike ===\n")
    success = asyncio.run(run_client())

    print("\n=== Summary ===")
    print(f"Round trip: {'WORKS' if success else 'FAILS'}")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        run_server()
    else:
        main()
