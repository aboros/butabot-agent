"""Abstract base class for platform connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, Optional


@dataclass
class IncomingMessage:
    """Normalized incoming message from any platform."""

    thread_id: str
    channel_id: str
    user_id: str
    content: str
    platform: str
    #: Incoming user message id (Discord snowflake or Slack message ts) for thinking placeholder correlation.
    source_message_id: Optional[str] = None
    #: Slack API thread_ts when it differs from thread_id (e.g. channel-scoped conversation keys).
    slack_thread_ts: Optional[str] = None


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
    async def send_message(
        self,
        thread_id: str,
        content: str,
        *,
        source_message_id: Optional[str] = None,
        replace_thinking_placeholder: bool = True,
        tool_use_id: Optional[str] = None,
        release_thinking_placeholder: bool = False,
    ) -> None:
        """
        Send a text message back to the user.

        Implementations may apply platform-specific formatting (e.g. Slack
        Block Kit markdown blocks). source_message_id selects which Thinking
        placeholder is tied to this send when multiple user messages share a
        thread_id.

        - replace_thinking_placeholder True (default): replace the Thinking
          message in-place with this content (legacy).
        - release_thinking_placeholder True: stop tracking the Thinking message
          without editing it in the channel, then post this content as new
          message(s). Use for final replies so "Thinking…" stays visible.
        - replace_thinking_placeholder False and release_thinking_placeholder
          False: post a new message and leave Thinking unchanged (e.g. tool
          status). If tool_use_id is set, remember that message for
          on_tool_result.

        release_thinking_placeholder takes precedence over replace when both
        are set.
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
        self,
        tool_use_id: str,
        tool_result: Any,
        is_error: bool,
        *,
        tool_name: Optional[str] = None,
    ) -> None:
        """
        Called after a tool finishes executing.

        Implementations update the approval prompt in-place when tool approval
        is enabled, or the ephemeral "Using …" status message when approval is
        disabled and that message was recorded for tool_use_id.
        """

    @abstractmethod
    async def start(self) -> None:
        """Start listening for incoming messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down the connector gracefully."""
