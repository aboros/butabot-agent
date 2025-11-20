"""Claude client wrapper for thread-aware conversations."""

import json
from typing import Any, AsyncIterator, Callable, Dict, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    Message,
    HookMatcher,
    HookContext,
)

from .mcp_config import load_mcp_config
from .session_manager import SessionManager


class ClaudeClient:
    """Wrapper around ClaudeSDKClient for thread-aware Slack conversations."""
    
    def __init__(
        self,
        session_manager: SessionManager,
        feedback_callback: Optional[Callable[[str, str], Any]] = None,
    ):
        """
        Initialize Claude client.
        
        Args:
            session_manager: Session manager for thread-to-session mapping
            feedback_callback: Optional callback for sending feedback messages to Slack.
                             Called with (thread_id, feedback_message) to send feedback.
                             Can be a callable or a dict mapping thread_id -> callback.
        """
        self.session_manager = session_manager
        self.feedback_callback = feedback_callback
        self._clients: Dict[str, ClaudeSDKClient] = {}  # thread_id -> client
        self._feedback_callbacks: Dict[str, Callable[[str, str], Any]] = {}  # thread_id -> callback
        
        # Load MCP config
        mcp_config_path = load_mcp_config()
        
        # Build options with MCP servers
        # Disable built-in filesystem tools and other unnecessary tools
        # Only allow: WebSearch, WebFetch, Skill
        self.base_options = ClaudeAgentOptions(
            mcp_servers=mcp_config_path if mcp_config_path else {},
            disallowed_tools=[
                # Filesystem tools
                "Read", "Write", "Edit", "Glob", "Grep",
                # Development tools
                "Task", "Bash", "TodoWrite", "NotebookEdit", "ExitPlanMode",
                # Process management
                "BashOutput", "KillBash",
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
            """Hook that sends feedback before tool execution."""
            # Get callback dynamically (check per-thread first, then global)
            callback = self._feedback_callbacks.get(thread_id) or self.feedback_callback or feedback_callback
            if not callback:
                return {}
            
            tool_name = input_data.get("tool_name", "unknown")
            tool_input = input_data.get("tool_input", {})
            
            # Format tool input for display
            tool_input_str = self._format_tool_input(tool_input)
            
            # Create feedback message
            feedback = f"⚡ *PreToolUse:* `{tool_name}`\n"
            feedback += f"```\n{tool_input_str}\n```"
            
            # Send feedback
            try:
                await callback(thread_id, feedback)
            except Exception as e:
                print(f"Error sending PreToolUse feedback: {e}")
            
            # Always allow tool execution
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                }
            }
        
        async def post_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext
        ) -> Dict[str, Any]:
            """Hook that sends feedback after tool execution."""
            # Get callback dynamically (check per-thread first, then global)
            callback = self._feedback_callbacks.get(thread_id) or self.feedback_callback or feedback_callback
            if not callback:
                return {}
            
            tool_name = input_data.get("tool_name", "unknown")
            tool_result = input_data.get("tool_result", {})
            is_error = input_data.get("is_error", False)
            
            # Format tool result for display
            tool_result_str = self._format_tool_result(tool_result)
            
            # Create feedback message
            status_emoji = "✅" if not is_error else "❌"
            feedback = f"{status_emoji} *PostToolUse:* `{tool_name}`\n"
            if is_error:
                feedback += f"❌ Error occurred\n"
            feedback += f"```\n{tool_result_str}\n```"
            
            # Send feedback
            try:
                await callback(thread_id, feedback)
            except Exception as e:
                print(f"Error sending PostToolUse feedback: {e}")
            
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
        # Get or create client (SDK maintains session automatically)
        client = await self._get_or_create_client(thread_id)
        
        # Send message
        await client.query(user_message)
        
        # Stream responses and extract session_id from ResultMessage
        async for message in client.receive_response():
            # Extract session_id from ResultMessage (SDK provides this)
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                # Store the SDK-provided session_id for potential future resume
                self.session_manager.store_session(thread_id, session_id)
            
            yield message
    
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

