"""Factory function for creating configured fast-agent instances."""

from pathlib import Path
from typing import Any, Dict, Optional

try:
    import fast_agent as fast
except ImportError:
    fast = None  # Will raise error when trying to use

from config.loader import load_config, ConfigError


def create_agent_config(config_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load agent configuration from fastagent.config.yaml.

    Args:
        config_dir: Directory containing config files (defaults to ./config)

    Returns:
        Dictionary with agent configuration (model, max_tokens, mcp_servers)

    Raises:
        ConfigError: If config cannot be loaded or is invalid
    """
    try:
        config = load_config(config_dir)
        return {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "mcp_servers": config.mcp_servers,
        }
    except ConfigError as e:
        raise ConfigError(f"Failed to load agent configuration: {e}") from e


# Module-level agent definition - required for @fast.agent decorator
# This will be configured when create_agent_from_config is first called
_agent_config_cache: Optional[Dict[str, Any]] = None


def _get_agent_config(config_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Get or cache agent configuration."""
    global _agent_config_cache
    if _agent_config_cache is None:
        _agent_config_cache = create_agent_config(config_dir)
    return _agent_config_cache


# Agent definition function - will be decorated at runtime
async def _butabot_agent():
    """Agent definition function - configured via @fast.agent decorator."""
    pass


async def create_agent_from_config(
    agent_config: Optional[Dict[str, Any]] = None,
    config_dir: Optional[Path] = None,
) -> Any:
    """
    Create a fast-agent instance from configuration.

    This function creates and returns the fast.run() context manager
    configured with the specified model and MCP servers.

    Args:
        agent_config: Optional pre-loaded agent configuration dict.
                     If None, will load from config_dir.
        config_dir: Directory containing config files (defaults to ./config)

    Returns:
        Fast-agent async context manager (from fast.run())

    Raises:
        ImportError: If fast-agent-mcp is not installed
        ConfigError: If config cannot be loaded or is invalid

    Example:
        async with create_agent_from_config() as agent:
            response = await agent("Hello!")
    """
    if fast is None:
        raise ImportError(
            "fast-agent-mcp not installed. Run: pip install fast-agent-mcp>=0.2.5"
        )

    # Load config if not provided
    if agent_config is None:
        agent_config = create_agent_config(config_dir)

    # Extract configuration values
    model = agent_config.get("model", "claude-3-5-sonnet-20241022")
    mcp_servers = agent_config.get("mcp_servers", {})

    # Convert mcp_servers dict to list format if needed
    # fast-agent expects servers as a list or dict format
    servers = mcp_servers if isinstance(mcp_servers, (list, dict)) else []

    # Create agent using @fast.agent decorator
    # Note: We need to apply the decorator dynamically
    # The fast.run() function automatically uses the most recently defined @fast.agent
    @fast.agent(
        name="butabot_agent",
        instruction="You are a helpful assistant with access to tools. Use available tools to help the user.",
        model=model,
        servers=servers,
        use_history=True,
    )
    async def _agent():
        """Agent definition function."""
        pass

    # Return the context manager from fast.run()
    # This will use the agent we just defined
    return fast.run()

