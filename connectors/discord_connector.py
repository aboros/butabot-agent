"""Discord connector implementing PlatformInterface."""

import os
from typing import Any, Dict, Optional

import nextcord
from nextcord import Intents
from nextcord.ext import commands
from dotenv import load_dotenv

from .interface import PlatformInterface, PlatformMessage

# Load environment variables
load_dotenv()


class DiscordConnector(PlatformInterface):
    """Discord platform connector implementing PlatformInterface."""

    def __init__(self, bot_token: Optional[str] = None):
        """
        Initialize DiscordConnector.

        Args:
            bot_token: Discord bot token (defaults to DISCORD_TOKEN env var)
        """
        self.bot_token = bot_token or os.getenv("DISCORD_TOKEN")

        if not self.bot_token:
            raise ValueError("DISCORD_TOKEN environment variable or bot_token parameter required")

        # Configure intents
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        # Initialize bot
        self.bot = commands.Bot(intents=intents)

        # Message handler will be set externally
        self._message_handler: Optional[Any] = None
        # Keep _agent for backward compatibility (deprecated)
        self._agent: Optional[Any] = None

        # Register event handlers
        self._register_handlers()

    def set_message_handler(self, message_handler: Any) -> None:
        """Set the message handler instance for processing messages."""
        self._message_handler = message_handler

    def set_agent(self, agent: Any) -> None:
        """Set the agent instance for processing messages (deprecated - use set_message_handler)."""
        self._agent = agent

    def _normalize_discord_message(self, message: nextcord.Message) -> PlatformMessage:
        """
        Normalize Discord message to PlatformMessage.

        Args:
            message: Discord Message object

        Returns:
            PlatformMessage instance
        """
        # Use message.id as thread_id for threaded messages, or channel.id for regular messages
        thread_id = str(message.id) if hasattr(message, "thread") and message.thread else str(message.channel.id)
        user_id = str(message.author.id)
        content = message.content
        channel_id = str(message.channel.id)

        # Check if bot is mentioned
        is_mention = self.bot.user and self.bot.user in message.mentions

        return PlatformMessage(
            thread_id=thread_id,
            user_id=user_id,
            content=content,
            platform="discord",
            channel_id=channel_id,
            is_mention=is_mention,
        )

    async def receive_message(self, event: Dict[str, Any]) -> Optional[PlatformMessage]:
        """
        Receive and normalize a message event from Discord.

        Args:
            event: Raw Discord event dictionary (Message object)

        Returns:
            Normalized PlatformMessage instance, or None if event should be ignored
        """
        # For Discord, event is typically the Message object directly
        if isinstance(event, nextcord.Message):
            message = event
        elif "message" in event:
            message = event["message"]
        else:
            return None

        # Filter out bot messages
        if message.author.bot:
            return None

        # Only process messages with content
        if not message.content:
            return None

        return self._normalize_discord_message(message)

    async def send_message(
        self,
        thread_id: str,
        content: str,
        msg_type: str = "text",
    ) -> None:
        """
        Send a message to Discord.

        Args:
            thread_id: Thread/channel ID to send message to
            content: Message content to send
            msg_type: Type of message (e.g., "text", "markdown")
        """
        # Get channel by ID
        channel = self.bot.get_channel(int(thread_id))
        if not channel:
            raise ValueError(f"Channel {thread_id} not found")

        # Send message
        await channel.send(content)

    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        params: Dict[str, Any],
        timeout: float = 300.0,
    ) -> bool:
        """
        Request user approval for a tool execution via Discord buttons.

        Args:
            thread_id: Thread/conversation identifier
            tool_name: Name of the tool requesting approval
            params: Tool parameters/input
            timeout: Approval timeout in seconds (default: 300)

        Returns:
            True if approved, False if denied or timed out
        """
        # This method should send a message with View containing Approve/Deny buttons
        # For now, return False as placeholder
        # Full implementation requires approval tracker integration
        return False

    async def handle_button_click(self, payload: Dict[str, Any]) -> None:
        """
        Handle button/interaction clicks (e.g., approval buttons).

        Args:
            payload: Discord interaction payload
        """
        # Extract approval_id from payload and resolve via approval tracker
        # For now, this is a placeholder
        pass

    def _register_handlers(self) -> None:
        """Register Discord event handlers."""
        
        @self.bot.event
        async def on_ready():
            """Handle bot ready event."""
            print(f"Discord bot logged in as {self.bot.user}")

        @self.bot.event
        async def on_message(message: nextcord.Message):
            """Handle incoming messages."""
            # Don't process bot messages
            if message.author.bot:
                return

            # Normalize message
            platform_message = self._normalize_discord_message(message)

            # Check for factoids first
            from bot.factoids import FactoidManager
            factoid_manager = FactoidManager()
            factoid_response = factoid_manager.check_factoid(
                platform_message.content,
                platform_message.is_mention
            )
            if factoid_response:
                await message.channel.send(factoid_response)
                return

            # Process message through message handler if available
            if self._message_handler and platform_message.is_mention:
                try:
                    await self._message_handler.handle_message(platform_message)
                except Exception as e:
                    # Log error and send user-friendly message
                    import logging
                    logging.error(f"Error processing message: {e}", exc_info=True)
                    await message.channel.send(
                        f"Sorry, I encountered an error processing your message: {str(e)}"
                    )

            # Process commands
            await self.bot.process_commands(message)

    async def start(self) -> None:
        """Start the Discord connector."""
        if not self.bot_token:
            raise ValueError("DISCORD_TOKEN required")
        
        await self.bot.start(self.bot_token)

    async def stop(self) -> None:
        """Stop the Discord connector."""
        await self.bot.close()

