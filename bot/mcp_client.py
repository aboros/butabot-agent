"""MCP client wrapper for loading and managing MCP servers."""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastmcp import Client

# Load environment variables from .env file
load_dotenv()


class MCPClientWrapper:
    """Thin wrapper around FastMCP Client for schema conversion and caching.
    
    FastMCP Client handles all multi-server complexity automatically.
    This wrapper adds:
    - Schema conversion (MCP → Anthropic format)
    - Tool caching (avoid re-discovery on every API call)
    - File loading convenience
    
    Note: FastMCP manages connection lifecycle - we use async with for each operation
    to ensure Docker-based STDIO servers work correctly.
    """
    
    def __init__(self, mcp_config_path: Path):
        """
        Initialize MCP client from config file.
        
        Args:
            mcp_config_path: Path to .mcp.json configuration file
        """
        self.mcp_client: Optional[Client] = None
        self._tools: List[Dict[str, Any]] = []
        self._initialized = False
        
        # Load config immediately (synchronous, no I/O in __init__)
        if mcp_config_path.exists():
            with open(mcp_config_path) as f:
                raw_config = json.load(f)
                # Substitute environment variables in config
                self._config = self._substitute_env_vars(raw_config)
        else:
            self._config = {"mcpServers": {}}
    
    def _substitute_env_vars(self, obj: Any) -> Any:
        """
        Recursively substitute environment variables in config.
        
        Handles ${VAR_NAME} syntax in strings, dicts, and lists.
        
        Args:
            obj: Config object (dict, list, or string)
            
        Returns:
            Object with environment variables substituted
        """
        if isinstance(obj, dict):
            return {key: self._substitute_env_vars(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._substitute_env_vars(item) for item in obj]
        elif isinstance(obj, str):
            # Substitute ${VAR_NAME} patterns with environment variable values
            def replace_var(match):
                var_name = match.group(1)
                # Support default value syntax: ${VAR_NAME:-default}
                if ':-' in var_name:
                    var_name, default = var_name.split(':-', 1)
                    return os.getenv(var_name, default)
                return os.getenv(var_name, match.group(0))  # Return original if not found
            
            # Match ${VAR_NAME} or ${VAR_NAME:-default}
            pattern = r'\$\{([^}]+)\}'
            return re.sub(pattern, replace_var, obj)
        else:
            return obj
    
    async def initialize(self):
        """
        Initialize FastMCP client and discover tools.
        
        Uses async with to let FastMCP manage connection lifecycle properly.
        This is especially important for Docker-based STDIO servers.
        FastMCP Client handles all multi-server complexity automatically.
        """
        if self._initialized:
            return
        
        # FastMCP Client handles all multi-server configuration
        self.mcp_client = Client(self._config)
        
        # Use async with to discover tools - let FastMCP manage the connection
        # This ensures Docker-based STDIO servers work correctly
        async with self.mcp_client:
            # Discover tools from all servers (FastMCP handles this)
            tools = await self.mcp_client.list_tools()
            # Convert FastMCP tool objects to dicts for caching
            self._tools = [
                {
                    "name": tool.name,
                    "description": getattr(tool, "description", ""),
                    "inputSchema": getattr(tool, "inputSchema", getattr(tool, "input_schema", {}))
                }
                for tool in tools
            ]
        
        print(f"[INFO] MCP client initialized with {len(self._tools)} tools", file=sys.stderr)
        sys.stderr.flush()
        
        self._initialized = True
    
    async def call_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """
        Execute tool via FastMCP.
        
        Uses async with for each tool call to let FastMCP manage connection lifecycle.
        This ensures Docker-based STDIO servers work correctly.
        FastMCP automatically handles server name prefixes and routing.
        
        Args:
            tool_name: Name of the tool (may be prefixed with server name)
            tool_input: Tool input parameters
            
        Returns:
            Tool execution result
        """
        if not self._initialized:
            await self.initialize()
        
        # Ensure mcp_client is created
        if self.mcp_client is None:
            self.mcp_client = Client(self._config)
        
        try:
            print(f"[INFO] Calling MCP tool: {tool_name} with input: {tool_input}", file=sys.stderr)
            sys.stderr.flush()
            
            # Use async with for each tool call - let FastMCP manage connection lifecycle
            # This is especially important for Docker-based STDIO servers
            async with self.mcp_client:
                # Use named parameters as per FastMCP API
                # Use raise_on_error=False to get structured error results instead of exceptions
                result = await self.mcp_client.call_tool(
                    name=tool_name, 
                    arguments=tool_input,
                    raise_on_error=False
                )
                print(f"[INFO] MCP tool {tool_name} returned result: {type(result)}", file=sys.stderr)
                print(f"[INFO] Result has .data: {hasattr(result, 'data')}, .data value: {getattr(result, 'data', None)}", file=sys.stderr)
                print(f"[INFO] Result has .structured_content: {hasattr(result, 'structured_content')}, .structured_content value: {getattr(result, 'structured_content', None)}", file=sys.stderr)
                print(f"[INFO] Result has .content: {hasattr(result, 'content')}, .content length: {len(getattr(result, 'content', []))}", file=sys.stderr)
                sys.stderr.flush()
                
                # Check for errors first (using raise_on_error=False gives us error results)
                if hasattr(result, "is_error") and result.is_error:
                    error_msg = "Tool execution failed"
                    if hasattr(result, "content") and result.content:
                        # Extract error message from content blocks
                        error_parts = []
                        for block in result.content:
                            if hasattr(block, "text"):
                                error_parts.append(block.text)
                        if error_parts:
                            error_msg = " ".join(error_parts)
                    elif hasattr(result, "structured_content") and result.structured_content:
                        # Try to extract error from structured content
                        error_msg = str(result.structured_content)
                    
                    print(f"[ERROR] MCP tool {tool_name} returned error: {error_msg}", file=sys.stderr)
                    sys.stderr.flush()
                    # Return error as result instead of raising - let Claude handle it
                    return {"error": True, "message": error_msg}
                
                # FastMCP returns CallToolResult with .data, .structured_content, and .content
                # For Drupal MCP tools, structured_content often has complete JSON data already
                # Priority: structured_content (if available) > .data (with recursive conversion) > .content
                if hasattr(result, "structured_content") and result.structured_content is not None:
                    # structured_content is already proper JSON - use it directly
                    print(f"[INFO] Using structured_content for {tool_name} (already JSON)", file=sys.stderr)
                    sys.stderr.flush()
                    return result.structured_content
                elif hasattr(result, "data") and result.data is not None:
                    # Fully hydrated Python objects (FastMCP exclusive)
                    # Recursively convert Pydantic models and nested objects to plain Python types
                    data = self._recursively_convert_pydantic(result.data)
                    
                    # Log the data type and preview for debugging
                    print(f"[INFO] Extracted and converted result.data type: {type(data)}, preview: {str(data)[:200]}", file=sys.stderr)
                    sys.stderr.flush()
                    return data
                elif hasattr(result, "content") and result.content:
                    # Extract text from content blocks (list of TextContent, ImageContent, etc.)
                    print(f"[INFO] Extracting text from content blocks for {tool_name}", file=sys.stderr)
                    sys.stderr.flush()
                    text_parts = []
                    for block in result.content:
                        if hasattr(block, "text"):
                            text_parts.append(block.text)
                        elif hasattr(block, "data"):
                            # Binary data - convert to string representation
                            text_parts.append(f"<binary data: {len(block.data)} bytes>")
                    if text_parts:
                        return "\n".join(text_parts)
                    else:
                        # Empty content blocks
                        return ""
                else:
                    # Fallback: return result as-is (shouldn't happen with proper FastMCP)
                    print(f"[WARNING] MCP tool {tool_name} returned unexpected result format", file=sys.stderr)
                    sys.stderr.flush()
                    return result
        except Exception as e:
            # Log the error for debugging
            # Some errors (like validation errors) are still raised even with raise_on_error=False
            error_msg = str(e)
            print(f"[ERROR] MCP tool call failed for {tool_name}: {error_msg}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            # Return error as dict instead of raising - let Claude handle it
            return {"error": True, "message": error_msg}
    
    def get_anthropic_tools(self) -> List[Dict[str, Any]]:
        """
        Convert cached MCP tools to Anthropic format.
        
        Returns:
            List of Anthropic tool definitions ready for API
        """
        return [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": self._convert_schema(tool.get("inputSchema", {}))
            }
            for tool in self._tools
        ]
    
    def _convert_schema(self, mcp_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert MCP schema to Anthropic format.
        
        Args:
            mcp_schema: MCP tool input schema
            
        Returns:
            Anthropic-compatible input schema
        """
        # If already in Anthropic format, return as-is
        if isinstance(mcp_schema, dict) and "type" in mcp_schema:
            return mcp_schema.copy()
        
        # Convert MCP format to Anthropic format
        return {
            "type": "object",
            "properties": mcp_schema.get("properties", {}),
            "required": mcp_schema.get("required", [])
        }
    
    def _recursively_convert_pydantic(self, obj: Any) -> Any:
        """
        Recursively convert Pydantic models and nested objects to plain Python types.
        
        Args:
            obj: Object that may contain Pydantic models
            
        Returns:
            Plain Python dict/list/primitive with all Pydantic models converted
        """
        # Handle Pydantic models
        if hasattr(obj, "model_dump"):
            # Pydantic v2 - recursively convert
            try:
                return self._recursively_convert_pydantic(obj.model_dump(mode="json"))
            except Exception:
                # Fallback to non-recursive conversion
                try:
                    return obj.model_dump()
                except Exception:
                    pass
        elif hasattr(obj, "dict"):
            # Pydantic v1 - recursively convert
            try:
                return self._recursively_convert_pydantic(obj.dict())
            except Exception:
                # Fallback to non-recursive conversion
                try:
                    return obj.dict()
                except Exception:
                    pass
        
        # Handle dicts - recurse into values
        if isinstance(obj, dict):
            return {key: self._recursively_convert_pydantic(value) for key, value in obj.items()}
        
        # Handle lists - recurse into items
        if isinstance(obj, list):
            return [self._recursively_convert_pydantic(item) for item in obj]
        
        # Handle objects with __dict__ that aren't already handled
        if hasattr(obj, "__dict__") and not isinstance(obj, (str, int, float, bool, type(None))):
            try:
                return self._recursively_convert_pydantic(dict(obj.__dict__))
            except Exception:
                pass
        
        # Return primitives and other types as-is
        return obj
    
    async def ensure_initialized(self):
        """
        Ensure MCP client is initialized (idempotent).
        
        Can be called multiple times safely.
        """
        if not self._initialized:
            await self.initialize()
    
    async def close(self):
        """
        Cleanup method for shutdown (no-op since FastMCP manages connections).
        
        FastMCP manages connection lifecycle automatically with async with,
        so no explicit cleanup is needed.
        """
        # No-op: FastMCP manages connections via async with context managers
        pass

