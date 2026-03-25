"""Abstract base class for platform connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict


@dataclass
class IncomingMessage:
    """Normalized incoming message from any platform."""

    thread_id: str
    channel_id: str
    user_id: str
    content: str
    platform: str


class PlatformConnector(ABC):
    """
    Abstract base class for platform connectors.

    Each platform (Slack, Discord, …) implements this interface so the
    orchestrator and agent layer remain platform-agnostic.  A single
    instance is wired to exactly one platform at startup.
    """

    def set_message_handler(
        self,
        handler: Callable[["IncomingMessage"], Coroutine[Any, Any, None]],
    ) -> None:
        """Register the async callable invoked when a user message arrives."""
        self._message_handler = handler

    @abstractmethod
    async def send_message(self, thread_id: str, content: str) -> None:
        """
        Send a text message back to the user.

        Implementations may apply platform-specific formatting (e.g. Slack
        Block Kit markdown blocks) and should handle the update-in-place
        pattern (e.g. replacing a "Thinking…" placeholder) internally.
        """

    @abstractmethod
    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> bool:
        """
        Post an approval prompt to the user and wait for their decision.

        Returns True if the user approved, False if denied or timed out.
        All platform-specific UI (buttons, embeds, …) is handled here.
        """

    @abstractmethod
    async def on_tool_result(
        self, tool_use_id: str, tool_result: Any, is_error: bool
    ) -> None:
        """
        Called after a tool finishes executing.

        Implementations use this to update the approval message in-place
        (e.g. replace "Executing…" with "Results received.").
        """

    @abstractmethod
    async def start(self) -> None:
        """Start listening for incoming messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down the connector gracefully."""
