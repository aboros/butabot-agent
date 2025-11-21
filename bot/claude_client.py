"""Claude client wrapper for thread-aware conversations."""

import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from anthropic import AsyncAnthropic

from .session_manager import SessionManager
from .mcp_client import MCPClientWrapper
from .logger import (
    log_info,
    log_error,
    log_send_to_agent,
    log_agent_message,
)


class ClaudeClient:
    """Wrapper around Anthropic client for thread-aware Slack conversations."""
    
    def __init__(
        self,
        session_manager: SessionManager,
        api_key: Optional[str] = None,
        mcp_config_path: Optional[Path] = None,
        feedback_callback: Optional[Callable[[str, str], Any]] = None,
        approval_manager: Optional[Any] = None,
        model: str = "claude-sonnet-4-5",
        max_tokens: int = 4096,
    ):
        """
        Initialize Claude client.
        
        Args:
            session_manager: Session manager for thread-to-message mapping
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            mcp_config_path: Path to .mcp.json configuration file
            feedback_callback: Optional callback for sending feedback messages
            approval_manager: Optional ToolApprovalManager instance
            model: Claude model to use
            max_tokens: Maximum tokens per response
        """
        self.session_manager = session_manager
        self.feedback_callback = feedback_callback
        self.approval_manager = approval_manager
        self.model = model
        self.max_tokens = max_tokens
        
        # Initialize Anthropic async client
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable or api_key parameter required")
        self.client = AsyncAnthropic(api_key=api_key)
        
        # Initialize MCP client
        mcp_path = mcp_config_path or Path("/app/.mcp.json")
        self.mcp_client = MCPClientWrapper(mcp_path)
        
        # Cache Anthropic tool definitions (lazy-loaded)
        self._anthropic_tools: Optional[List[Dict[str, Any]]] = None
        self._tools_logged = False
        
        # Per-thread state
        self._feedback_callbacks: Dict[str, Callable[[str, str], Any]] = {}
        self._channel_ids: Dict[str, str] = {}
    
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
    
    async def _get_anthropic_tools(self) -> List[Dict[str, Any]]:
        """Get Anthropic tool definitions from MCP servers."""
        if self._anthropic_tools is None:
            # Ensure MCP client is initialized
            await self.mcp_client.ensure_initialized()
            self._anthropic_tools = self.mcp_client.get_anthropic_tools()
            
            # Log tool count on first discovery
            if not self._tools_logged:
                tool_count = len(self._anthropic_tools)
                log_info(f"Tools available: {tool_count} total")
                self._tools_logged = True
        
        return self._anthropic_tools
    
    async def send_message(
        self,
        thread_id: str,
        user_message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Send a message to Claude and stream responses with tool execution.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            user_message: User's message text
            
        Yields:
            Response messages (assistant messages, tool results, etc.)
        """
        try:
            # Get conversation history
            messages = self.session_manager.get_messages(thread_id)
            
            # Add user message
            messages.append({
                "role": "user",
                "content": user_message
            })
            self.session_manager.store_messages(thread_id, messages)
            
            # Log sending message
            message_preview = user_message[:100] + "..." if len(user_message) > 100 else user_message
            log_send_to_agent(
                thread_ts=thread_id,
                message_preview=message_preview,
                message_length=len(user_message)
            )
            
            # Get tool definitions
            tools = await self._get_anthropic_tools()
            
            # Tool execution loop (max iterations to prevent infinite loops)
            max_iterations = 10
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                
                log_info(f"Tool execution loop iteration {iteration} for thread {thread_id}")
                
                # Call Anthropic Messages API
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=messages,
                    tools=tools if tools else None,
                )
                
                log_info(f"API response stop_reason: {response.stop_reason}")
                
                # Convert Pydantic models to dictionaries for storage and processing
                content_dicts = []
                for block in response.content:
                    if hasattr(block, 'model_dump'):
                        # Pydantic v2
                        content_dicts.append(block.model_dump())
                    elif hasattr(block, 'dict'):
                        # Pydantic v1
                        content_dicts.append(block.dict())
                    else:
                        # Already a dict or fallback
                        content_dicts.append(block if isinstance(block, dict) else {"type": getattr(block, 'type', 'unknown'), "text": getattr(block, 'text', str(block))})
                
                # Add assistant response to history
                assistant_message = {
                    "role": "assistant",
                    "content": content_dicts
                }
                messages.append(assistant_message)
                self.session_manager.store_messages(thread_id, messages)
                
                # Yield assistant message
                yield {
                    "type": "assistant",
                    "content": content_dicts,
                    "model": response.model,
                }
                
                # Check if tools were used
                if response.stop_reason != "tool_use":
                    # No tools used - conversation complete
                    break
                
                # Process tool_use blocks
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        # Log tool use
                        log_agent_message(
                            message_type="ToolUse",
                            block_types=["ToolUseBlock"],
                            thread_ts=thread_id,
                            tool_name=block.name,
                            tool_use_id=block.id
                        )
                        
                        # Intercept tool use for approval
                        approved, tool_result = await self._execute_tool_with_approval(
                            thread_id=thread_id,
                            tool_use_id=block.id,
                            tool_name=block.name,
                            tool_input=block.input,
                        )
                        
                        # Format tool result for API
                        # Tool results can be strings, dicts, or lists
                        if isinstance(tool_result, str):
                            result_content = tool_result
                        elif isinstance(tool_result, (dict, list)):
                            result_content = json.dumps(tool_result)
                        else:
                            result_content = str(tool_result)
                        
                        # Add tool result to history
                        tool_result_message = {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result_content,
                                "is_error": not approved
                            }]
                        }
                        tool_results.append(tool_result_message)
                
                # Add all tool results to history at once (for parallel tool use)
                if tool_results:
                    # Combine all tool results into single user message
                    combined_tool_results = []
                    for result_msg in tool_results:
                        combined_tool_results.extend(result_msg["content"])
                    
                    log_info(f"Adding {len(combined_tool_results)} tool results to message history")
                    
                    messages.append({
                        "role": "user",
                        "content": combined_tool_results
                    })
                    self.session_manager.store_messages(thread_id, messages)
                    
                    log_info(f"Continuing tool execution loop (iteration {iteration + 1}) to get Claude's response")
                    
                    # Continue loop to get Claude's response to tool results
                    continue
            
            if iteration >= max_iterations:
                log_error(f"Tool execution loop reached max iterations for thread {thread_id}")
                
        except Exception as e:
            log_error(f"Error in send_message for thread {thread_id}: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            raise
    
    async def _execute_tool_with_approval(
        self,
        thread_id: str,
        tool_use_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> tuple[bool, Any]:
        """
        Execute a tool with approval workflow.
        
        Args:
            thread_id: Slack thread ID
            tool_use_id: Tool use ID from Claude
            tool_name: Name of the tool
            tool_input: Tool input parameters
            
        Returns:
            Tuple of (approved, result) where result is tool output or denial message
        """
        # Request approval
        approved = True
        if self.approval_manager:
            channel_id = self._channel_ids.get(thread_id)
            if channel_id:
                try:
                    approval_id, approved, message_ts = await self.approval_manager.request_approval(
                        thread_id=thread_id,
                        channel_id=channel_id,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=tool_use_id,
                    )
                    
                    if approved and message_ts:
                        self.approval_manager.store_tool_use_mapping(tool_use_id, approval_id)
                except Exception as e:
                    log_error(f"Error requesting tool approval: {e}")
                    approved = False
        
        if not approved:
            return False, "Tool execution denied by user"
        
        # Execute tool via MCP client
        try:
            log_info(f"Executing tool: {tool_name} with input: {tool_input}")
            result = await self.mcp_client.call_tool(tool_name, tool_input)
            
            # Check if result is an error dict (returned when raise_on_error=False)
            is_error = False
            if isinstance(result, dict) and result.get("error") is True:
                is_error = True
                error_msg = result.get("message", "Tool execution failed")
                log_error(f"Tool {tool_name} returned error: {error_msg}")
                result = error_msg  # Use error message as result
            
            if not is_error:
                log_info(f"Tool {tool_name} executed successfully. Result type: {type(result)}")
            
            # Update approval message with result
            if self.approval_manager:
                await self.approval_manager.update_approval_message_with_result(
                    tool_use_id=tool_use_id,
                    tool_result=result,
                    is_error=is_error
                )
            
            return True, result
            
        except Exception as e:
            # Some errors (like validation errors) may still raise exceptions
            error_msg = f"Tool execution error: {str(e)}"
            log_error(f"Error executing tool {tool_name}: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            
            # Update approval message with error
            if self.approval_manager:
                await self.approval_manager.update_approval_message_with_result(
                    tool_use_id=tool_use_id,
                    tool_result=error_msg,
                    is_error=True
                )
            
            return True, error_msg  # Return error as result (Claude will handle it)
    
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
            if message.get("type") == "assistant":
                content = message.get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
        
        return "\n".join(text_parts)
    
    async def disconnect_session(self, thread_id: str):
        """
        Disconnect a session (no-op for direct API usage).
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
        """
        # No-op: direct API usage doesn't require connection management
        pass
    
    async def disconnect_all(self):
        """Disconnect all active clients (no-op for direct API usage)."""
        # No-op: direct API usage doesn't require connection management
        pass
