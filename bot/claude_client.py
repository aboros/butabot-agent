"""Claude client wrapper for thread-aware conversations."""

import base64
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import aiohttp
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
        # Check if tool approvals are disabled via environment variable
        disable_approvals = os.getenv("DISABLE_TOOL_APPROVALS", "").lower() in ("true", "1", "yes")
        
        # Request approval (unless disabled)
        approved = True
        if self.approval_manager and not disable_approvals:
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
        elif disable_approvals:
            # Approvals disabled - log what would have happened but proceed with execution
            log_info(f"[APPROVALS DISABLED] Would request approval for tool: {tool_name} (tool_use_id: {tool_use_id})")
            log_info(f"[APPROVALS DISABLED] Tool input: {tool_input}")
            approved = True  # Auto-approve when disabled
        
        if not approved:
            return False, "Tool execution denied by user"
        
        # Execute tool via MCP client
        try:
            log_info(f"Executing tool: {tool_name} with input: {tool_input}")
            
            # Send feedback about tool execution starting
            feedback_callback = self._feedback_callbacks.get(thread_id)
            if feedback_callback:
                try:
                    await feedback_callback(thread_id, f"⚙️ Executing tool `{tool_name}`...")
                except Exception as e:
                    log_error(f"Error sending tool execution feedback: {e}")
            
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
            
            # Handle image generation results (post image directly to Slack)
            if isinstance(result, dict) and "image_base64" in result and not is_error:
                await self._post_image_to_slack(
                    thread_id=thread_id,
                    tool_name=tool_name,
                    image_base64=result["image_base64"],
                    mime_type=result.get("mime_type", "image/png"),
                    prompt=result.get("prompt", "")
                )
                # Return a simple success message for Claude
                result = "Image generated and posted to Slack successfully."
            
            # Format tool result for display
            if isinstance(result, str):
                result_preview = result[:500] + "..." if len(result) > 500 else result
            elif isinstance(result, (dict, list)):
                result_str = json.dumps(result, indent=2)
                result_preview = result_str[:500] + "..." if len(result_str) > 500 else result_str
            else:
                result_preview = str(result)[:500] + ("..." if len(str(result)) > 500 else "")
            
            # Send tool result to Slack via feedback callback
            if feedback_callback:
                try:
                    status_emoji = "✅" if not is_error else "❌"
                    result_message = f"{status_emoji} *Tool `{tool_name}` completed*\n\n*Result:*\n```\n{result_preview}\n```"
                    await feedback_callback(thread_id, result_message)
                except Exception as e:
                    log_error(f"Error sending tool result feedback: {e}")
            
            # Update approval message with result (only if approvals are enabled)
            disable_approvals = os.getenv("DISABLE_TOOL_APPROVALS", "").lower() in ("true", "1", "yes")
            if self.approval_manager and not disable_approvals:
                await self.approval_manager.update_approval_message_with_result(
                    tool_use_id=tool_use_id,
                    tool_result=result,
                    is_error=is_error
                )
            elif disable_approvals:
                log_info(f"[APPROVALS DISABLED] Tool {tool_name} completed. Result: {str(result)[:200]}...")
            
            return True, result
            
        except Exception as e:
            # Some errors (like validation errors) may still raise exceptions
            error_msg = f"Tool execution error: {str(e)}"
            log_error(f"Error executing tool {tool_name}: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            
            # Send error feedback to Slack
            feedback_callback = self._feedback_callbacks.get(thread_id)
            if feedback_callback:
                try:
                    error_feedback = f"❌ *Tool `{tool_name}` failed*\n\n*Error:*\n```\n{error_msg}\n```"
                    await feedback_callback(thread_id, error_feedback)
                except Exception as feedback_error:
                    log_error(f"Error sending tool error feedback: {feedback_error}")
            
            # Update approval message with error (only if approvals are enabled)
            disable_approvals = os.getenv("DISABLE_TOOL_APPROVALS", "").lower() in ("true", "1", "yes")
            if self.approval_manager and not disable_approvals:
                await self.approval_manager.update_approval_message_with_result(
                    tool_use_id=tool_use_id,
                    tool_result=error_msg,
                    is_error=True
                )
            elif disable_approvals:
                log_info(f"[APPROVALS DISABLED] Tool {tool_name} failed with error: {error_msg}")
            
            return True, error_msg  # Return error as result (Claude will handle it)
    
    async def _post_image_to_slack(
        self,
        thread_id: str,
        tool_name: str,
        image_base64: str,
        mime_type: str = "image/png",
        prompt: str = ""
    ) -> None:
        """
        Post an image to Slack using the new 3-step file upload API.
        
        Uses files.getUploadURLExternal -> POST to upload URL -> files.completeUploadExternal
        as per https://docs.slack.dev/messaging/working-with-files#uploading_files
        
        Args:
            thread_id: Slack thread timestamp
            tool_name: Name of the tool that generated the image
            image_base64: Base64-encoded image data
            mime_type: MIME type of the image (default: "image/png")
            prompt: Original prompt used to generate the image
        """
        if not self.approval_manager:
            log_error("Cannot post image: approval_manager not available")
            return
        
        channel_id = self._channel_ids.get(thread_id)
        if not channel_id:
            log_error(f"Cannot post image: channel_id not found for thread {thread_id}")
            return
        
        slack_client = self.approval_manager.slack_client
        
        try:
            # Decode base64 image data
            image_bytes = base64.b64decode(image_base64)
            file_length = len(image_bytes)
            
            # Determine file extension from MIME type
            file_ext = "png"
            if "jpeg" in mime_type or "jpg" in mime_type:
                file_ext = "jpg"
            elif "gif" in mime_type:
                file_ext = "gif"
            elif "webp" in mime_type:
                file_ext = "webp"
            
            # Generate filename
            filename = f"generated_image.{file_ext}"
            if prompt:
                # Create a sanitized filename from prompt (first 30 chars, alphanumeric only)
                sanitized = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in prompt[:30])
                filename = f"{sanitized}.{file_ext}"
            
            title = f"Generated image: {prompt[:100] if prompt else tool_name}"
            initial_comment = f"🎨 Image generated using `{tool_name}`"
            
            log_info(f"Uploading image to Slack: {filename} ({file_length} bytes)")
            
            # Step 1: Get upload URL and file ID
            upload_response = await slack_client.files_getUploadURLExternal(
                filename=filename,
                length=file_length,
            )
            
            if not upload_response.get("ok"):
                error = upload_response.get("error", "Unknown error")
                raise Exception(f"Failed to get upload URL: {error}")
            
            upload_url = upload_response["upload_url"]
            file_id = upload_response["file_id"]
            
            log_info(f"Got upload URL and file_id: {file_id}")
            
            # Step 2: Upload file content to the upload URL
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    upload_url,
                    data=image_bytes,
                    headers={"Content-Type": "application/octet-stream"},
                ) as upload_resp:
                    if upload_resp.status != 200:
                        error_text = await upload_resp.text()
                        raise Exception(f"Failed to upload file: HTTP {upload_resp.status} - {error_text}")
                    
                    upload_result = await upload_resp.text()
                    log_info(f"File uploaded to URL: {upload_result}")
            
            # Step 3: Complete the upload and share to channel
            complete_response = await slack_client.files_completeUploadExternal(
                files=[{"id": file_id, "title": title}],
                channel_id=channel_id,
                initial_comment=initial_comment,
                thread_ts=thread_id,
            )
            
            if not complete_response.get("ok"):
                error = complete_response.get("error", "Unknown error")
                raise Exception(f"Failed to complete upload: {error}")
            
            # Log success
            files = complete_response.get("files", [])
            if files:
                uploaded_file_id = files[0].get("id", file_id)
                log_info(f"Image uploaded successfully to Slack: file_id={uploaded_file_id}")
            else:
                log_info(f"Image uploaded successfully to Slack: file_id={file_id}")
                
        except Exception as e:
            log_error(f"Error uploading image to Slack: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            # Try to send error message via feedback
            feedback_callback = self._feedback_callbacks.get(thread_id)
            if feedback_callback:
                try:
                    await feedback_callback(thread_id, f"❌ Error uploading generated image to Slack: {str(e)}")
                except Exception:
                    pass
    
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
