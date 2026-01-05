"""Core message handler for centralized agent communication."""

import logging
from typing import Dict, Optional

from connectors.interface import PlatformInterface, PlatformMessage
from agent.tool_agent import ChatPlatformToolAgent

logger = logging.getLogger(__name__)


class MessageHandler:
    """
    Central handler for all agent communication.
    
    Receives normalized PlatformMessages from connectors, processes them through
    the agent, and routes responses back through the appropriate connector.
    """

    def __init__(self, agent: ChatPlatformToolAgent):
        """
        Initialize MessageHandler.

        Args:
            agent: ChatPlatformToolAgent instance for processing messages
        """
        self.agent = agent
        self.connectors: Dict[str, PlatformInterface] = {}

    def register_connector(self, platform_name: str, connector: PlatformInterface) -> None:
        """
        Register a connector for a specific platform.

        Args:
            platform_name: Name of the platform (e.g., "slack", "discord")
            connector: PlatformInterface instance for the platform
        """
        self.connectors[platform_name] = connector
        logger.info(f"Registered connector for platform: {platform_name}")

    async def handle_message(self, message: PlatformMessage) -> str:
        """
        Central method for processing all incoming messages.

        This method:
        1. Sets thread context on agent
        2. Calls agent.send() with message content
        3. Routes response back through appropriate connector

        Args:
            message: Normalized PlatformMessage from connector

        Returns:
            Agent response text

        Raises:
            ValueError: If platform is not registered
            Exception: If agent processing or response routing fails
        """
        # Set thread context (for maintaining conversation state)
        self.agent.set_thread_id(message.thread_id)

        # Process message with agent
        try:
            response = await self.agent.send(message.content)
        except Exception as e:
            logger.error(
                f"Error processing message from {message.platform} "
                f"(thread: {message.thread_id}): {e}",
                exc_info=True
            )
            raise

        # Route response back through the originating connector
        if message.platform not in self.connectors:
            error_msg = f"No connector registered for platform: {message.platform}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        try:
            connector = self.connectors[message.platform]
            await connector.send_message(
                thread_id=message.thread_id,
                content=response,
                msg_type="response",
            )
        except Exception as e:
            logger.error(
                f"Error routing response to {message.platform} "
                f"(thread: {message.thread_id}): {e}",
                exc_info=True
            )
            # Don't raise - we've already processed the message, just log the error
            # The response was generated, even if we couldn't send it back

        return response

