"""Tests for ConversationDispatch."""

import asyncio
import unittest

from bot.conversation_dispatch import ConversationDispatch
from bot.connectors.interface import IncomingMessage


def _msg(tid: str, content: str) -> IncomingMessage:
    return IncomingMessage(
        thread_id=tid,
        channel_id="c",
        user_id="u",
        content=content,
        platform="discord",
        source_message_id=content,
    )


class TestConversationDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_same_thread_fifo_order(self) -> None:
        contents: list[str] = []

        async def process(m: IncomingMessage) -> None:
            contents.append(m.content)

        d = ConversationDispatch(process, max_concurrent=4)
        await d.submit(_msg("A", "first"))
        await d.submit(_msg("A", "second"))
        await asyncio.sleep(0.3)
        await d.shutdown()
        self.assertEqual(contents, ["first", "second"])

    async def test_different_threads_can_overlap(self) -> None:
        active = 0
        max_active = [0]
        lock = asyncio.Lock()

        async def process(m: IncomingMessage) -> None:
            nonlocal active
            async with lock:
                active += 1
                max_active[0] = max(max_active[0], active)
            await asyncio.sleep(0.05)
            async with lock:
                active -= 1

        d = ConversationDispatch(process, max_concurrent=4)
        await asyncio.gather(
            d.submit(_msg("T1", "a")),
            d.submit(_msg("T2", "b")),
        )
        await asyncio.sleep(0.2)
        await d.shutdown()
        self.assertGreaterEqual(max_active[0], 2)


if __name__ == "__main__":
    unittest.main()
