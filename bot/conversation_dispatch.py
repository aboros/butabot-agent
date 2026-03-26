"""Per-conversation-key FIFO dispatch with optional global concurrency cap."""

from __future__ import annotations

import asyncio
import traceback
from typing import Awaitable, Callable, Dict

from .connectors.interface import IncomingMessage
from .logger import log_error, log_info

ProcessFn = Callable[[IncomingMessage], Awaitable[None]]


class ConversationDispatch:
    """
    One asyncio.Queue per thread_id (conversation key), each drained by a
    dedicated worker task. A global semaphore limits concurrent agent turns
    across different keys.
    """

    def __init__(
        self,
        process: ProcessFn,
        *,
        max_concurrent: int = 8,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._process = process
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queues: Dict[str, asyncio.Queue[IncomingMessage]] = {}
        self._workers: Dict[str, asyncio.Task[None]] = {}
        self._registry_lock = asyncio.Lock()

    async def submit(self, incoming: IncomingMessage) -> None:
        """Enqueue a message; starts a worker for this thread_id if needed."""
        tid = incoming.thread_id
        async with self._registry_lock:
            if tid not in self._queues:
                self._queues[tid] = asyncio.Queue()
            queue = self._queues[tid]
            if tid not in self._workers:
                self._workers[tid] = asyncio.create_task(
                    self._worker_loop(tid, queue),
                    name=f"conv-worker-{tid[:32]}",
                )
                log_info(f"[Dispatch] Started worker for thread_id={tid[:48]}...")
        await queue.put(incoming)

    async def _worker_loop(self, thread_id: str, queue: asyncio.Queue[IncomingMessage]) -> None:
        while True:
            try:
                msg = await queue.get()
            except asyncio.CancelledError:
                break
            try:
                async with self._semaphore:
                    await self._process(msg)
            except Exception as e:
                log_error(
                    f"[Dispatch] process() failed for thread_id={thread_id}: {e}"
                )
                traceback.print_exc()
            finally:
                queue.task_done()

    async def shutdown(self) -> None:
        """Cancel all worker tasks (best-effort)."""
        async with self._registry_lock:
            tasks = list(self._workers.values())
            self._workers.clear()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
