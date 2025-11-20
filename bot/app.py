"""Slack Bolt app for Butabot Agent."""

import asyncio
import os
from typing import Any, Dict

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from .claude_client import ClaudeClient
from .session_manager import SessionManager
from .tool_approval import ToolApprovalManager


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
        
        # Initialize Claude client with approval callback
        # Note: We'll create approval callbacks per channel when needed
        self.claude_client = ClaudeClient(
            session_manager=self.session_manager,
            tool_approval_callback=None,  # Will be set per message
        )
        
        # Register event handlers
        self._register_handlers()
    
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
            
            # Create approval callback for this channel
            approval_callback = await self.tool_approval_manager.create_approval_callback(channel_id)
            
            # Send "thinking" message
            thinking_msg = await say("🤔 Thinking...", thread_ts=thread_ts)
            
            try:
                # Create a temporary client with approval callback for this thread
                temp_client = ClaudeClient(
                    session_manager=self.session_manager,
                    tool_approval_callback=approval_callback,
                    tool_approval_manager=self.tool_approval_manager,
                )
                
                # Stream responses
                response_text = ""
                async for message in temp_client.send_message(thread_ts, user_message):
                    # Process different message types
                    from claude_agent_sdk import AssistantMessage, TextBlock
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                response_text += block.text + "\n"
                
                # Send new message with response instead of updating
                if response_text.strip():
                    await say(
                        response_text.strip(),
                        thread_ts=thread_ts
                    )
                else:
                    await say(
                        "I processed your request, but there's no text response.",
                        thread_ts=thread_ts
                    )
                
                # Clean up temp client
                await temp_client.disconnect_all()
                
            except Exception as e:
                error_msg = f"❌ Error: {str(e)}"
                await say(
                    error_msg,
                    thread_ts=thread_ts
                )
                print(f"Error handling message: {e}")
                import traceback
                traceback.print_exc()
        
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
                text_content = approval_data.get("text_content", "")
                
                approval_text = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    text_content=text_content,
                    approved=True
                )
            else:
                approval_text = "✅ Tool approved. Executing..."
            
            # Update the original approval request message
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=approval_text,
                blocks=[]  # Remove buttons
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
                text_content = approval_data.get("text_content", "")
                
                denial_text = self.tool_approval_manager.format_approval_message(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    text_content=text_content,
                    approved=False
                )
            else:
                denial_text = "❌ Tool denied."
            
            # Update the original approval request message
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=denial_text,
                blocks=[]  # Remove buttons
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

