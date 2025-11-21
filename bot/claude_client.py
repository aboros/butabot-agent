"""Claude client wrapper for thread-aware conversations."""

import json
import os
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
from .logger import (
    log_tools_startup,
    log_session_created,
    log_pre_tool_use,
    log_post_tool_use,
    log_agent_message,
    log_send_to_agent,
    log_info,
    log_error,
)


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
        self._tools_logged = False  # Track if we've logged tool count on startup
        
        # Read GitLab project ID from environment variable
        self.gitlab_project_id = os.getenv("GITLAB_PROJECT_ID")
        if not self.gitlab_project_id:
            log_info("GITLAB_PROJECT_ID not set - GitLab tools will require project_id in each call")
        
        # Use .mcp.json from project root - let SDK handle parsing
        # According to docs, configure MCP servers in .mcp.json at project root
        # SDK can load it directly if we pass the path
        from pathlib import Path
        mcp_config_path = Path("/app/.mcp.json")
        
        # Build options - SDK will handle parsing .mcp.json
        # Enable filesystem tools (Read, Write, Edit, Glob, Grep) for Drupal development
        # Restrict agent's working directory to /app/data for security
        # Agent can access any folders mounted in docker-compose.yml
        disallowed_tools = [
            # Keep Bash disabled for security (enable if you need Drush/Composer commands)
            # Note: Drupal MCP server can handle Drupal operations without Bash
            "Bash", "BashOutput", "KillBash",
            # Keep other dev tools disabled
            "Task", "TodoWrite", "NotebookEdit", "ExitPlanMode",
            # Filesystem tools (Read, Write, Edit, Glob, Grep) are now ENABLED
        ]
        
        self.base_options = ClaudeAgentOptions(
            mcp_servers=mcp_config_path if mcp_config_path.exists() else {},
            cwd="/app/data",  # Agent's working directory
            disallowed_tools=disallowed_tools,
        )
        
        # Store disallowed_tools for logging when tool count is available
        self._disallowed_tools = disallowed_tools
    
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
            
            # Log session creation - we'll get session_id from first ResultMessage
            # For now, log that a client was created
            log_info(f"New agent client created for thread | thread_ts={thread_id}")
        
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
            tool_input = input_data.get("tool_input", {}).copy()  # Copy to avoid mutation
            
            # Inject GitLab project_id for all GitLab MCP tools
            if tool_name.startswith("mcp__gitlab__") and self.gitlab_project_id:
                tool_input["project_id"] = self.gitlab_project_id
            
            # Log PreToolUse hook invocation
            log_pre_tool_use(tool_name=tool_name, thread_ts=thread_id, tool_use_id=tool_use_id)
            
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
                        log_error(f"Error requesting tool approval: {e}")
                        # On error, deny by default for safety
                        decision = "deny"
                        decision_reason = f"Error requesting approval: {str(e)}"
            
            # Build hook response with updatedInput if GitLab tool
            hook_output = {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": decision_reason,
            }
            
            # Include updatedInput if we modified tool_input (GitLab tools)
            if tool_name.startswith("mcp__gitlab__") and self.gitlab_project_id:
                hook_output["updatedInput"] = tool_input
            
            return {
                "hookSpecificOutput": hook_output
            }
        
        async def post_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext
        ) -> Dict[str, Any]:
            """Hook that updates approval message after tool execution."""
            tool_name = input_data.get("tool_name", "unknown")
            
            # Log PostToolUse hook invocation
            log_post_tool_use(tool_name=tool_name, thread_ts=thread_id, tool_use_id=tool_use_id)
            
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
                    log_error(f"Error updating approval message with result: {e}")
            
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
            # Get or create client (SDK maintains session automatically)
            client = await self._get_or_create_client(thread_id)
            
            # Log sending message to agent
            message_preview = user_message[:100] + "..." if len(user_message) > 100 else user_message
            log_send_to_agent(
                thread_ts=thread_id,
                message_preview=message_preview,
                message_length=len(user_message)
            )
            
            # Send message
            await client.query(user_message)
            
            # Stream responses and extract session_id from ResultMessage
            try:
                async for message in client.receive_response():
                    # Log SystemMessage to capture tool count on startup
                    if isinstance(message, SystemMessage):
                        subtype = getattr(message, 'subtype', 'unknown')
                        data = getattr(message, 'data', {})
                        
                        # Log tool count and disallowed tools on startup (first SystemMessage with tools)
                        if isinstance(data, dict) and 'tools' in data and not self._tools_logged:
                            tools = data.get('tools', [])
                            if isinstance(tools, list):
                                tool_count = len(tools)
                                log_info(f"Tools available: {tool_count} total")
                                # Log disallowed tools alongside tool count
                                if self._disallowed_tools:
                                    log_info(f"Disallowed tools: {', '.join(sorted(self._disallowed_tools))}")
                                else:
                                    log_info("Disallowed tools: none")
                                self._tools_logged = True
                        continue
                    
                    # Extract session_id from ResultMessage and log session creation
                    if isinstance(message, ResultMessage):
                        session_id = message.session_id
                        # Store the SDK-provided session_id for potential future resume
                        self.session_manager.store_session(thread_id, session_id)
                        # Log session creation
                        log_session_created(session_id=session_id, thread_ts=thread_id)
                        continue
                    
                    # Log AssistantMessage with response type and block types
                    if isinstance(message, AssistantMessage):
                        block_types = [type(block).__name__ for block in message.content]
                        # Extract tool information if ToolUseBlock is present
                        tool_name = None
                        tool_use_id = None
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                tool_name = block.name
                                tool_use_id = block.id
                                break  # Use first ToolUseBlock if multiple
                        log_agent_message(
                            message_type="AssistantMessage",
                            block_types=block_types,
                            thread_ts=thread_id,
                            tool_name=tool_name,
                            tool_use_id=tool_use_id
                        )
                    
                    yield message
                
            except StopAsyncIteration:
                # Normal completion
                pass
            except Exception as iter_error:
                log_error(f"Exception during receive_response() iteration: {iter_error}")
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
                raise
            
        except Exception as e:
            log_error(f"Error in send_message for thread {thread_id}: {e}")
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

