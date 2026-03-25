"""Main entry point — wires a platform connector to the Claude client."""

import asyncio
import os
import sys

from dotenv import load_dotenv

from .session_manager import SessionManager
from .claude_client import ClaudeClient
from .connectors.interface import IncomingMessage
from .logger import log_error, log_info

load_dotenv()


async def main() -> None:
    """
    Start the bot.

    The platform is selected via the PLATFORM environment variable
    (default: "slack").  Adding support for a new platform means
    implementing PlatformConnector and adding a branch here.
    """
    platform = os.getenv("PLATFORM", "slack").lower()
    log_info(f"Starting Butabot on platform: {platform}")

    if platform == "slack":
        from .connectors.slack_connector import SlackConnector
        connector = SlackConnector()
    elif platform == "discord":
        from .connectors.discord_connector import DiscordConnector
        connector = DiscordConnector()
    else:
        raise ValueError(
            f"Unknown platform: {platform!r}. "
            "Set the PLATFORM environment variable to a supported value ('slack' or 'discord')."
        )

    session_manager = SessionManager()
    claude_client = ClaudeClient(
        session_manager=session_manager,
        connector=connector,
    )

    async def handle_message(message: IncomingMessage) -> None:
        try:
            response = await claude_client.get_text_response(
                message.thread_id, message.content
            )
            if not response:
                response = "✅ Done. (No text response — tools may have been executed.)"
            await connector.send_message(message.thread_id, response)
        except Exception as e:
            log_error(f"Error handling message for thread {message.thread_id}: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            await connector.send_message(message.thread_id, f"❌ *Error:* {e}")

    connector.set_message_handler(handle_message)
    log_info("Bot initialized. Starting connector...")
    await connector.start()


if __name__ == "__main__":
    asyncio.run(main())
