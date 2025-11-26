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
    - Persistent connections for STDIO servers (better performance and state management)
    
    Connection Lifecycle:
    - Connection is opened during initialize() and kept open for application lifetime
    - Connection is closed during close() on application shutdown
    - This pattern is essential for STDIO servers to avoid spawning new processes per operation
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
        self._connection_open = False
        self._roots: List[str] = []
        
        # Load config immediately (synchronous, no I/O in __init__)
        if mcp_config_path.exists():
            with open(mcp_config_path) as f:
                raw_config = json.load(f)
                # Substitute environment variables in config
                self._config = self._substitute_env_vars(raw_config)
        else:
            self._config = {"mcpServers": {}}
        
        # Extract filesystem roots from config
        self._roots = self._extract_filesystem_roots()
    
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
    
    def _extract_filesystem_roots(self) -> List[str]:
        """
        Extract filesystem root paths from MCP config and convert to file:// URLs.
        
        Looks for filesystem server configuration and extracts directory paths
        from its args. Converts paths to file:// URLs as required by FastMCP.
        Defaults to file:///app/data if not found.
        
        Returns:
            List of file:// URLs for filesystem operations
        """
        try:
            mcp_servers = self._config.get("mcpServers", {})
            filesystem_server = mcp_servers.get("filesystem", {})
            args = filesystem_server.get("args", [])
            
            # Look for arguments that look like directory paths (start with /)
            roots = []
            for arg in args:
                if isinstance(arg, str) and arg.startswith("/"):
                    # Convert path to file:// URL format
                    # file:///path/to/dir (note: three slashes for absolute paths)
                    roots.append(f"file://{arg}")
            
            if roots:
                return roots
        except Exception as e:
            print(f"[WARNING] Failed to extract filesystem roots from config: {e}", file=sys.stderr)
            sys.stderr.flush()
        
        # Default to file:///app/data if extraction fails or no roots found
        return ["file:///app/data"]
    
    async def initialize(self):
        """
        Initialize FastMCP client and discover tools.
        
        Opens a persistent connection that remains open for the application lifetime.
        This is essential for STDIO servers to avoid spawning new processes per operation,
        which improves performance and maintains server state.
        
        FastMCP Client handles all multi-server complexity automatically.
        """
        if self._initialized and self._connection_open:
            return
        
        # FastMCP Client handles all multi-server configuration
        # Pass roots to configure filesystem server access
        self.mcp_client = Client(self._config, roots=self._roots if self._roots else None)
        
        # Open connection and keep it open (persistent connection pattern)
        # This is critical for STDIO servers - avoids spawning new processes per operation
        try:
            await self.mcp_client.__aenter__()
            self._connection_open = True
            
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
            if self._roots:
                print(f"[INFO] Filesystem roots configured: {self._roots}", file=sys.stderr)
            sys.stderr.flush()
            
            self._initialized = True
        except Exception as e:
            print(f"[ERROR] Failed to initialize MCP client: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            self._connection_open = False
            # Don't raise - bot can work without MCP tools
    
    async def call_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """
        Execute tool via FastMCP using persistent connection.
        
        Uses the persistent connection opened during initialize().
        This avoids spawning new STDIO processes per operation, improving performance
        and maintaining server state across tool calls.
        
        FastMCP automatically handles server name prefixes and routing.
        
        Args:
            tool_name: Name of the tool (may be prefixed with server name)
            tool_input: Tool input parameters
            
        Returns:
            Tool execution result
        """
        # Ensure connection is open
        if not self._initialized or not self._connection_open:
            await self.initialize()
        
        # Ensure mcp_client exists and connection is open
        if self.mcp_client is None or not self._connection_open:
            print(f"[ERROR] MCP client not initialized or connection not open", file=sys.stderr)
            sys.stderr.flush()
            return {"error": True, "message": "MCP client not initialized"}
        
        try:
            print(f"[INFO] Calling MCP tool: {tool_name} with input: {tool_input}", file=sys.stderr)
            sys.stderr.flush()
            
            # Use persistent connection (no async with - connection stays open)
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
        Ensure MCP client is initialized and connection is open (idempotent).
        
        Can be called multiple times safely. Ensures both initialization flag
        and connection state are set.
        """
        if not self._initialized or not self._connection_open:
            await self.initialize()
    
    async def close(self):
        """
        Close MCP client connections on application shutdown.
        
        Properly closes the persistent connection opened during initialize().
        This is essential for clean shutdown of STDIO server processes.
        """
        if self.mcp_client is not None and self._connection_open:
            try:
                await self.mcp_client.__aexit__(None, None, None)
                self._connection_open = False
                print(f"[INFO] MCP client connections closed", file=sys.stderr)
                sys.stderr.flush()
            except Exception as e:
                print(f"[ERROR] Error closing MCP client connections: {e}", file=sys.stderr)
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                # Continue shutdown even if close fails
                self._connection_open = False

