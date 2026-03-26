"""Build stable conversation keys (thread_id) from platform events."""

from __future__ import annotations

import os
import re
from typing import Literal, Optional, Tuple

DiscordTopLevelKey = Literal["per_message", "channel", "channel_user"]
SlackKey = Literal["thread_ts", "channel", "channel_user"]


def sanitize_conversation_key(key: str, max_len: int = 200) -> str:
    """Sanitize a key for persistence filenames."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", key)
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len]
    return sanitized


def _get_discord_top_level_key() -> DiscordTopLevelKey:
    v = os.getenv("DISCORD_TOP_LEVEL_KEY", "per_message").lower().strip()
    if v in ("per_message", "channel", "channel_user"):
        return v  # type: ignore[return-value]
    return "per_message"


def _get_slack_key() -> SlackKey:
    v = os.getenv("SLACK_KEY", "thread_ts").lower().strip()
    if v in ("thread_ts", "channel", "channel_user"):
        return v  # type: ignore[return-value]
    return "thread_ts"


def build_discord_conversation_key(
    *,
    channel_id: str,
    user_id: str,
    message_id: int,
    in_thread: bool,
    discord_thread_id: Optional[int],
) -> str:
    """
    Compute session thread_id for Discord.

    Inside a Discord thread, always use the thread's snowflake id.
    Top-level behavior is controlled by DISCORD_TOP_LEVEL_KEY.
    """
    if in_thread and discord_thread_id is not None:
        return str(discord_thread_id)

    mode = _get_discord_top_level_key()
    if mode == "per_message":
        return str(message_id)
    if mode == "channel":
        return str(channel_id)
    return f"{channel_id}:{user_id}"


def thinking_map_key(thread_id: str, source_message_id: Optional[str]) -> str:
    """Key for thinking placeholder maps when multiple messages share a thread_id."""
    if source_message_id:
        return f"{thread_id}:{source_message_id}"
    return thread_id


def build_slack_conversation_key(
    *,
    thread_ts: str,
    channel_id: str,
    user_id: str,
) -> Tuple[str, str]:
    """
    Compute session thread_id for Slack and return (conversation_key, slack_thread_ts).

    slack_thread_ts is always the API thread_ts for posting (thread_ts or root message ts).
    """
    mode = _get_slack_key()
    slack_thread_ts = thread_ts
    if mode == "thread_ts":
        return thread_ts, slack_thread_ts
    if mode == "channel":
        return channel_id, slack_thread_ts
    return f"{channel_id}:{user_id}", slack_thread_ts
