"""Claude client wrapper for thread-aware conversations."""

import json
import sys
from typing import Any, AsyncIterator, Callable, Dict, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    SystemMessage,
    Message,
    HookMatcher,
    HookContext,
)

from .session_manager import SessionManager


def log(message: str, level: str = "INFO"):
    """Log message with flush to ensure it appears in Docker logs."""
    print(f"[{level}] ClaudeClient: {message}", file=sys.stderr, flush=True)


class ClaudeClient:
    """Wrapper around ClaudeSDKClient for thread-aware Slack conversations."""
    
    def __init__(
        self,
        session_manager: SessionManager,
        feedback_callback: Optional[Callable[[str, str], Any]] = None,
        approval_manager: Optional[Any] = None,
    ):
        """
        Initialize Claude client.
        
        Args:
            session_manager: Session manager for thread-to-session mapping
            feedback_callback: Optional callback for sending feedback messages to Slack.
                             Called with (thread_id, feedback_message) to send feedback.
                             Can be a callable or a dict mapping thread_id -> callback.
            approval_manager: Optional ToolApprovalManager instance for tool approval
        """
        self.session_manager = session_manager
        self.feedback_callback = feedback_callback
        self.approval_manager = approval_manager
        self._clients: Dict[str, ClaudeSDKClient] = {}  # thread_id -> client
        self._feedback_callbacks: Dict[str, Callable[[str, str], Any]] = {}  # thread_id -> callback
        self._channel_ids: Dict[str, str] = {}  # thread_id -> channel_id
        
        # Use .mcp.json from project root - let SDK handle parsing
        # According to docs, configure MCP servers in .mcp.json at project root
        # SDK can load it directly if we pass the path
        from pathlib import Path
        mcp_config_path = Path("/app/.mcp.json")
        
        # Build options - SDK will handle parsing .mcp.json
        # Enable filesystem tools (Read, Write, Edit, Glob, Grep) for Drupal development
        # Restrict agent's working directory to /app/agent/workspace for security
        # Agent can access mounted Drupal site folders within the workspace
        self.base_options = ClaudeAgentOptions(
            mcp_servers=mcp_config_path if mcp_config_path.exists() else {},
            cwd="/app/agent/workspace",  # Restrict agent to workspace only
            disallowed_tools=[
                # Keep Bash disabled for security (enable if you need Drush/Composer commands)
                # Note: Drupal MCP server can handle Drupal operations without Bash
                "Bash", "BashOutput", "KillBash",
                # Keep other dev tools disabled
                "Task", "TodoWrite", "NotebookEdit", "ExitPlanMode",
                # Filesystem tools (Read, Write, Edit, Glob, Grep) are now ENABLED
            ],
        )
    
    def set_feedback_callback(self, thread_id: str, callback: Callable[[str, str], Any]):
        """
        Set feedback callback for a specific thread.
        
        Args:
            thread_id: Slack thread ID
            callback: Callback function that takes (thread_id, feedback_message)
        """
        self._feedback_callbacks[thread_id] = callback
    
    def set_channel_id(self, thread_id: str, channel_id: str):
        """
        Set channel ID for a specific thread (needed for approval requests).
        
        Args:
            thread_id: Slack thread ID
            channel_id: Slack channel ID
        """
        self._channel_ids[thread_id] = channel_id
    
    async def _get_or_create_client(self, thread_id: str) -> ClaudeSDKClient:
        """
        Get or create a ClaudeSDKClient for a thread.
        
        The SDK maintains sessions automatically within a client instance.
        We only use 'resume' if we're recreating a client for an existing session.
        
        Args:
            thread_id: Slack thread ID
            
        Returns:
            ClaudeSDKClient instance
        """
        if thread_id not in self._clients:
            # Get stored session_id if this thread has sent messages before
            stored_session_id = self.session_manager.get_session(thread_id)
            
            # Create options with resume parameter only if we have a stored session_id
            # Copy disallowed_tools from base_options
            options = ClaudeAgentOptions(
                mcp_servers=self.base_options.mcp_servers,
                resume=stored_session_id if stored_session_id else None,
                disallowed_tools=self.base_options.disallowed_tools,
            )
            
            # Add feedback hooks (callback will be checked dynamically in hooks)
            # Check if we have any callback (per-thread or global)
            has_callback = thread_id in self._feedback_callbacks or self.feedback_callback is not None
            if has_callback:
                hooks = self._create_feedback_hooks(thread_id)
                if hooks:
                    options.hooks = hooks
            
            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._clients[thread_id] = client
        
        return self._clients[thread_id]
    
    def _create_feedback_hooks(self, thread_id: str, feedback_callback: Optional[Callable[[str, str], Any]] = None) -> Dict[str, list[HookMatcher]]:
        """Create PreToolUse and PostToolUse hooks for feedback messages."""
        hooks = {}
        
        async def pre_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext
        ) -> Dict[str, Any]:
            """Hook that requests approval before tool execution."""
            tool_name = input_data.get("tool_name", "unknown")
            tool_input = input_data.get("tool_input", {})
            
            # Request approval if approval_manager is available
            decision = "allow"  # Default to allow if no approval manager
            decision_reason = ""
            
            if self.approval_manager:
                channel_id = self._channel_ids.get(thread_id)
                if channel_id:
                    try:
                        # Request approval (this will post the approval dialog)
                        approval_id, approved, message_ts = await self.approval_manager.request_approval(
                            thread_id=thread_id,
                            channel_id=channel_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_use_id or "",
                        )
                        
                        decision = "allow" if approved else "deny"
                        decision_reason = f"Tool {tool_name} {'approved' if approved else 'denied'}"
                        
                        # Store mapping for later result updates (only if approved)
                        if approved and message_ts:
                            self.approval_manager.store_tool_use_mapping(tool_use_id or "", approval_id)
                        elif not approved:
                            # Clean up approval data if denied
                            self.approval_manager._cleanup_approval(approval_id)
                    except Exception as e:
                        print(f"Error requesting tool approval: {e}")
                        # On error, deny by default for safety
                        decision = "deny"
                        decision_reason = f"Error requesting approval: {str(e)}"
            
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": decision_reason,
                }
            }
        
        async def post_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext
        ) -> Dict[str, Any]:
            """Hook that updates approval message after tool execution."""
            # Update the approval message instead of sending a new one
            if self.approval_manager and tool_use_id:
                tool_result = input_data.get("tool_result", {})
                is_error = input_data.get("is_error", False)
                
                try:
                    await self.approval_manager.update_approval_message_with_result(
                        tool_use_id=tool_use_id,
                        tool_result=tool_result,
                        is_error=is_error
                    )
                except Exception as e:
                    print(f"Error updating approval message with result: {e}")
            
            return {}
        
        hooks["PreToolUse"] = [HookMatcher(hooks=[pre_tool_use_hook])]
        hooks["PostToolUse"] = [HookMatcher(hooks=[post_tool_use_hook])]
        
        return hooks
    
    def _format_tool_input(self, tool_input: Dict[str, Any]) -> str:
        """Format tool input for display."""
        try:
            return json.dumps(tool_input, indent=2)
        except Exception:
            return str(tool_input)
    
    def _format_tool_result(self, tool_result: Any) -> str:
        """Format tool result for display."""
        try:
            if isinstance(tool_result, str):
                # Try to parse as JSON for pretty printing
                try:
                    parsed = json.loads(tool_result)
                    return json.dumps(parsed, indent=2)
                except (json.JSONDecodeError, ValueError):
                    return tool_result
            elif isinstance(tool_result, (dict, list)):
                return json.dumps(tool_result, indent=2)
            else:
                return str(tool_result)
        except Exception:
            return str(tool_result)
    
    async def send_message(
        self,
        thread_id: str,
        user_message: str,
    ) -> AsyncIterator[Message]:
        """
        Send a message to Claude and stream responses.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            user_message: User's message text
            
        Yields:
            Messages from Claude (AssistantMessage, ToolUseBlock, etc.)
        """
        try:
            log(f"Sending message to Claude for thread {thread_id}")
            
            # Get or create client (SDK maintains session automatically)
            client = await self._get_or_create_client(thread_id)
            log(f"Got/created client for thread {thread_id}")
            
            # Send message
            log(f"Calling client.query() with message: {user_message[:100]}")
            await client.query(user_message)
            log("client.query() completed, starting to receive response")
            
            # Stream responses and extract session_id from ResultMessage
            message_count = 0
            try:
                log("Starting to iterate over receive_response()")
                async for message in client.receive_response():
                    message_count += 1
                    message_type = type(message).__name__
                    log(f"Received message #{message_count}: {message_type}")
                    
                    # Log SystemMessage details
                    if isinstance(message, SystemMessage):
                        subtype = getattr(message, 'subtype', 'unknown')
                        data = getattr(message, 'data', {})
                        log(f"  SystemMessage subtype: {subtype}")
                        log(f"  SystemMessage data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
                        if isinstance(data, dict):
                            # Log important data fields
                            for key in ['message', 'status', 'error', 'info', 'type']:
                                if key in data:
                                    log(f"  SystemMessage.{key}: {str(data[key])[:200]}")
                            
                            # Log MCP server status
                            if 'mcp_servers' in data:
                                mcp_servers = data['mcp_servers']
                                log(f"  MCP Servers: {len(mcp_servers) if isinstance(mcp_servers, list) else 'N/A'} servers")
                                if isinstance(mcp_servers, list):
                                    for server in mcp_servers:
                                        server_name = server.get('name', 'unknown')
                                        server_status = server.get('status', 'unknown')
                                        server_tools = server.get('tools', [])
                                        server_error = server.get('error', None)
                                        # Log all server fields for debugging
                                        log(f"    - {server_name}: status={server_status}, tools={len(server_tools) if isinstance(server_tools, list) else 'N/A'}")
                                        log(f"      Full server data: {json.dumps(server, indent=2, default=str)[:500]}")
                                        if server_error:
                                            log(f"      Error: {server_error}", level="ERROR")
                                        if isinstance(server_tools, list) and server_tools:
                                            tool_names = [t.get('name', 'unknown') if isinstance(t, dict) else str(t) for t in server_tools[:5]]
                                            log(f"      Tools: {tool_names}")
                            
                            # Log available tools
                            if 'tools' in data:
                                tools = data.get('tools', [])
                                log(f"  Available tools: {len(tools) if isinstance(tools, list) else 'N/A'} total")
                                if isinstance(tools, list):
                                    mcp_tools = [t for t in tools if isinstance(t, dict) and t.get('name', '').startswith('mcp__')]
                                    log(f"    MCP tools: {len(mcp_tools)}")
                                    if mcp_tools:
                                        mcp_tool_names = [t.get('name', 'unknown') for t in mcp_tools[:10]]
                                        log(f"    MCP tool names: {mcp_tool_names}")
                            
                            # Log full data if it's small enough
                            if len(str(data)) < 500:
                                log(f"  SystemMessage full data: {data}")
                        # Check if SystemMessage indicates an error or completion
                        if isinstance(data, dict):
                            if data.get('status') == 'error' or data.get('error'):
                                log(f"  ⚠️ SystemMessage indicates error - this may cause receive_response() to stop", level="WARNING")
                            if data.get('status') == 'complete' or data.get('complete'):
                                log(f"  ℹ️ SystemMessage indicates completion - receive_response() may stop", level="INFO")
                    
                    # Extract session_id from ResultMessage (SDK provides this)
                    if isinstance(message, ResultMessage):
                        session_id = message.session_id
                        # Store the SDK-provided session_id for potential future resume
                        self.session_manager.store_session(thread_id, session_id)
                        log(f"  ResultMessage session_id: {session_id}")
                        log(f"  ResultMessage duration_ms: {message.duration_ms}")
                        log(f"  ResultMessage is_error: {message.is_error}")
                        log(f"  ResultMessage num_turns: {message.num_turns}")
                        log(f"  ResultMessage total_cost_usd: {message.total_cost_usd}")
                        log(f"Stored session_id {session_id} for thread {thread_id}")
                        # ResultMessage typically ends the iteration, but we'll continue to see if more messages come
                        log("  ℹ️ ResultMessage received - receive_response() should continue until this message")
                    
                    # Log AssistantMessage details
                    if isinstance(message, AssistantMessage):
                        log(f"  AssistantMessage model: {getattr(message, 'model', 'N/A')}")
                        log(f"  AssistantMessage content blocks: {len(message.content)}")
                        for i, block in enumerate(message.content):
                            block_type = type(block).__name__
                            log(f"    Block {i+1}: {block_type}")
                            if isinstance(block, ToolUseBlock):
                                log(f"      Tool: {block.name}, ID: {block.id}")
                            elif isinstance(block, TextBlock):
                                text_preview = block.text[:100] + "..." if len(block.text) > 100 else block.text
                                log(f"      Text preview: {text_preview}")
                    
                    log(f"Yielding message #{message_count} of type {message_type}")
                    yield message
                    log(f"Successfully yielded message #{message_count}, waiting for next message...")
                
                log(f"receive_response() iteration completed. Total messages: {message_count}")
            except StopAsyncIteration:
                log(f"StopAsyncIteration raised after {message_count} messages - this is normal when iteration completes", level="INFO")
            except Exception as iter_error:
                log(f"Exception during receive_response() iteration after {message_count} messages: {iter_error}", level="ERROR")
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                raise
            
            log(f"Finished streaming {message_count} messages for thread {thread_id}")
            
        except Exception as e:
            log(f"Error in send_message for thread {thread_id}: {e}", level="ERROR")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise
    
    async def get_text_response(self, thread_id: str, user_message: str) -> str:
        """
        Send a message and collect all text blocks into a single response.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            user_message: User's message text
            
        Returns:
            Combined text from all text blocks
        """
        text_parts = []
        
        async for message in self.send_message(thread_id, user_message):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
        
        return "\n".join(text_parts)
    
    async def disconnect_session(self, thread_id: str):
        """
        Disconnect a session's client.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
        """
        if thread_id in self._clients:
            client = self._clients[thread_id]
            await client.disconnect()
            del self._clients[thread_id]
    
    async def disconnect_all(self):
        """Disconnect all active clients."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()

