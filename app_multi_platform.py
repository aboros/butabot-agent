"""Multi-platform bot application entry point."""

import asyncio
from pathlib import Path
from typing import Optional

from config.loader import load_config, load_secrets
from connectors.discord_connector import DiscordConnector
from connectors.slack_connector import SlackConnector
from agent.agent_config import create_agent_config, create_agent_from_config
from agent.tool_agent import ChatPlatformToolAgent
from approval.approval_tracker import ApprovalTracker
from approval.rules_manager import ApprovalRulesManager
from bot.factoids import FactoidManager
from core.message_handler import MessageHandler


async def main():
    """Main entry point for multi-platform bot."""
    # Load configuration
    config_dir = Path("config")
    config = load_config(config_dir)
    secrets = load_secrets(config_dir)

    # Initialize approval system
    rules_manager = ApprovalRulesManager(config_dir)
    approval_tracker = ApprovalTracker()

    # Initialize factoid manager
    factoid_manager = FactoidManager()

    # Initialize connectors
    slack_connector = SlackConnector()
    # For testing, only start Slack connector
    # discord_connector = DiscordConnector()

    # Create agent configuration and instance
    agent_context = None
    try:
        agent_config = create_agent_config(config_dir)
        agent_context = await create_agent_from_config(agent_config, config_dir)
        
        # Use async context manager to keep agent alive
        async with agent_context as agent_instance:
            # Create ChatPlatformToolAgent wrapper
            # Note: The agent's platform_interface is used for approval requests and status messages
            # For now, we use slack_connector as the platform interface
            agent = ChatPlatformToolAgent(
                platform_interface=slack_connector,
                rules_manager=rules_manager,
                approval_tracker=approval_tracker,
                agent_instance=agent_instance,
            )

            # Initialize MessageHandler
            message_handler = MessageHandler(agent)
            
            # Register connectors with message handler
            message_handler.register_connector("slack", slack_connector)
            # message_handler.register_connector("discord", discord_connector)
            
            # Set message handler on connectors
            slack_connector.set_message_handler(message_handler)
            # discord_connector.set_message_handler(message_handler)
            
            # Start connectors (agent stays alive in context manager)
            try:
                # For testing, only start Slack connector
                await slack_connector.start()
                # await asyncio.gather(
                #     slack_connector.start(),
                #     discord_connector.start(),
                # )
            except KeyboardInterrupt:
                print("\nShutting down...")
            finally:
                await slack_connector.stop()
                # await discord_connector.stop()
        
    except ImportError as e:
        print(f"Warning: {e}")
        print("Agent functionality will not be available. Only factoids will work.")
        # Start connectors without agent
        try:
            await slack_connector.start()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            await slack_connector.stop()
    except Exception as e:
        print(f"Error creating agent: {e}")
        import traceback
        traceback.print_exc()
        # Start connectors without agent
        try:
            await slack_connector.start()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            await slack_connector.stop()


if __name__ == "__main__":
    asyncio.run(main())

