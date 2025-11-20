"""Claude client wrapper for thread-aware conversations."""

from typing import Any, AsyncIterator, Callable, Dict, Optional, Union

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
        tool_approval_callback: Optional[Callable[[str, Dict[str, Any], str], Dict[str, Any]]] = None,
        tool_approval_manager: Optional[Any] = None,
    ):
        """
        Initialize Claude client.
        
        Args:
            session_manager: Session manager for thread-to-session mapping
            tool_approval_callback: Optional callback for tool approval handling.
                                   Called with (thread_id, tool_info, tool_use_id)
                                   Should return dict with 'decision' ('allow'|'deny') and optional 'message'
            tool_approval_manager: Optional ToolApprovalManager instance for updating approval messages with results
        """
        self.session_manager = session_manager
        self.tool_approval_callback = tool_approval_callback
        self.tool_approval_manager = tool_approval_manager
        self._clients: Dict[str, ClaudeSDKClient] = {}  # thread_id -> client
        self._text_blocks_by_tool_use: Dict[str, str] = {}  # tool_use_id -> text blocks from the same AssistantMessage
        self._pending_text_blocks: Dict[str, str] = {}  # thread_id -> text blocks waiting for next tool use
        
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
            
            # Add tool approval hook if callback is provided
            if self.tool_approval_callback:
                options.hooks = {
                    "PreToolUse": [
                        HookMatcher(
                            hooks=[self._create_tool_approval_hook(thread_id)]
                        )
                    ]
                }
            
            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._clients[thread_id] = client
        
        return self._clients[thread_id]
    
    def _create_tool_approval_hook(self, thread_id: str):
        """Create a PreToolUse hook for tool approval."""
        async def tool_approval_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext
        ) -> Dict[str, Any]:
            """Hook that requests tool approval before execution."""
            if not self.tool_approval_callback:
                return {"hookSpecificOutput": {"permissionDecision": "allow"}}

            tool_name = input_data.get("tool_name", "unknown")
            tool_input = input_data.get("tool_input", {})
            
            # Get text blocks associated with this specific tool use
            # First try to get text blocks mapped to this tool_use_id
            text_content = self._text_blocks_by_tool_use.get(tool_use_id or "", "")
            
            # If not found, try to get pending text blocks for this thread
            # (for cases where text came before tool use in a previous message)
            if not text_content:
                text_content = self._pending_text_blocks.get(thread_id, "")
                # Clear pending text after using it
                if text_content and thread_id in self._pending_text_blocks:
                    del self._pending_text_blocks[thread_id]

            # Call the approval callback
            result = await self.tool_approval_callback(
                thread_id,
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "text_content": text_content,  # Include text blocks from Claude's response
                },
                tool_use_id or "",
            )

            decision = result.get("decision", "deny")

            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": result.get("message", ""),
                }
            }

        return tool_approval_hook
    
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
            # Process AssistantMessages to map text blocks to tool use IDs
            if isinstance(message, AssistantMessage):
                # Clear any pending text blocks for this thread when we get a new message
                # This ensures old text doesn't leak to new tool uses
                if thread_id in self._pending_text_blocks:
                    del self._pending_text_blocks[thread_id]
                
                # Process content blocks in order to associate text with tool uses
                text_parts = []
                tool_use_blocks = []
                
                # First pass: collect all text blocks, tool use blocks, and tool result blocks
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_use_blocks.append(block)
                    elif isinstance(block, ToolResultBlock):
                        # Update approval message with tool result
                        if self.tool_approval_manager:
                            tool_use_id = block.tool_use_id
                            tool_result_content = block.content
                            is_error = block.is_error or False
                            
                            # Format tool result content
                            if tool_result_content is None:
                                tool_result = "(No result returned)"
                            elif isinstance(tool_result_content, list):
                                # If it's a list of content blocks, extract text
                                result_text = ""
                                for item in tool_result_content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        result_text += item.get("text", "")
                                tool_result = result_text if result_text else str(tool_result_content)
                            elif isinstance(tool_result_content, str):
                                tool_result = tool_result_content
                            else:
                                tool_result = str(tool_result_content)
                            
                            # Update the approval message
                            await self.tool_approval_manager.update_approval_message_with_result(
                                tool_use_id=tool_use_id,
                                tool_result=tool_result,
                                is_error=is_error,
                            )
                
                # Build the text content from all text blocks
                text_content = "\n".join(text_parts) if text_parts else ""
                
                # CRITICAL: When we receive AssistantMessage with both TextBlocks and ToolUseBlocks,
                # the PreToolUse hook has already been called (by the CLI before sending us the message).
                # So we need to update approval messages that were created without text content.
                if text_content and tool_use_blocks:
                    for tool_use_block in tool_use_blocks:
                        # Map for future reference (for hooks in subsequent messages)
                        self._text_blocks_by_tool_use[tool_use_block.id] = text_content
                        
                        # Update approval message if it exists and doesn't have text content yet
                        # This handles the timing issue where hook runs before we receive the message
                        if self.tool_approval_manager:
                            await self.tool_approval_manager.update_approval_message_with_text(
                                tool_use_id=tool_use_block.id,
                                text_content=text_content,
                            )
                elif text_content and not tool_use_blocks:
                    # Text blocks without tool uses - store for next tool use
                    # This handles cases where Claude explains something before using a tool
                    self._pending_text_blocks[thread_id] = text_content
            
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
        
        # Clean up pending text blocks for this thread
        if thread_id in self._pending_text_blocks:
            del self._pending_text_blocks[thread_id]
        
        # Note: We can't easily clean up text_blocks_by_tool_use by thread_id since we key by tool_use_id
        # The tool_use_ids will be cleaned up naturally as they're used
    
    async def disconnect_all(self):
        """Disconnect all active clients."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
        self._text_blocks_by_tool_use.clear()
        self._pending_text_blocks.clear()

