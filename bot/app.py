"""Slack Bolt app for Butabot Agent."""

import asyncio
import json
import os
import sys
from typing import Any, Callable, Dict

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from claude_agent_sdk import (
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    SystemMessage,
    ResultMessage,
)

from .claude_client import ClaudeClient
from .session_manager import SessionManager
from .tool_approval import ToolApprovalManager


def log(message: str, level: str = "INFO"):
    """Log message with flush to ensure it appears in Docker logs."""
    print(f"[{level}] {message}", file=sys.stderr, flush=True)


# Load environment variables
load_dotenv()


class ButabotApp:
    """Main Slack bot application."""
    
    def __init__(self):
        """Initialize the bot application."""
        # Initialize Slack app
        self.app = AsyncApp(
            token=os.getenv("SLACK_BOT_TOKEN"),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
        )
        
        # Initialize components
        self.session_manager = SessionManager()
        self.slack_client = AsyncWebClient(token=os.getenv("SLACK_BOT_TOKEN"))
        self.tool_approval_manager = ToolApprovalManager(self.slack_client)
        
        # Initialize Claude client (feedback callback will be set per message)
        self.claude_client = ClaudeClient(
            session_manager=self.session_manager,
            feedback_callback=None,  # Will be set per message
            approval_manager=self.tool_approval_manager,
        )
        
        # Register event handlers
        self._register_handlers()
    
    def _create_feedback_callback(self, thread_ts: str, say: Callable) -> Callable[[str, str], Any]:
        """
        Create a feedback callback function that uses say() to send messages.
        
        Args:
            thread_ts: Slack thread timestamp
            say: Slack say function
            
        Returns:
            Async callback function that takes (thread_id, feedback_message)
        """
        async def feedback_callback(thread_id: str, feedback_message: str) -> None:
            """Send feedback message to Slack."""
            try:
                await say(
                    blocks=[{
                        "type": "markdown",
                        "text": feedback_message
                    }],
                    thread_ts=thread_ts
                )
            except Exception as e:
                print(f"Error sending feedback message: {e}")
        
        return feedback_callback
    
    def _detect_api_error(self, text: str) -> tuple[bool, str]:
        """
        Detect API errors in text and return user-friendly error message.
        
        Args:
            text: Text content to check for errors
            
        Returns:
            Tuple of (is_error, user_friendly_message)
        """
        # Check for common API error patterns
        if "529" in text and ("overloaded" in text.lower() or "Overloaded" in text):
            return True, "⚠️ *API Temporarily Overloaded*\n\nThe Claude API is currently experiencing high load. Please try again in a few moments."
        
        if "API Error" in text or '"type":"error"' in text:
            # Try to extract error details
            if "overloaded" in text.lower():
                return True, "⚠️ *API Temporarily Overloaded*\n\nThe Claude API is currently experiencing high load. Please try again in a few moments."
            elif "rate_limit" in text.lower() or "429" in text:
                return True, "⚠️ *Rate Limit Exceeded*\n\nToo many requests. Please wait a moment before trying again."
            elif "401" in text or "unauthorized" in text.lower():
                return True, "❌ *Authentication Error*\n\nThere's an issue with API authentication. Please contact support."
            elif "500" in text or "internal" in text.lower():
                return True, "❌ *API Internal Error*\n\nThe API encountered an internal error. Please try again later."
            else:
                # Generic API error
                return True, "❌ *API Error*\n\nAn error occurred while processing your request. Please try again."
        
        return False, text
    
    def _format_assistant_message(self, message: AssistantMessage) -> list[Dict[str, Any]]:
        """
        Format AssistantMessage with detailed information about its content.
        
        Args:
            message: AssistantMessage instance
            
        Returns:
            List of Slack Block Kit blocks (markdown blocks)
        """
        lines = []
        
        # Count blocks by type
        text_blocks = []
        tool_use_blocks = []
        tool_result_blocks = []
        
        for block in message.content:
            if isinstance(block, TextBlock):
                text_blocks.append(block)
            elif isinstance(block, ToolUseBlock):
                tool_use_blocks.append(block)
            elif isinstance(block, ToolResultBlock):
                tool_result_blocks.append(block)
        
        # Add block counts
        if text_blocks:
            for i, block in enumerate(text_blocks, 1):
                # Check for API errors and format accordingly
                is_error, formatted_text = self._detect_api_error(block.text)
                
                if is_error:
                    # For errors, show user-friendly message instead of raw error
                    lines.append(formatted_text)
                    log(f"Detected API error in TextBlock: {block.text[:200]}", level="WARNING")
                else:
                    # Show full text content
                    lines.append(formatted_text)
        
        if tool_use_blocks:
            # We don't cast a message for tool use blocks, 
            # so we don't need to format them.
            # Tool use will be handled by the PreToolUse hook.
            pass
        
        if tool_result_blocks:
            # Tool result will be handled by the PostToolUse hook.
            pass
        
        if not text_blocks and not tool_use_blocks and not tool_result_blocks:
            lines.append("_Claude API sent an empty message._")
        
        # Join lines and create markdown block
        markdown_text = "\n".join(lines)
        
        # Return as Slack Block Kit markdown block
        return [
            {
                "type": "markdown",
                "text": markdown_text
            }
        ]
    
    def _format_tool_input(self, tool_input: Dict[str, Any]) -> str:
        """Format tool input for display."""
        try:
            formatted = json.dumps(tool_input, indent=2)
            # Truncate if too long
            if len(formatted) > 200:
                return formatted[:200] + "..."
            return formatted
        except Exception:
            return str(tool_input)[:200]
    
    def _format_tool_result_preview(self, result: Any) -> str:
        """Format tool result preview for display."""
        try:
            if result is None:
                return "(No result)"
            elif isinstance(result, str):
                if len(result) > 100:
                    return result[:100] + "..."
                return result
            elif isinstance(result, list):
                if len(result) > 3:
                    return f"[{len(result)} items]"
                return str(result)[:100]
            elif isinstance(result, dict):
                return f"{{...{len(result)} keys...}}"
            else:
                result_str = str(result)
                return result_str[:100] + "..." if len(result_str) > 100 else result_str
        except Exception:
            return str(result)[:100]
    
    def _register_handlers(self):
        """Register Slack event and action handlers."""
        
        @self.app.event("app_mention")
        async def handle_app_mention(event: Dict[str, Any], say, client):
            """Handle bot mentions."""
            try:
                log(f"Received app_mention event: {event.get('text', '')[:100]}")
                
                # Extract event data
                channel_id = event.get("channel")
                thread_ts = event.get("thread_ts") or event.get("ts")  # Use thread_ts if in thread, else ts
                
                log(f"Processing message in channel={channel_id}, thread_ts={thread_ts}")
                
                # Get bot user ID
                auth_response = await self.slack_client.auth_test()
                bot_user_id = auth_response.get("user_id", "")
                
                # Remove bot mention from message
                user_message = event.get("text", "")
                if f"<@{bot_user_id}>" in user_message:
                    user_message = user_message.replace(f"<@{bot_user_id}>", "").strip()
                
                if not user_message:
                    await say(
                        blocks=[{
                            "type": "markdown",
                            "text": "Hello! How can I help you?"
                        }],
                        thread_ts=thread_ts
                    )
                    return
                
                log(f"Sending message to Claude: {user_message[:100]}")
                
                # Create feedback callback for this thread
                feedback_callback = self._create_feedback_callback(thread_ts, say)
                
                # Set feedback callback and channel_id for this thread on the main client
                self.claude_client.set_feedback_callback(thread_ts, feedback_callback)
                self.claude_client.set_channel_id(thread_ts, channel_id)
                
                # Send "thinking" message and store its timestamp for updates
                thinking_response = await say(
                    blocks=[{
                        "type": "markdown",
                        "text": "🤔 Thinking..."
                    }],
                    thread_ts=thread_ts
                )
                thinking_ts = thinking_response.get("ts") if thinking_response else None
                log(f"Sent thinking message, ts={thinking_ts}")
                
                try:
                    response_sent = False
                    message_count = 0
                    # Stream responses and process every AssistantMessage
                    log("Starting to iterate over claude_client.send_message()")
                    try:
                        async for message in self.claude_client.send_message(thread_ts, user_message):
                            message_count += 1
                            message_type = type(message).__name__
                            log(f"App received message #{message_count}: {message_type}")
                            
                            # Log SystemMessage details
                            if isinstance(message, SystemMessage):
                                subtype = getattr(message, 'subtype', 'unknown')
                                data = getattr(message, 'data', {})
                                log(f"  SystemMessage subtype: {subtype}")
                                log(f"  SystemMessage data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
                                if isinstance(data, dict):
                                    # Log important data fields
                                    for key in ['message', 'status', 'error', 'info']:
                                        if key in data:
                                            log(f"  SystemMessage.{key}: {str(data[key])[:200]}")
                                continue  # Continue processing after SystemMessage
                            
                            # Log ResultMessage details
                            if isinstance(message, ResultMessage):
                                log(f"  ResultMessage session_id: {getattr(message, 'session_id', 'N/A')}")
                                log(f"  ResultMessage duration_ms: {getattr(message, 'duration_ms', 'N/A')}")
                                log(f"  ResultMessage is_error: {getattr(message, 'is_error', 'N/A')}")
                                log(f"  ResultMessage num_turns: {getattr(message, 'num_turns', 'N/A')}")
                                log(f"  ResultMessage total_cost_usd: {getattr(message, 'total_cost_usd', 'N/A')}")
                                continue  # Continue processing after ResultMessage
                            
                            if isinstance(message, AssistantMessage):
                                # Check if message contains ToolUseBlocks
                                has_tool_use = any(
                                    isinstance(block, ToolUseBlock) 
                                    for block in message.content
                                )
                                
                                # Check if message contains API errors
                                has_api_error = any(
                                    isinstance(block, TextBlock) and 
                                    ("API Error" in block.text or "529" in block.text or '"type":"error"' in block.text)
                                    for block in message.content
                                )
                                
                                log(f"AssistantMessage has {len(message.content)} blocks, has_tool_use={has_tool_use}, has_api_error={has_api_error}")
                                
                                # Skip sending if message contains ToolUseBlocks (handled by PreToolUse hook)
                                if has_tool_use:
                                    log("Skipping AssistantMessage with ToolUseBlocks (handled by PreToolUse hook)")
                                    continue
                                
                                # Format and send the message
                                formatted_blocks = self._format_assistant_message(message)
                                
                                # If we have a thinking message timestamp, update it; otherwise send new message
                                if thinking_ts and not response_sent:
                                    # Update the thinking message with the actual response
                                    log(f"Updating thinking message (ts={thinking_ts})")
                                    await self.slack_client.chat_update(
                                        channel=channel_id,
                                        ts=thinking_ts,
                                        blocks=formatted_blocks,
                                        thread_ts=thread_ts
                                    )
                                    response_sent = True
                                else:
                                    # Send as new message
                                    log("Sending new message")
                                    await say(blocks=formatted_blocks, thread_ts=thread_ts)
                                    response_sent = True
                        
                        log(f"App finished iterating over send_message(). Total messages: {message_count}")
                    except StopAsyncIteration:
                        log(f"StopAsyncIteration in app after {message_count} messages - iteration completed", level="INFO")
                    except Exception as iter_error:
                        log(f"Exception during send_message() iteration in app after {message_count} messages: {iter_error}", level="ERROR")
                        import traceback
                        traceback.print_exc(file=sys.stderr)
                        sys.stderr.flush()
                        raise
                    
                    log(f"Finished processing {message_count} messages, response_sent={response_sent}")
                    
                    # If no response was sent (shouldn't happen, but handle gracefully)
                    if not response_sent and thinking_ts:
                        log("No response sent, updating thinking message with completion notice")
                        await self.slack_client.chat_update(
                            channel=channel_id,
                            ts=thinking_ts,
                            blocks=[{
                                "type": "markdown",
                                "text": "✅ Processing complete. (No text response, but tools may have been executed.)"
                            }],
                            thread_ts=thread_ts
                        )
                    
                except Exception as e:
                    error_msg = f"❌ *Error:* {str(e)}"
                    error_blocks = [{
                        "type": "markdown",
                        "text": error_msg
                    }]
                    # Update thinking message with error, or send new message if update fails
                    if thinking_ts:
                        try:
                            await self.slack_client.chat_update(
                                channel=channel_id,
                                ts=thinking_ts,
                                blocks=error_blocks,
                                thread_ts=thread_ts
                            )
                        except Exception:
                            # Fallback: send as new message
                            await say(blocks=error_blocks, thread_ts=thread_ts)
                    else:
                        await say(blocks=error_blocks, thread_ts=thread_ts)
                    
                    # Log error with flush to ensure it appears in Docker logs
                    log(f"Error handling message: {e}", level="ERROR")
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()
            except Exception as outer_e:
                # Catch any errors in the outer try block
                log(f"Outer error in handle_app_mention: {outer_e}", level="ERROR")
                import traceback
                traceback.print_exc(file=sys.stderr)
                sys.stderr.flush()
        
        @self.app.action("tool_approve")
        async def handle_tool_approve(ack, body: Dict[str, Any]):
            """Handle tool approval button click."""
            await ack()
            approval_id = body["actions"][0]["value"]
            
            # Get approval details before handling response (which may trigger cleanup)
            approval_data = self.tool_approval_manager.get_pending_approval(approval_id)
            
            # Handle the approval response
            self.tool_approval_manager.handle_approval_response(approval_id, approved=True)
            
            # Get message details
            message = body["message"]
            message_ts = message.get("ts")
            channel_id = body["channel"]["id"]
            
            # Format informative approval message
            if approval_data:
                tool_name = approval_data.get("tool_name", "unknown")
                tool_input = approval_data.get("tool_input", {})
                
                approval_text = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    approved=True
                )
            else:
                approval_text = "✅ Tool approved. Executing..."
            
            # Update the original approval request message
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=[{
                    "type": "markdown",
                    "text": approval_text
                }]
            )
        
        @self.app.action("tool_deny")
        async def handle_tool_deny(ack, body: Dict[str, Any]):
            """Handle tool denial button click."""
            await ack()
            approval_id = body["actions"][0]["value"]
            
            # Get approval details before handling response (which may trigger cleanup)
            approval_data = self.tool_approval_manager.get_pending_approval(approval_id)
            
            # Handle the denial response
            self.tool_approval_manager.handle_approval_response(approval_id, approved=False)
            
            # Get message details
            message = body["message"]
            message_ts = message.get("ts")
            channel_id = body["channel"]["id"]
            
            # Format informative denial message
            if approval_data:
                tool_name = approval_data.get("tool_name", "unknown")
                tool_input = approval_data.get("tool_input", {})
                
                denial_text = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    approved=False
                )
            else:
                denial_text = "❌ Tool denied."
            
            # Update the original approval request message
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=[{
                    "type": "markdown",
                    "text": denial_text
                }]
            )
        
        @self.app.event("message")
        async def handle_message(event: Dict[str, Any]):
            """Handle regular messages (for debugging)."""
            # Only process if bot is mentioned
            if "bot_id" in event:
                return  # Ignore bot messages
        
        # Health check endpoint (for HTTP mode)
        # Note: This requires HTTP adapter, not Socket Mode
        # For Socket Mode, health checks aren't needed, but we'll add a simple check
        pass
    
    async def start_socket_mode(self):
        """Start the bot in Socket Mode."""
        handler = AsyncSocketModeHandler(self.app, os.getenv("SLACK_APP_TOKEN"))
        await handler.start_async()
    
    async def start_http(self, port: int = 3000):
        """Start the bot in HTTP mode (for production)."""
        # Note: Slack Bolt HTTP mode requires additional setup
        # For now, we'll use Socket Mode for simplicity
        # HTTP mode would require ngrok or public URL for Slack events
        raise NotImplementedError("HTTP mode not yet implemented. Use Socket Mode.")


async def main():
    """Main entry point."""
    bot = ButabotApp()
    
    # Check if we should use Socket Mode or HTTP
    use_socket_mode = os.getenv("SLACK_APP_TOKEN") is not None
    
    if use_socket_mode:
        print("Starting bot in Socket Mode...")
        await bot.start_socket_mode()
    else:
        print("Starting bot in HTTP Mode...")
        port = int(os.getenv("PORT", "3000"))
        await bot.start_http(port)


if __name__ == "__main__":
    asyncio.run(main())

