"""Factory function for creating configured fast-agent instances."""

from pathlib import Path
from typing import Any, Dict, Optional

try:
    from fast_agent.core.fastagent import FastAgent
    
    # Create the FastAgent instance - required before using @fast.agent decorator
    fast = FastAgent("Butabot Agent")
except ImportError:
    fast = None  # Will raise error when trying to use
    FastAgent = None

# Note: Fast-agent automatically loads configuration from fastagent.config.yaml
# No need for custom config loading - fast-agent handles it internally


# Module-level agent definition with default configuration
# Fast-agent will automatically read from fastagent.config.yaml for actual configuration
# The decorator parameters here serve as defaults/fallbacks
if fast is not None:
    @fast.agent(
        name="butabot_agent",
        instruction="You are a helpful assistant with access to tools. Use available tools to help the user.",
        use_history=True,
    )
    async def _butabot_agent():
        """Agent definition function - configured via @fast.agent decorator."""
        pass


async def create_agent_from_config(
    config_dir: Optional[Path] = None,
) -> Any:
    """
    Create a fast-agent instance from configuration.

    This function returns the fast.run() context manager for the pre-defined agent.
    Fast-agent automatically reads configuration from fastagent.config.yaml in the project root.

    Args:
        config_dir: Optional config directory (for compatibility, not used by fast-agent).
                   Fast-agent reads from fastagent.config.yaml in project root automatically.

    Returns:
        Fast-agent async context manager (from fast.run())

    Raises:
        ImportError: If fast-agent-mcp is not installed

    Example:
        async with create_agent_from_config() as agent:
            response = await agent("Hello!")
    """
    if fast is None:
        raise ImportError(
            "fast-agent-mcp not installed. Run: pip install fast-agent-mcp>=0.2.5"
        )

    # Fast-agent automatically loads configuration from fastagent.config.yaml in project root
    # The config files are copied there during Docker build or mounted in docker-compose
    return fast.run()

