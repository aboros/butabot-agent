"""Platform connectors for multi-platform bot support."""

from .discord_connector import DiscordConnector
from .interface import PlatformInterface, PlatformMessage
from .slack_connector import SlackConnector

__all__ = ["PlatformInterface", "PlatformMessage", "SlackConnector", "DiscordConnector"]

