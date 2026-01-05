"""ChatPlatformToolAgent with ToolRunnerHooks for approval integration."""

import asyncio
from typing import Any, Dict, Optional

from connectors.interface import PlatformInterface

from .exceptions import ToolApprovalDenied
from approval.approval_tracker import ApprovalTracker
from approval.rules_manager import ApprovalRulesManager


class ChatPlatformToolAgent:
    """
    Agent wrapper that integrates fast-agent with platform connectors and approval system.

    This class provides hooks for tool execution interception to implement
    approval workflows and status messaging.
    """

    def __init__(
        self,
        platform_interface: PlatformInterface,
        rules_manager: ApprovalRulesManager,
        approval_tracker: ApprovalTracker,
        agent_instance: Any,  # fast-agent agent instance
    ):
        """
        Initialize ChatPlatformToolAgent.

        Args:
            platform_interface: Platform connector for sending messages
            rules_manager: Manager for approval rules
            approval_tracker: Tracker for pending approvals
            agent_instance: fast-agent agent instance
        """
        self.platform_interface = platform_interface
        self.rules_manager = rules_manager
        self.approval_tracker = approval_tracker
        self.agent = agent_instance
        self._current_thread_id: Optional[str] = None

    def set_thread_id(self, thread_id: str) -> None:
        """
        Set the current thread ID for this agent session.

        Args:
            thread_id: Thread/conversation identifier
        """
        self._current_thread_id = thread_id

    async def before_tool_call(
        self, tool_use_id: str, tool_name: str, tool_input: Dict[str, Any]
    ) -> None:
        """
        Hook called before tool execution (ToolRunnerHooks interface).

        Checks approval rules and requests user approval if needed.

        Args:
            tool_use_id: Tool use ID from the agent
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters

        Raises:
            ToolApprovalDenied: If tool execution is denied
        """
        if not self._current_thread_id:
            raise ValueError("Thread ID must be set before tool calls")

        # Check if approval is required
        if not self.rules_manager.requires_approval(tool_name):
            # No approval required - allow execution
            return

        # Create approval request
        approval_id = await self.approval_tracker.create_approval(
            thread_id=self._current_thread_id,
            tool_name=tool_name,
            params=tool_input,
            tool_use_id=tool_use_id,
        )

        # Request approval from platform
        approved = await self.platform_interface.request_approval(
            thread_id=self._current_thread_id,
            tool_name=tool_name,
            params=tool_input,
        )

        # Resolve the approval
        await self.approval_tracker.resolve_approval(approval_id, approved)

        # Wait for approval decision
        try:
            approved_result = await self.approval_tracker.wait_for_approval(approval_id)
            if not approved_result:
                raise ToolApprovalDenied(
                    tool_name=tool_name, reason="User denied tool execution"
                )
        except asyncio.TimeoutError:
            raise ToolApprovalDenied(
                tool_name=tool_name, reason="Approval request timed out"
            )

    async def after_tool_call(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        result: Any,
    ) -> None:
        """
        Hook called after tool execution (ToolRunnerHooks interface).

        Reports tool execution results via platform interface.

        Args:
            tool_use_id: Tool use ID from the agent
            tool_name: Name of the tool that was executed
            tool_input: Tool input parameters
            result: Tool execution result
        """
        if not self._current_thread_id:
            return

        # Report tool execution status
        # Note: PlatformInterface.send_message can be used for status updates
        # The exact implementation depends on how status messages are handled
        try:
            status_message = f"Tool '{tool_name}' execution completed"
            await self.platform_interface.send_message(
                thread_id=self._current_thread_id,
                content=status_message,
                msg_type="status",
            )
        except Exception:
            # Don't fail if status message fails
            pass

    async def send(self, message: str) -> str:
        """
        Send a message to the agent and get response.

        Args:
            message: User message to send

        Returns:
            Agent response text
        """
        # Use fast-agent's API to send message
        # The exact API may vary based on fast-agent-mcp version
        # This is a placeholder that will need adjustment based on actual API
        if hasattr(self.agent, "__call__"):
            # If agent is callable
            response = await self.agent(message)
            return response if isinstance(response, str) else str(response)
        else:
            # Fallback: try send method if available
            response = await self.agent.send(message)
            return response if isinstance(response, str) else str(response)

