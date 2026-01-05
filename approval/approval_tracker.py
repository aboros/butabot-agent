"""Approval tracker for managing pending tool approval requests with async resolution."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional
from uuid import UUID

import asyncio


@dataclass
class ToolApprovalRequest:
    """Represents a pending tool approval request."""

    approval_id: UUID
    thread_id: str
    tool_name: str
    tool_input: dict
    tool_use_id: str
    created_at: datetime
    timeout: float
    future: asyncio.Future = field(default_factory=lambda: asyncio.Future())

    def is_expired(self) -> bool:
        """Check if this approval request has expired."""
        expiry_time = self.created_at + timedelta(seconds=self.timeout)
        return datetime.now() > expiry_time


class ApprovalTracker:
    """Tracks pending tool approval requests with async resolution and timeouts."""

    def __init__(self):
        """Initialize ApprovalTracker."""
        self._approvals: Dict[UUID, ToolApprovalRequest] = {}
        self._lock = asyncio.Lock()

    async def create_approval(
        self,
        thread_id: str,
        tool_name: str,
        params: dict,
        tool_use_id: str,
        timeout: float = 300.0,
    ) -> UUID:
        """
        Create a new approval request.

        Args:
            thread_id: Thread/conversation identifier
            tool_name: Name of the tool requesting approval
            params: Tool parameters/input
            tool_use_id: Tool use ID from the agent
            timeout: Approval timeout in seconds (default: 300)

        Returns:
            Approval ID (UUID) for tracking this request
        """
        approval_id = uuid.uuid4()
        future = asyncio.Future()

        request = ToolApprovalRequest(
            approval_id=approval_id,
            thread_id=thread_id,
            tool_name=tool_name,
            tool_input=params,
            tool_use_id=tool_use_id,
            created_at=datetime.now(),
            timeout=timeout,
            future=future,
        )

        async with self._lock:
            self._approvals[approval_id] = request

        return approval_id

    async def resolve_approval(self, approval_id: UUID, approved: bool) -> bool:
        """
        Resolve an approval request.

        Args:
            approval_id: Approval ID to resolve
            approved: True if approved, False if denied

        Returns:
            True if approval was resolved, False if not found
        """
        async with self._lock:
            request = self._approvals.get(approval_id)
            if request is None:
                return False

            # Set the future result
            if not request.future.done():
                request.future.set_result(approved)

            # Remove from tracking
            del self._approvals[approval_id]

        return True

    async def get_future(self, approval_id: UUID) -> asyncio.Future:
        """
        Get the Future for an approval request.

        Args:
            approval_id: Approval ID

        Returns:
            Future that will resolve to True/False when approval is decided

        Raises:
            KeyError: If approval_id is not found
        """
        async with self._lock:
            request = self._approvals.get(approval_id)
            if request is None:
                raise KeyError(f"Approval ID {approval_id} not found")

            return request.future

    async def wait_for_approval(
        self, approval_id: UUID, timeout: Optional[float] = None
    ) -> bool:
        """
        Wait for an approval decision with optional timeout.

        Args:
            approval_id: Approval ID to wait for
            timeout: Optional timeout in seconds (uses request timeout if None)

        Returns:
            True if approved, False if denied or timed out

        Raises:
            KeyError: If approval_id is not found
            asyncio.TimeoutError: If timeout occurs (only if timeout is explicitly provided)
        """
        future = await self.get_future(approval_id)

        # Use provided timeout or get from request
        if timeout is None:
            async with self._lock:
                request = self._approvals.get(approval_id)
                if request is None:
                    raise KeyError(f"Approval ID {approval_id} not found")
                timeout = request.timeout

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            # Timeout occurred - resolve as denied
            await self.resolve_approval(approval_id, approved=False)
            return False

    async def cleanup_expired(self) -> int:
        """
        Remove expired approval requests.

        Returns:
            Number of expired approvals cleaned up
        """
        async with self._lock:
            expired_ids = [
                approval_id
                for approval_id, request in self._approvals.items()
                if request.is_expired()
            ]

            for approval_id in expired_ids:
                request = self._approvals[approval_id]
                # Resolve future as denied if not already resolved
                if not request.future.done():
                    request.future.set_result(False)
                del self._approvals[approval_id]

            return len(expired_ids)

    async def get_pending_approval(self, approval_id: UUID) -> Optional[dict]:
        """
        Get approval request details (for display/logging).

        Args:
            approval_id: Approval ID

        Returns:
            Dictionary with approval details, or None if not found
        """
        async with self._lock:
            request = self._approvals.get(approval_id)
            if request is None:
                return None

            return {
                "approval_id": str(request.approval_id),
                "thread_id": request.thread_id,
                "tool_name": request.tool_name,
                "tool_input": request.tool_input,
                "tool_use_id": request.tool_use_id,
                "created_at": request.created_at.isoformat(),
                "timeout": request.timeout,
                "is_expired": request.is_expired(),
            }

    async def get_pending_count(self) -> int:
        """Get the number of pending approvals."""
        async with self._lock:
            return len(self._approvals)

