"""MCP configuration loader."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Union

from claude_agent_sdk import McpServerConfig


def load_mcp_config(config_path: Union[str, Path, None] = None) -> Union[Dict[str, McpServerConfig], Path, None]:
    """
    Load MCP server configuration from JSON file.
    
    The config file should follow Claude's format:
    {
      "mcpServers": {
        "server-name": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-name"],
          "env": {"KEY": "value"}  // optional
        }
      }
    }
    
    Args:
        config_path: Path to MCP config JSON file. If None, uses MCP_CONFIG_PATH
                     env var or defaults to config/mcp.config.json
                     
    Returns:
        Path to config file (Agent SDK can load it directly) or None if not found
    """
    if config_path is None:
        config_path = os.getenv("MCP_CONFIG_PATH", "config/mcp.config.json")
    
    config_path = Path(config_path)
    
    if not config_path.exists():
        return None
    
    # Agent SDK can load config files directly, so return the path
    # This is simpler than manually parsing and converting
    return config_path


def parse_mcp_config(config_path: Union[str, Path]) -> Dict[str, McpServerConfig]:
    """
    Parse MCP config JSON and convert to Agent SDK format.
    
    This is useful if you need to modify the config programmatically.
    
    Args:
        config_path: Path to MCP config JSON file
        
    Returns:
        Dictionary of server name to McpServerConfig
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
        ValueError: If config format is invalid
    """
    config_path = Path(config_path)
    
    if not config_path.exists():
        raise FileNotFoundError(f"MCP config file not found: {config_path}")
    
    with open(config_path, "r") as f:
        config_data = json.load(f)
    
    if "mcpServers" not in config_data:
        raise ValueError("Config file must contain 'mcpServers' key")
    
    mcp_servers: Dict[str, McpServerConfig] = {}
    
    for server_name, server_config in config_data["mcpServers"].items():
        # Determine server type based on config
        if "command" in server_config:
            # Stdio server (default)
            mcp_config: McpServerConfig = {
                "command": server_config["command"],
            }
            if "args" in server_config:
                mcp_config["args"] = server_config["args"]
            if "env" in server_config:
                mcp_config["env"] = server_config["env"]
            # Type is optional for stdio servers
            if "type" in server_config:
                mcp_config["type"] = server_config["type"]
        elif "url" in server_config:
            # HTTP or SSE server
            server_type = server_config.get("type", "http")
            if server_type not in ["http", "sse"]:
                raise ValueError(f"Invalid server type: {server_type}")
            
            mcp_config = {
                "type": server_type,
                "url": server_config["url"],
            }
            if "headers" in server_config:
                mcp_config["headers"] = server_config["headers"]
        else:
            raise ValueError(f"Invalid server config for {server_name}: must have 'command' or 'url'")
        
        mcp_servers[server_name] = mcp_config
    
    return mcp_servers

