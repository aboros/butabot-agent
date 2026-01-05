"""Abstract base class and dataclasses for platform connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class PlatformMessage:
    """Standardized message representation across platforms."""

    thread_id: str
    user_id: str
    content: str
    platform: str
    channel_id: str
    is_mention: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert PlatformMessage to dictionary."""
        return {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "content": self.content,
            "platform": self.platform,
            "channel_id": self.channel_id,
            "is_mention": self.is_mention,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlatformMessage":
        """Create PlatformMessage from dictionary."""
        return cls(
            thread_id=data["thread_id"],
            user_id=data["user_id"],
            content=data["content"],
            platform=data["platform"],
            channel_id=data["channel_id"],
            is_mention=data["is_mention"],
        )


class PlatformInterface(ABC):
    """Abstract base class for platform connectors."""

    @abstractmethod
    async def receive_message(self, event: Dict[str, Any]) -> Optional[PlatformMessage]:
        """
        Receive and normalize a message event from the platform.

        Args:
            event: Raw platform event dictionary

        Returns:
            Normalized PlatformMessage instance, or None if event should be ignored
        """
        pass

    @abstractmethod
    async def send_message(
        self,
        thread_id: str,
        content: str,
        msg_type: str = "text",
    ) -> None:
        """
        Send a message to the platform.

        Args:
            thread_id: Thread/conversation identifier
            content: Message content to send
            msg_type: Type of message (e.g., "text", "markdown")
        """
        pass

    @abstractmethod
    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        params: Dict[str, Any],
        timeout: float = 300.0,
    ) -> bool:
        """
        Request user approval for a tool execution.

        Args:
            thread_id: Thread/conversation identifier
            tool_name: Name of the tool requesting approval
            params: Tool parameters/input
            timeout: Approval timeout in seconds (default: 300)

        Returns:
            True if approved, False if denied or timed out
        """
        pass

    @abstractmethod
    async def handle_button_click(self, payload: Dict[str, Any]) -> None:
        """
        Handle button/interaction clicks (e.g., approval buttons).

        Args:
            payload: Platform-specific interaction payload
        """
        pass

