"""Tool approval handler for Slack interactions."""

import asyncio
import uuid
from typing import Any, Dict, Optional, Tuple
from datetime import datetime

from slack_sdk.web.async_client import AsyncWebClient
from .logger import log_error, log_slack_api_call


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
    
    async def request_approval(
        self,
        thread_id: str,
        channel_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> Tuple[str, bool, Optional[str]]:
        """
        Request tool approval from Slack user.
        
        Args:
            thread_id: Slack thread timestamp
            channel_id: Slack channel ID
            tool_name: Name of the tool being requested
            tool_input: Input parameters for the tool
            tool_use_id: Agent SDK tool use ID
            
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
        
        # Store approval data
        self._pending_approvals[approval_id] = {
            "thread_id": thread_id,
            "channel_id": channel_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
            "created_at": datetime.now(),
            "message_ts": None,  # Will be set after posting
        }
        
        # Store mapping from tool_use_id to approval_id for post-processing
        self._approvals_by_tool_use_id[tool_use_id] = approval_id
        
        # Format tool input for display
        tool_input_str = self._format_tool_input(tool_input)
        
        # Build the approval request message
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
                text=f"Tool approval requested: {tool_name}",
                unfurl_links=False,
                unfurl_media=False
            )
            message_ts = response.get("ts")
            # Store message_ts in approval data
            if approval_id in self._pending_approvals:
                self._pending_approvals[approval_id]["message_ts"] = message_ts
        except Exception as e:
            # If we can't post, deny by default
            log_error(f"Error posting approval request: {e}")
            self._cleanup_approval(approval_id)
            return approval_id, False, None
        
        # Wait for approval with timeout
        try:
            await asyncio.wait_for(approval_event.wait(), timeout=self.approval_timeout)
            approved = self._approval_results.get(approval_id, False)
        except asyncio.TimeoutError:
            # Timeout - deny by default
            additional_info = f"type=timeout | tool={tool_name}"
            if tool_use_id:
                additional_info += f" | tool_use_id={tool_use_id}"
            log_slack_api_call(method="chat_postMessage", thread_ts=thread_id, additional_info=additional_info)
            response = await self.slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_id,
                text=f"⏱️ Tool approval request timed out. Denying `{tool_name}`.",
                unfurl_links=False,
                unfurl_media=False
            )
            if response and isinstance(response, dict):
                response_ts = response.get("ts")
                if response_ts:
                    log_slack_api_call(method="chat_postMessage", thread_ts=thread_id, ts=response_ts, additional_info=additional_info)
            approved = False
            # Clean up mapping on timeout
            if tool_use_id in self._approvals_by_tool_use_id:
                del self._approvals_by_tool_use_id[tool_use_id]
        
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
        # Clean up tool_use_id mapping if it exists
        if approval_id in self._pending_approvals:
            tool_use_id = self._pending_approvals[approval_id].get("tool_use_id")
            if tool_use_id and tool_use_id in self._approvals_by_tool_use_id:
                del self._approvals_by_tool_use_id[tool_use_id]
        
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
            return json.dumps(tool_input, indent=2, ensure_ascii=False)
        except Exception:
            return str(tool_input)
    
    async def update_approval_message_with_result(
        self,
        tool_use_id: str,
        tool_result: Any,
        is_error: bool = False,
    ) -> bool:
        """
        Update the approval message to show that tool execution completed.
        
        Args:
            tool_use_id: Agent SDK tool use ID
            tool_result: Tool execution result (not displayed, kept for API compatibility)
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
        
        # Get the original approval message format and update the context block
        tool_input = approval_data.get("tool_input", {})
        tool_input_str = self._format_tool_input(tool_input)
        
        # Build message using same format as format_approval_message
        status_emoji = "✅" if not is_error else "❌"
        status_text = "Approved"  # Keep "Approved" status since it was approved
        
        message_text = f"{status_emoji} *Tool {status_text}*\n\n"
        message_text += f"*Tool:* `{tool_name}`\n"
        message_text += f"\n*Input parameters:*\n```\n{tool_input_str}\n```"
        
        # Build blocks with updated context block
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Results received._"
                    }
                ]
            }
        ]
        
        try:
            additional_info = f"type=approval_result | tool={tool_name} | is_error={is_error}"
            if tool_use_id:
                additional_info += f" | tool_use_id={tool_use_id}"
            log_slack_api_call(method="chat_update", thread_ts=approval_data.get("thread_id"), ts=message_ts, additional_info=additional_info)
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Tool {status_text}: {tool_name} - Results received",
                blocks=blocks,
                unfurl_links=False,
                unfurl_media=False
            )
            # Clean up approval data after successful update
            if approval_id:
                # Clean up tool_use_id mapping
                if tool_use_id in self._approvals_by_tool_use_id:
                    del self._approvals_by_tool_use_id[tool_use_id]
                self._cleanup_approval(approval_id)
            return True
        except Exception as e:
            log_error(f"Error updating approval message with result: {e}")
            return False
    
    def store_tool_use_mapping(self, tool_use_id: str, approval_id: str):
        """
        Store mapping from tool_use_id to approval_id for later result updates.
        
        Args:
            tool_use_id: Agent SDK tool use ID
            approval_id: Approval request ID
        """
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id]["tool_use_id"] = tool_use_id
    
    def format_approval_message(self, tool_name: str, tool_input: Dict[str, Any], approved: bool = True) -> list:
        """
        Format an informative approval/denial message as Slack blocks.
        
        Args:
            tool_name: Name of the tool
            tool_input: Tool input parameters
            approved: True if approved, False if denied
            
        Returns:
            List of Slack block objects
        """
        tool_input_str = self._format_tool_input(tool_input)
        status_emoji = "✅" if approved else "❌"
        status_text = "Approved" if approved else "Denied"
        
        message_text = f"{status_emoji} *Tool {status_text}*\n\n"
        message_text += f"*Tool:* `{tool_name}`\n"
        message_text += f"\n*Input parameters:*\n```\n{tool_input_str}\n```"
        
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message_text
                }
            }
        ]
        
        # Add context block for status
        if approved:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Executing tool..._"
                    }
                ]
            })
        else:
            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_Tool execution cancelled._"
                    }
                ]
            })
        
        return blocks
    
    def get_pending_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        """Get pending approval data."""
        return self._pending_approvals.get(approval_id)

