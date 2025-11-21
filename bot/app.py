"""Slack Bolt app for Butabot Agent."""

import asyncio
import json
import os
import re
import sys
from typing import Any, Callable, Dict

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

# No longer using Agent SDK - using Anthropic SDK directly

from .claude_client import ClaudeClient
from .session_manager import SessionManager
from .tool_approval import ToolApprovalManager
from .logger import (
    log_slack_event,
    log_agent_message,
    log_slack_api_call,
    log_info,
    log_error,
    log_warning,
)


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
    
    async def initialize_mcp_servers(self):
        """Discover MCP tools at startup (FastMCP manages connections per operation)."""
        log_info("Discovering MCP tools...")
        try:
            await self.claude_client.mcp_client.initialize()
            tool_count = len(self.claude_client.mcp_client.get_anthropic_tools())
            log_info(f"MCP tools discovered: {tool_count} tools available")
        except Exception as e:
            log_error(f"Error discovering MCP tools: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            # Don't fail startup - bot can still work without MCP tools
    
    async def shutdown(self):
        """Cleanup resources on shutdown (no-op since FastMCP manages connections)."""
        # FastMCP manages connection lifecycle via async with, so no explicit cleanup needed
        log_info("Shutdown complete")
    
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
                feedback_blocks = [{
                    "type": "markdown",
                    "text": feedback_message
                }]
                text_content = self._extract_text_from_blocks(feedback_blocks)
                log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=feedback")
                response = await say(
                    text=text_content,
                    blocks=feedback_blocks,
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False
                )
                if response and isinstance(response, dict):
                    response_ts = response.get("ts")
                    if response_ts:
                        log_slack_api_call(method="say", thread_ts=thread_ts, ts=response_ts, additional_info="type=feedback")
            except Exception as e:
                log_error(f"Error sending feedback message: {e}")
        
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
    
    def _format_assistant_message(self, message: Dict[str, Any]) -> list[Dict[str, Any]]:
        """
        Format assistant message for Slack.
        
        Args:
            message: Anthropic API response message dict
            
        Returns:
            List of Slack Block Kit blocks (markdown blocks)
        """
        lines = []
        content = message.get("content", [])
        
        for block in content:
            if block.get("type") == "text":
                text = block.get("text", "")
                is_error, formatted_text = self._detect_api_error(text)
                
                if is_error:
                    # For errors, show user-friendly message instead of raw error
                    lines.append(formatted_text)
                    log_warning(f"Detected API error in text block: {text[:200]}")
                else:
                    # Show full text content
                    lines.append(formatted_text)
            elif block.get("type") == "tool_use":
                # Tool use blocks are handled by approval workflow, skip formatting
                pass
        
        if not lines:
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
    
    def _extract_text_from_blocks(self, blocks: list[Dict[str, Any]]) -> str:
        """
        Extract plain text from Slack Block Kit blocks for use as fallback text.
        
        Args:
            blocks: List of Slack Block Kit block objects
            
        Returns:
            Plain text string extracted from blocks
        """
        text_parts = []
        for block in blocks:
            if isinstance(block, dict):
                block_type = block.get("type")
                # Handle markdown blocks
                if block_type == "markdown" and "text" in block:
                    text_parts.append(block["text"])
                # Handle section blocks with text
                elif block_type == "section" and "text" in block:
                    text_obj = block["text"]
                    if isinstance(text_obj, dict) and "text" in text_obj:
                        text_parts.append(text_obj["text"])
                # Handle context blocks
                elif block_type == "context" and "elements" in block:
                    for element in block["elements"]:
                        if isinstance(element, dict) and "text" in element:
                            text_parts.append(element["text"])
        
        # Join all text parts and strip markdown formatting for plain text
        combined_text = " ".join(text_parts)
        # Remove markdown formatting for plain text fallback
        combined_text = re.sub(r'\*\*(.*?)\*\*', r'\1', combined_text)  # Bold
        combined_text = re.sub(r'\*(.*?)\*', r'\1', combined_text)  # Italic
        combined_text = re.sub(r'`(.*?)`', r'\1', combined_text)  # Code
        combined_text = re.sub(r'```[\s\S]*?```', '', combined_text)  # Code blocks
        return combined_text.strip() if combined_text.strip() else "Message updated"
    
    def _register_handlers(self):
        """Register Slack event and action handlers."""
        
        @self.app.event("app_mention")
        async def handle_app_mention(event: Dict[str, Any], say, client):
            """Handle bot mentions."""
            try:
                # Log incoming Slack event
                event_ts = event.get("ts")
                log_slack_event(event_type="app_mention", event_ts=event_ts)
                
                # Extract event data
                channel_id = event.get("channel")
                thread_ts = event.get("thread_ts") or event.get("ts")  # Use thread_ts if in thread, else ts
                
                # Get bot user ID
                auth_response = await self.slack_client.auth_test()
                bot_user_id = auth_response.get("user_id", "")
                
                # Remove bot mention from message
                user_message = event.get("text", "")
                if f"<@{bot_user_id}>" in user_message:
                    user_message = user_message.replace(f"<@{bot_user_id}>", "").strip()
                
                if not user_message:
                    hello_blocks = [{
                        "type": "markdown",
                        "text": "Hello! How can I help you?"
                    }]
                    text_content = self._extract_text_from_blocks(hello_blocks)
                    log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=hello")
                    response = await say(
                        text=text_content,
                        blocks=hello_blocks,
                        thread_ts=thread_ts,
                        unfurl_links=False,
                        unfurl_media=False
                    )
                    if response and isinstance(response, dict):
                        response_ts = response.get("ts")
                        if response_ts:
                            log_slack_api_call(method="say", thread_ts=thread_ts, ts=response_ts, additional_info="type=hello")
                    return
                
                # Create feedback callback for this thread
                feedback_callback = self._create_feedback_callback(thread_ts, say)
                
                # Set feedback callback and channel_id for this thread on the main client
                self.claude_client.set_feedback_callback(thread_ts, feedback_callback)
                self.claude_client.set_channel_id(thread_ts, channel_id)
                
                # Send "thinking" message and store its timestamp for updates
                thinking_blocks = [{
                    "type": "markdown",
                    "text": "🤔 Thinking..."
                }]
                text_content = self._extract_text_from_blocks(thinking_blocks)
                log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=thinking")
                thinking_response = await say(
                    text=text_content,
                    blocks=thinking_blocks,
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False
                )
                thinking_ts = thinking_response.get("ts") if thinking_response else None
                if thinking_ts:
                    log_slack_api_call(method="say", thread_ts=thread_ts, ts=thinking_ts, additional_info="type=thinking")
                
                try:
                    response_sent = False
                    # Stream responses and process assistant messages
                    try:
                        async for message in self.claude_client.send_message(thread_ts, user_message):
                            # Handle assistant messages
                            if message.get("type") == "assistant":
                                content = message.get("content", [])
                                
                                # Log agent message with response type and block types
                                block_types = [block.get("type", "unknown") for block in content]
                                # Extract tool information if tool_use block is present
                                tool_name = None
                                tool_use_id = None
                                for block in content:
                                    if block.get("type") == "tool_use":
                                        tool_name = block.get("name")
                                        tool_use_id = block.get("id")
                                        break  # Use first tool_use block if multiple
                                log_agent_message(
                                    message_type="AssistantMessage",
                                    block_types=block_types,
                                    thread_ts=thread_ts,
                                    tool_name=tool_name,
                                    tool_use_id=tool_use_id
                                )
                                
                                # Check if message contains tool_use blocks
                                has_tool_use = any(
                                    block.get("type") == "tool_use"
                                    for block in content
                                )
                                
                                # Skip sending if message contains tool_use blocks (handled by approval workflow)
                                if has_tool_use:
                                    continue
                                
                                # Format and send the message
                                formatted_blocks = self._format_assistant_message(message)
                                
                                # If we have a thinking message timestamp, update it; otherwise send new message
                                if thinking_ts and not response_sent:
                                    # Update the thinking message with the actual response
                                    text_content = self._extract_text_from_blocks(formatted_blocks)
                                    log_slack_api_call(method="chat_update", thread_ts=thread_ts, ts=thinking_ts, additional_info="type=response")
                                    await self.slack_client.chat_update(
                                        channel=channel_id,
                                        ts=thinking_ts,
                                        text=text_content,
                                        blocks=formatted_blocks,
                                        thread_ts=thread_ts,
                                        unfurl_links=False,
                                        unfurl_media=False
                                    )
                                    response_sent = True
                                else:
                                    # Send as new message
                                    text_content = self._extract_text_from_blocks(formatted_blocks)
                                    log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=response")
                                    response = await say(
                                        text=text_content,
                                        blocks=formatted_blocks,
                                        thread_ts=thread_ts,
                                        unfurl_links=False,
                                        unfurl_media=False
                                    )
                                    if response and isinstance(response, dict):
                                        response_ts = response.get("ts")
                                        if response_ts:
                                            log_slack_api_call(method="say", thread_ts=thread_ts, ts=response_ts, additional_info="type=response")
                                    response_sent = True
                        
                    except StopAsyncIteration:
                        # Normal completion
                        pass
                    except Exception as iter_error:
                        log_error(f"Exception during send_message() iteration: {iter_error}")
                        import traceback
                        traceback.print_exc(file=sys.stderr)
                        sys.stderr.flush()
                        raise
                    
                    # If no response was sent (shouldn't happen, but handle gracefully)
                    if not response_sent and thinking_ts:
                        completion_blocks = [{
                            "type": "markdown",
                            "text": "✅ Processing complete. (No text response, but tools may have been executed.)"
                        }]
                        text_content = self._extract_text_from_blocks(completion_blocks)
                        log_slack_api_call(method="chat_update", thread_ts=thread_ts, ts=thinking_ts, additional_info="type=completion")
                        await self.slack_client.chat_update(
                            channel=channel_id,
                            ts=thinking_ts,
                            text=text_content,
                            blocks=completion_blocks,
                            thread_ts=thread_ts,
                            unfurl_links=False,
                            unfurl_media=False
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
                            text_content = self._extract_text_from_blocks(error_blocks)
                            log_slack_api_call(method="chat_update", thread_ts=thread_ts, ts=thinking_ts, additional_info="type=error")
                            await self.slack_client.chat_update(
                                channel=channel_id,
                                ts=thinking_ts,
                                text=text_content,
                                blocks=error_blocks,
                                thread_ts=thread_ts,
                                unfurl_links=False,
                                unfurl_media=False
                            )
                        except Exception:
                            # Fallback: send as new message
                            text_content = self._extract_text_from_blocks(error_blocks)
                            log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=error")
                            response = await say(
                                text=text_content,
                                blocks=error_blocks,
                                thread_ts=thread_ts,
                                unfurl_links=False,
                                unfurl_media=False
                            )
                            if response and isinstance(response, dict):
                                response_ts = response.get("ts")
                                if response_ts:
                                    log_slack_api_call(method="say", thread_ts=thread_ts, ts=response_ts, additional_info="type=error")
                    else:
                        text_content = self._extract_text_from_blocks(error_blocks)
                        log_slack_api_call(method="say", thread_ts=thread_ts, additional_info="type=error")
                        response = await say(
                            text=text_content,
                            blocks=error_blocks,
                            thread_ts=thread_ts,
                            unfurl_links=False,
                            unfurl_media=False
                        )
                        if response and isinstance(response, dict):
                            response_ts = response.get("ts")
                            if response_ts:
                                log_slack_api_call(method="say", thread_ts=thread_ts, ts=response_ts, additional_info="type=error")
                    
                    # Log error
                    log_error(f"Error handling message: {e}")
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                    sys.stderr.flush()
            except Exception as outer_e:
                # Catch any errors in the outer try block
                log_error(f"Outer error in handle_app_mention: {outer_e}")
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
            
            # Log Slack event with tool information
            message = body.get("message", {})
            event_ts = message.get("ts")
            thread_ts = message.get("thread_ts")
            tool_name = approval_data.get("tool_name", "unknown") if approval_data else "unknown"
            tool_use_id = approval_data.get("tool_use_id", "") if approval_data else ""
            additional_info = f"tool={tool_name}"
            if tool_use_id:
                additional_info += f" | tool_use_id={tool_use_id}"
            log_slack_event(event_type="tool_approve", event_ts=event_ts, thread_ts=thread_ts, additional_info=additional_info)
            
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
                
                approval_blocks = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    approved=True
                )
            else:
                approval_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "✅ Tool approved. Executing..."
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "_Executing tool..._"
                            }
                        ]
                    }
                ]
            
            # Update the original approval request message
            tool_name = approval_data.get('tool_name', 'unknown') if approval_data else 'unknown'
            tool_use_id = approval_data.get('tool_use_id', '') if approval_data else ''
            additional_info = f"type=approval | tool={tool_name}"
            if tool_use_id:
                additional_info += f" | tool_use_id={tool_use_id}"
            log_slack_api_call(method="chat_update", thread_ts=thread_ts, ts=message_ts, additional_info=additional_info)
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Tool approved: {tool_name}",
                blocks=approval_blocks,
                unfurl_links=False,
                unfurl_media=False
            )
        
        @self.app.action("tool_deny")
        async def handle_tool_deny(ack, body: Dict[str, Any]):
            """Handle tool denial button click."""
            await ack()
            approval_id = body["actions"][0]["value"]
            
            # Get approval details before handling response (which may trigger cleanup)
            approval_data = self.tool_approval_manager.get_pending_approval(approval_id)
            
            # Log Slack event with tool information
            message = body.get("message", {})
            event_ts = message.get("ts")
            thread_ts = message.get("thread_ts")
            tool_name = approval_data.get("tool_name", "unknown") if approval_data else "unknown"
            tool_use_id = approval_data.get("tool_use_id", "") if approval_data else ""
            additional_info = f"tool={tool_name}"
            if tool_use_id:
                additional_info += f" | tool_use_id={tool_use_id}"
            log_slack_event(event_type="tool_deny", event_ts=event_ts, thread_ts=thread_ts, additional_info=additional_info)
            
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
                
                denial_blocks = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    approved=False
                )
            else:
                denial_blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "❌ Tool denied."
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "_Tool execution cancelled._"
                            }
                        ]
                    }
                ]
            
            # Update the original approval request message
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Tool denied: {approval_data.get('tool_name', 'unknown') if approval_data else 'unknown'}",
                blocks=denial_blocks,
                unfurl_links=False,
                unfurl_media=False
            )
        
        @self.app.event("message")
        async def handle_message(event: Dict[str, Any]):
            """Handle regular messages (for debugging)."""
            # Log incoming Slack event
            event_ts = event.get("ts")
            log_slack_event(event_type="message", event_ts=event_ts)
            
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
    
    try:
        # Initialize MCP servers before starting Slack connection
        await bot.initialize_mcp_servers()
        
        # Check if we should use Socket Mode or HTTP
        use_socket_mode = os.getenv("SLACK_APP_TOKEN") is not None
        
        if use_socket_mode:
            print("Starting bot in Socket Mode...")
            await bot.start_socket_mode()
        else:
            print("Starting bot in HTTP Mode...")
            port = int(os.getenv("PORT", "3000"))
            await bot.start_http(port)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

