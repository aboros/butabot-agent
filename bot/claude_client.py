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

from .mcp_config import load_mcp_config
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

