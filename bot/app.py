"""Slack Bolt app for Butabot Agent."""

import asyncio
import json
import os
from typing import Any, Callable, Dict

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock

from .claude_client import ClaudeClient
from .session_manager import SessionManager


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
        
        # Initialize Claude client (feedback callback will be set per message)
        self.claude_client = ClaudeClient(
            session_manager=self.session_manager,
            feedback_callback=None,  # Will be set per message
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
                await say(feedback_message, thread_ts=thread_ts)
            except Exception as e:
                print(f"Error sending feedback message: {e}")
        
        return feedback_callback
    
    def _format_assistant_message(self, message: AssistantMessage) -> str:
        """
        Format AssistantMessage with detailed information about its content.
        
        Args:
            message: AssistantMessage instance
            
        Returns:
            Formatted string with message details
        """
        lines = [f"📝 *AssistantMessage*"]
        
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
            lines.append(f"├─ TextBlock ({len(text_blocks)}):")
            for i, block in enumerate(text_blocks, 1):
                # Show full text content (no truncation for text blocks)
                # Replace newlines with spaces for cleaner display in Slack
                text_content = block.text.replace(chr(10), ' ')
                lines.append(f"│  {i}. {text_content}")
        
        if tool_use_blocks:
            lines.append(f"├─ ToolUseBlock ({len(tool_use_blocks)}):")
            for i, block in enumerate(tool_use_blocks, 1):
                tool_input_str = self._format_tool_input(block.input)
                lines.append(f"│  {i}. `{block.name}`")
                lines.append(f"│     Input: ```{tool_input_str}```")
        
        if tool_result_blocks:
            lines.append(f"├─ ToolResultBlock ({len(tool_result_blocks)}):")
            for i, block in enumerate(tool_result_blocks, 1):
                status = "❌ Error" if block.is_error else "✅ Success"
                result_preview = self._format_tool_result_preview(block.content)
                lines.append(f"│  {i}. {status}")
                lines.append(f"│     Result: {result_preview}")
        
        if not text_blocks and not tool_use_blocks and not tool_result_blocks:
            lines.append("└─ (empty message)")
        
        return "\n".join(lines)
    
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
                await say("Hello! How can I help you?", thread_ts=thread_ts)
                return
            
            # Create feedback callback for this thread
            feedback_callback = self._create_feedback_callback(thread_ts, say)
            
            # Set feedback callback for this thread on the main client
            self.claude_client.set_feedback_callback(thread_ts, feedback_callback)
            
            # Send "thinking" message
            await say("🤔 Thinking...", thread_ts=thread_ts)
            
            try:
                # Stream responses and process every AssistantMessage
                async for message in self.claude_client.send_message(thread_ts, user_message):
                    # Always say() for every AssistantMessage with detailed info
                    if isinstance(message, AssistantMessage):
                        formatted_message = self._format_assistant_message(message)
                        await say(formatted_message, thread_ts=thread_ts)
                
            except Exception as e:
                error_msg = f"❌ Error: {str(e)}"
                await say(
                    error_msg,
                    thread_ts=thread_ts
                )
                print(f"Error handling message: {e}")
                import traceback
                traceback.print_exc()
        
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

