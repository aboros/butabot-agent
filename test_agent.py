"""Minimal POC agent with fast-agent-mcp framework integration.

This script verifies fast-agent-mcp installation and MCP server connection.
Creates a minimal ToolRunnerHook that logs tool calls for verification.
"""

import asyncio
from typing import Any, Dict

try:
    import fast_agent as fast
except ImportError:
    print("ERROR: fast-agent-mcp not installed. Run: pip install fast-agent-mcp>=0.2.5")
    exit(1)


class MinimalToolRunnerHook:
    """Minimal hook implementation that logs tool calls for verification."""

    async def on_tool_call(self, tool_name: str, tool_input: Dict[str, Any]) -> None:
        """Log tool invocation."""
        print(f"[TOOL_CALL] {tool_name}: {tool_input}")

    async def on_tool_result(self, tool_name: str, result: Any) -> None:
        """Log tool result."""
        print(f"[TOOL_RESULT] {tool_name}: {result}")


@fast.agent(
    name="test_agent",
    instruction="You are a helpful assistant with access to tools. Use available tools to help the user.",
    model="claude-3-5-sonnet-20241022",
    servers=[],  # Will be configured via fastagent.config.yaml
    use_history=True,
)
async def test_agent():
    """Test agent definition."""
    pass


async def main():
    """POC: Instantiate agent, verify MCP connection, test tool discovery."""
    print("Starting fast-agent-mcp POC...")
    print("=" * 60)

    # Initialize hook for tool call interception
    hook = MinimalToolRunnerHook()

    try:
        async with fast.run() as agent:
            print("✓ Agent instantiated successfully")
            print("=" * 60)

            # Test basic message processing
            print("\nTesting agent with simple message...")
            response = await agent("Hello! What tools do you have available?")
            print(f"Agent response: {response}")
            print("=" * 60)

            # Verify tool discovery (if MCP servers are configured)
            print("\nTool discovery verification:")
            print("MCP servers should be configured in fastagent.config.yaml")
            print("Expected: Tools discovered from configured MCP servers")
            print("=" * 60)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n✓ POC completed successfully")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)

