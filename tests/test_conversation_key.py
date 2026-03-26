"""Tests for conversation key helpers."""

import os
import unittest

from bot import conversation_key as ck


class TestConversationKey(unittest.TestCase):
    def test_sanitize(self) -> None:
        self.assertEqual(ck.sanitize_conversation_key("a:b/c"), "a_b_c")

    def test_discord_top_level_modes(self) -> None:
        self.assertEqual(
            ck.build_discord_conversation_key(
                channel_id="ch",
                user_id="u",
                message_id=99,
                in_thread=False,
                discord_thread_id=None,
            ),
            "99",
        )
        os.environ["DISCORD_TOP_LEVEL_KEY"] = "channel"
        try:
            self.assertEqual(
                ck.build_discord_conversation_key(
                    channel_id="ch",
                    user_id="u",
                    message_id=99,
                    in_thread=False,
                    discord_thread_id=None,
                ),
                "ch",
            )
        finally:
            del os.environ["DISCORD_TOP_LEVEL_KEY"]

    def test_discord_in_thread_uses_thread_id(self) -> None:
        self.assertEqual(
            ck.build_discord_conversation_key(
                channel_id="ch",
                user_id="u",
                message_id=99,
                in_thread=True,
                discord_thread_id=555,
            ),
            "555",
        )

    def test_slack_modes(self) -> None:
        k, ts = ck.build_slack_conversation_key(
            thread_ts="ts1", channel_id="C1", user_id="U1"
        )
        self.assertEqual(k, "ts1")
        self.assertEqual(ts, "ts1")
        os.environ["SLACK_KEY"] = "channel"
        try:
            k2, ts2 = ck.build_slack_conversation_key(
                thread_ts="ts1", channel_id="C1", user_id="U1"
            )
            self.assertEqual(k2, "C1")
            self.assertEqual(ts2, "ts1")
        finally:
            del os.environ["SLACK_KEY"]

    def test_thinking_map_key(self) -> None:
        self.assertEqual(ck.thinking_map_key("t", None), "t")
        self.assertEqual(ck.thinking_map_key("t", "s"), "t:s")


if __name__ == "__main__":
    unittest.main()
