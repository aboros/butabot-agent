"""Slack connector implementing PlatformInterface."""

import json
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from .interface import PlatformInterface, PlatformMessage

# Load environment variables
load_dotenv()


class SlackConnector(PlatformInterface):
    """Slack platform connector implementing PlatformInterface."""

    def __init__(self, bot_token: Optional[str] = None, app_token: Optional[str] = None):
        """
        Initialize SlackConnector.

        Args:
            bot_token: Slack bot token (defaults to SLACK_BOT_TOKEN env var)
            app_token: Slack app token for Socket Mode (defaults to SLACK_APP_TOKEN env var)
        """
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN")
        self.app_token = app_token or os.getenv("SLACK_APP_TOKEN")

        if not self.bot_token:
            raise ValueError("SLACK_BOT_TOKEN environment variable or bot_token parameter required")

        # Initialize Slack app
        self.app = AsyncApp(token=self.bot_token)
        self.client = AsyncWebClient(token=self.bot_token)
        self.handler: Optional[AsyncSocketModeHandler] = None

        # Message handler will be set externally
        self._message_handler: Optional[Any] = None
        # Keep _agent for backward compatibility (deprecated)
        self._agent: Optional[Any] = None
        
        # Track channel_id per thread_id for message routing
        self._thread_channels: Dict[str, str] = {}

    def set_message_handler(self, message_handler: Any) -> None:
        """Set the message handler instance for processing messages."""
        self._message_handler = message_handler

    def set_agent(self, agent: Any) -> None:
        """Set the agent instance for processing messages (deprecated - use set_message_handler)."""
        self._agent = agent

    def _normalize_slack_message(self, event: Dict[str, Any]) -> PlatformMessage:
        """
        Normalize Slack event to PlatformMessage.

        Args:
            event: Slack event dictionary

        Returns:
            PlatformMessage instance
        """
        # Use event['ts'] as thread_id, or thread_ts if in a thread
        thread_id = event.get("thread_ts") or event.get("ts", "")
        user_id = event.get("user", "")
        content = event.get("text", "")
        channel_id = event.get("channel", "")

        # Check if this is a mention (has bot mention in text)
        text = event.get("text", "")
        is_mention = "<@" in text  # Simple check - can be refined

        return PlatformMessage(
            thread_id=thread_id,
            user_id=user_id,
            content=content,
            platform="slack",
            channel_id=channel_id,
            is_mention=is_mention,
        )

    async def receive_message(self, event: Dict[str, Any]) -> Optional[PlatformMessage]:
        """
        Receive and normalize a message event from Slack.

        Args:
            event: Raw Slack event dictionary

        Returns:
            Normalized PlatformMessage instance, or None if event should be ignored
        """
        # Filter out bot messages
        if "bot_id" in event:
            return None

        # Only process messages with text
        if not event.get("text"):
            return None

        return self._normalize_slack_message(event)

    async def send_message(
        self,
        thread_id: str,
        content: str,
        msg_type: str = "text",
    ) -> None:
        """
        Send a message to Slack.

        Args:
            thread_id: Thread timestamp (ts) to reply in thread
            content: Message content to send
            msg_type: Type of message (e.g., "text", "markdown")
        """
        # Get channel_id from thread tracking
        channel_id = self._thread_channels.get(thread_id)
        if not channel_id:
            # If thread_id is not in tracking, try using it as channel_id (for new threads)
            channel_id = thread_id
        
        await self._send_message_to_channel(channel_id, thread_id, content)

    async def _send_message_to_channel(
        self, channel_id: str, thread_id: str, content: str, blocks: Optional[list] = None
    ) -> None:
        """Internal helper to send message to a specific channel."""
        await self.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_id if thread_id != channel_id else None,
            text=content,
            blocks=blocks,
            unfurl_links=False,
            unfurl_media=False,
        )

    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        params: Dict[str, Any],
        timeout: float = 300.0,
    ) -> bool:
        """
        Request user approval for a tool execution via Slack buttons.

        Args:
            thread_id: Thread/conversation identifier
            tool_name: Name of the tool requesting approval
            params: Tool parameters/input
            timeout: Approval timeout in seconds (default: 300)

        Returns:
            True if approved, False if denied or timed out
        """
        # This method should post ephemeral message with approve/deny buttons
        # For now, return False as placeholder
        # Full implementation requires approval tracker integration
        return False

    async def handle_button_click(self, payload: Dict[str, Any]) -> None:
        """
        Handle button/interaction clicks (e.g., approval buttons).

        Args:
            payload: Slack interaction payload
        """
        # Extract approval_id from payload and resolve via approval tracker
        # For now, this is a placeholder
        pass

    def _register_handlers(self) -> None:
        """Register Slack event and action handlers."""
        from bot.factoids import FactoidManager
        
        factoid_manager = FactoidManager()
        
        @self.app.event("app_mention")
        async def handle_app_mention(event: Dict[str, Any], say, client):
            """Handle app mention events."""
            # Normalize message
            platform_message = self._normalize_slack_message(event)
            
            # Track channel_id for this thread
            self._thread_channels[platform_message.thread_id] = platform_message.channel_id
            
            # Check for factoids first
            factoid_response = factoid_manager.check_factoid(
                platform_message.content,
                platform_message.is_mention
            )
            if factoid_response:
                await say(text=factoid_response, thread_ts=platform_message.thread_id)
                return
            
            # Process message through message handler if available
            if self._message_handler:
                try:
                    await self._message_handler.handle_message(platform_message)
                except Exception as e:
                    # Log error and send user-friendly message
                    import logging
                    logging.error(f"Error processing message: {e}", exc_info=True)
                    await say(
                        text=f"Sorry, I encountered an error processing your message: {str(e)}",
                        thread_ts=platform_message.thread_id
                    )

    async def start(self) -> None:
        """Start the Slack connector (Socket Mode)."""
        if not self.app_token:
            raise ValueError("SLACK_APP_TOKEN required for Socket Mode")

        self.handler = AsyncSocketModeHandler(self.app, self.app_token)
        await self.handler.start_async()

    async def stop(self) -> None:
        """Stop the Slack connector."""
        if self.handler:
            await self.handler.close_async()

