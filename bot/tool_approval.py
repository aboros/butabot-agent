"""Tool approval handler for Slack interactions."""

import asyncio
import uuid
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta

from slack_sdk.web.async_client import AsyncWebClient


class ToolApprovalManager:
    """Manages tool approval requests and responses."""
    
    def __init__(self, slack_client: AsyncWebClient, approval_timeout: int = 300):
        """
        Initialize tool approval manager.
        
        Args:
            slack_client: Slack Web API client for posting messages
            approval_timeout: Timeout in seconds for approval requests (default: 5 minutes)
        """
        self.slack_client = slack_client
        self.approval_timeout = approval_timeout
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}  # approval_id -> approval_data
        self._approval_events: Dict[str, asyncio.Event] = {}  # approval_id -> event
        self._approval_results: Dict[str, bool] = {}  # approval_id -> approved (True/False)
        self._approvals_by_tool_use_id: Dict[str, str] = {}  # tool_use_id -> approval_id
        self._text_wait_events: Dict[str, asyncio.Event] = {}  # tool_use_id -> event for waiting for text
        self._text_wait_timeout: float = 2.0  # Wait up to 2 seconds for AssistantMessage with text
    
    async def request_approval(
        self,
        thread_id: str,
        channel_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
        text_content: str = "",
    ) -> Tuple[str, bool, Optional[str]]:
        """
        Request tool approval from Slack user.
        
        Args:
            thread_id: Slack thread timestamp
            channel_id: Slack channel ID
            tool_name: Name of the tool being requested
            tool_input: Input parameters for the tool
            tool_use_id: Agent SDK tool use ID
            text_content: Optional text content from Claude's response explaining the tool use
            
        Returns:
            Tuple of (approval_id, approved, message_ts) where:
            - approval_id: Unique approval request ID
            - approved: True if approved, False if denied
            - message_ts: Timestamp of the approval message (None if posting failed)
        """
        approval_id = str(uuid.uuid4())
        
        # Create event for this approval
        approval_event = asyncio.Event()
        self._approval_events[approval_id] = approval_event
        
        # Create event to wait for text content from AssistantMessage
        text_wait_event = asyncio.Event()
        self._text_wait_events[tool_use_id] = text_wait_event
        
        # Store approval data
        self._pending_approvals[approval_id] = {
            "thread_id": thread_id,
            "channel_id": channel_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
            "text_content": text_content,
            "created_at": datetime.now(),
            "message_ts": None,  # Will be set after posting
            "text_posted": False,  # Track if explanation text was posted
        }
        
        # Store mapping from tool_use_id to approval_id for post-processing
        self._approvals_by_tool_use_id[tool_use_id] = approval_id
        
        # If we already have text (from previous message), use it immediately
        if text_content:
            self._pending_approvals[approval_id]["text_posted"] = True
        else:
            # Wait briefly for AssistantMessage to arrive with text blocks
            # This allows us to include Claude's explanation in the approval message
            try:
                await asyncio.wait_for(text_wait_event.wait(), timeout=self._text_wait_timeout)
                # Text arrived - check if it was set in approval_data
                if approval_id in self._pending_approvals:
                    text_content = self._pending_approvals[approval_id].get("text_content", "")
                    if text_content:
                        self._pending_approvals[approval_id]["text_posted"] = True
            except asyncio.TimeoutError:
                # Timeout - proceed without text (AssistantMessage didn't arrive in time)
                # This is fine, we'll post approval without explanation text
                pass
            finally:
                # Clean up wait event
                if tool_use_id in self._text_wait_events:
                    del self._text_wait_events[tool_use_id]
        
        # Now post approval message (with or without text)
        # Send Claude's explanation first if we have it
        if text_content:
            try:
                await self.slack_client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_id,
                    text=text_content
                )
            except Exception as e:
                print(f"Error posting explanation message: {e}")
        
        # Format tool input for display
        tool_input_str = self._format_tool_input(tool_input)
        
        # Build the approval request message (without explanation)
        message_text = f"*Tool Approval Request*\n\n*Tool:* `{tool_name}`\n\n*Input:*\n```\n{tool_input_str}\n```"
        
        # Post approval request message
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Approve"
                        },
                        "style": "primary",
                        "value": approval_id,
                        "action_id": "tool_approve"
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Deny"
                        },
                        "style": "danger",
                        "value": approval_id,
                        "action_id": "tool_deny"
                    }
                ]
            }
        ]
        
        try:
            response = await self.slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_id,
                blocks=blocks,
                text=f"Tool approval requested: {tool_name}"
            )
            message_ts = response.get("ts")
            # Store message_ts in approval data
            if approval_id in self._pending_approvals:
                self._pending_approvals[approval_id]["message_ts"] = message_ts
        except Exception as e:
            # If we can't post, deny by default
            print(f"Error posting approval request: {e}")
            self._cleanup_approval(approval_id)
            return approval_id, False, None
        
        # Wait for approval with timeout
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=self.approval_timeout)
            approved = self._approval_results.get(approval_id, False)
        except asyncio.TimeoutError:
            # Timeout - deny by default
            await self.slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_id,
                text=f"⏱️ Tool approval request timed out. Denying `{tool_name}`."
            )
            approved = False
            # Clean up mapping and wait event on timeout
            if tool_use_id in self._approvals_by_tool_use_id:
                del self._approvals_by_tool_use_id[tool_use_id]
            if tool_use_id in self._text_wait_events:
                del self._text_wait_events[tool_use_id]
        
        message_ts = self._pending_approvals.get(approval_id, {}).get("message_ts")
        # Don't clean up approval data here - keep it for tool result updates
        # Cleanup will happen in update_approval_message_with_result or after timeout
        return approval_id, approved, message_ts
    
    def handle_approval_response(self, approval_id: str, approved: bool):
        """
        Handle approval response from Slack button click.
        
        Args:
            approval_id: Approval request ID
            approved: True if approved, False if denied
        """
        if approval_id in self._approval_events:
            self._approval_results[approval_id] = approved
            self._approval_events[approval_id].set()
    
    def _cleanup_approval(self, approval_id: str):
        """Clean up approval data."""
        # Clean up tool_use_id mapping and wait events if they exist
        if approval_id in self._pending_approvals:
            tool_use_id = self._pending_approvals[approval_id].get("tool_use_id")
            if tool_use_id:
                if tool_use_id in self._approvals_by_tool_use_id:
                    del self._approvals_by_tool_use_id[tool_use_id]
                if tool_use_id in self._text_wait_events:
                    del self._text_wait_events[tool_use_id]
        
        if approval_id in self._approval_events:
            del self._approval_events[approval_id]
        if approval_id in self._pending_approvals:
            del self._pending_approvals[approval_id]
        if approval_id in self._approval_results:
            del self._approval_results[approval_id]
    
    def _format_tool_input(self, tool_input: Dict[str, Any]) -> str:
        """Format tool input for display in Slack message."""
        import json
        try:
            return json.dumps(tool_input, indent=2)
        except Exception:
            return str(tool_input)
    
    async def update_approval_message_with_text(
        self,
        tool_use_id: str,
        text_content: str,
    ) -> bool:
        """
        Signal that text content has arrived and update approval data.
        
        This is called when we receive the AssistantMessage with text blocks.
        If the approval message hasn't been posted yet (we're waiting), this will
        signal the wait event so the approval can be posted with text included.
        If the approval was already posted, we'll post the text as a separate message.
        
        Args:
            tool_use_id: Agent SDK tool use ID
            text_content: Text content from Claude's explanation
            
        Returns:
            True if handled successfully, False otherwise
        """
        # Find approval by tool_use_id using the mapping
        approval_id = self._approvals_by_tool_use_id.get(tool_use_id)
        if not approval_id:
            return False  # No approval found (maybe already cleaned up or not created yet)
        
        approval_data = self._pending_approvals.get(approval_id)
        if not approval_data:
            return False
        
        # Store text content in approval data
        approval_data["text_content"] = text_content
        
        # Signal waiting hook that text arrived (if still waiting)
        if tool_use_id in self._text_wait_events:
            self._text_wait_events[tool_use_id].set()
            # Text will be included when approval message is posted
            return True
        
        # If approval message was already posted (timeout occurred or posted early),
        # post text as separate message. This handles the case where text arrives
        # after the 2-second timeout.
        if approval_data.get("message_ts"):
            # Check if text was already posted to avoid duplicates
            if approval_data.get("text_posted"):
                return True  # Already posted, nothing to do
            
            # Post text as separate message (approval was already posted)
            channel_id = approval_data.get("channel_id")
            thread_id = approval_data.get("thread_id")
            
            try:
                await self.slack_client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_id,
                    text=text_content
                )
                approval_data["text_posted"] = True
                return True
            except Exception as e:
                print(f"Error posting explanation text: {e}")
                return False
        
        # Approval message not posted yet - text will be included when it's posted
        return True
    
    async def update_approval_message_with_result(
        self,
        tool_use_id: str,
        tool_result: Any,
        is_error: bool = False,
    ) -> bool:
        """
        Update the approval message with tool execution results.
        
        Args:
            tool_use_id: Agent SDK tool use ID
            tool_result: Tool execution result (will be formatted as JSON)
            is_error: Whether the result is an error
            
        Returns:
            True if message was updated successfully, False otherwise
        """
        # Find approval by tool_use_id using the mapping
        approval_id = self._approvals_by_tool_use_id.get(tool_use_id)
        if not approval_id:
            # Fallback: search through pending approvals (for backwards compatibility)
            for aid, data in self._pending_approvals.items():
                if data.get("tool_use_id") == tool_use_id:
                    approval_id = aid
                    break
        
        if not approval_id:
            return False
        
        approval_data = self._pending_approvals.get(approval_id)
        if not approval_data or not approval_data.get("message_ts"):
            return False
        
        channel_id = approval_data.get("channel_id")
        message_ts = approval_data.get("message_ts")
        tool_name = approval_data.get("tool_name", "unknown")
        tool_input = approval_data.get("tool_input", {})
        
        # Format tool result
        tool_result_str = self._format_tool_result(tool_result)
        
        # Build updated message
        tool_input_str = self._format_tool_input(tool_input)
        status_emoji = "✅" if not is_error else "❌"
        status_text = "Completed" if not is_error else "Failed"
        
        message_text = f"{status_emoji} *Tool {status_text}*\n\n"
        message_text += f"*Tool:* `{tool_name}`\n"
        message_text += f"\n*Input parameters:*\n```\n{tool_input_str}\n```\n"
        message_text += f"\n*Result:*\n```\n{tool_result_str}\n```"
        
        try:
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message_text,
                blocks=[]  # Remove any blocks
            )
            # Clean up approval data after successful update
            if approval_id:
                # Clean up tool_use_id mapping
                if tool_use_id in self._approvals_by_tool_use_id:
                    del self._approvals_by_tool_use_id[tool_use_id]
                self._cleanup_approval(approval_id)
            return True
        except Exception as e:
            print(f"Error updating approval message with result: {e}")
            return False
    
    def _format_tool_result(self, tool_result: Any) -> str:
        """Format tool result for display in Slack message."""
        import json
        try:
            # If it's already a string, try to parse as JSON for pretty printing
            if isinstance(tool_result, str):
                try:
                    parsed = json.loads(tool_result)
                    return json.dumps(parsed, indent=2)
                except (json.JSONDecodeError, ValueError):
                    return tool_result
            # If it's a dict or list, format as JSON
            elif isinstance(tool_result, (dict, list)):
                return json.dumps(tool_result, indent=2)
            # Otherwise convert to string
            else:
                return str(tool_result)
        except Exception:
            return str(tool_result)
    
    def store_tool_use_mapping(self, tool_use_id: str, approval_id: str):
        """
        Store mapping from tool_use_id to approval_id for later result updates.
        
        Args:
            tool_use_id: Agent SDK tool use ID
            approval_id: Approval request ID
        """
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["tool_use_id"] = tool_use_id
    
    def format_approval_message(self, tool_name: str, tool_input: Dict[str, Any], text_content: str = "", approved: bool = True) -> str:
        """
        Format an informative approval/denial message.
        
        Args:
            tool_name: Name of the tool
            tool_input: Tool input parameters
            text_content: Optional text content from Claude's explanation (not included in message, kept for API compatibility)
            approved: True if approved, False if denied
            
        Returns:
            Formatted message text
        """
        tool_input_str = self._format_tool_input(tool_input)
        status_emoji = "✅" if approved else "❌"
        status_text = "Approved" if approved else "Denied"
        
        message = f"{status_emoji} *Tool {status_text}*\n\n"
        message += f"*Tool:* `{tool_name}`\n"
        message += f"\n*Input parameters:*\n```\n{tool_input_str}\n```"
        
        if approved:
            message += "\n\n_Executing tool..._"
        else:
            message += "\n\n_Tool execution cancelled._"
        
        return message
    
    def get_pending_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get pending approval data."""
        return self._pending_approvals.get(approval_id)
    
    async def create_approval_callback(self, channel_id: str):
        """
        Create an approval callback function for use with ClaudeClient.
        
        Args:
            channel_id: Slack channel ID
            
        Returns:
            Callback function that can be passed to ClaudeClient
        """
        async def approval_callback(
            thread_id: str,
            tool_info: Dict[str, Any],
            tool_use_id: str,
        ) -> Dict[str, Any]:
            """
            Callback for tool approval.
            
            Args:
                thread_id: Slack thread ID
                tool_info: Tool information dict with 'tool_name' and 'tool_input'
                tool_use_id: Agent SDK tool use ID
                
            Returns:
                Dict with 'decision' ('allow'|'deny'), optional 'message', and 'approval_id'
            """
            tool_name = tool_info.get("tool_name", "unknown")
            tool_input = tool_info.get("tool_input", {})
            text_content = tool_info.get("text_content", "")
            
            # Request approval
            approval_id, approved, message_ts = await self.request_approval(
                thread_id=thread_id,
                channel_id=channel_id,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_use_id=tool_use_id,
                text_content=text_content,
            )
            
            # Store mapping for later result updates (only if approved)
            if approved and message_ts:
                self.store_tool_use_mapping(tool_use_id, approval_id)
            elif not approved:
                # Clean up approval data if denied (no tool result will come)
                self._cleanup_approval(approval_id)
            
            if approved:
                return {
                    "decision": "allow",
                    "message": f"Tool {tool_name} approved",
                    "approval_id": approval_id,
                }
            else:
                return {
                    "decision": "deny",
                    "message": f"Tool {tool_name} denied or timed out",
                    "approval_id": approval_id,
                }
        
        return approval_callback

