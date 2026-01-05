"""Custom exceptions for agent operations."""


class ToolApprovalDenied(Exception):
    """Exception raised when tool execution is denied by user approval."""

    def __init__(self, tool_name: str, reason: str = "Tool execution was denied"):
        """
        Initialize ToolApprovalDenied exception.

        Args:
            tool_name: Name of the tool that was denied
            reason: Reason for denial
        """
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"{reason}: {tool_name}")

