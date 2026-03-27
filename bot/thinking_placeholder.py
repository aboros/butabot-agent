"""Line shown while the agent is working (Slack / Discord Thinking message)."""

import os


def get_thinking_placeholder() -> str:
    """
    Text from THINKING_PLACEHOLDER, or a default when unset/empty.

    Set in .env (see .env.example). Restart the process after changing.
    """
    v = os.getenv("THINKING_PLACEHOLDER", "").strip()
    if v:
        return v
    return "🤔 Thinking..."
