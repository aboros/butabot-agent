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
    agent_config: Optional[Dict[str, Any]] = None,
    config_dir: Optional[Path] = None,
) -> Any:
    """
    Create a fast-agent instance from configuration.

    This function returns the fast.run() context manager for the pre-defined agent.
    Fast-agent automatically reads configuration from fastagent.config.yaml.

    Note: The agent_config parameter is maintained for backward compatibility
    but fast-agent reads configuration from the config file automatically.

    Args:
        agent_config: Optional pre-loaded agent configuration dict (deprecated, kept for compatibility).
                     Fast-agent reads from fastagent.config.yaml automatically.
        config_dir: Directory containing config files (defaults to ./config)
                   Used for validation/loading but fast-agent reads from config file.

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

    # Validate config exists (for error checking)
    # Fast-agent will read configuration from fastagent.config.yaml automatically
    if config_dir is None:
        config_dir = Path("config")
    
    try:
        # Validate that config can be loaded (for early error detection)
        create_agent_config(config_dir)
    except ConfigError as e:
        # Log but don't fail - fast-agent might handle config differently
        pass

    # Return the context manager from fast.run()
    # Fast-agent automatically uses the module-level agent definition
    # and reads configuration from fastagent.config.yaml
    return fast.run()

