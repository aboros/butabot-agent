"""Entry point for running the Nano Banana MCP server as a module."""

from .server import mcp

if __name__ == "__main__":
    mcp.run()

