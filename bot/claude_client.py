"""Claude client wrapper for thread-aware conversations."""

import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    SystemMessage,
    Message,
    HookMatcher,
    HookContext,
)

from .session_manager import SessionManager
from .logger import (
    log_tools_startup,
    log_session_created,
    log_pre_tool_use,
    log_post_tool_use,
    log_agent_message,
    log_send_to_agent,
    log_info,
    log_error,
)


def _tool_approval_enabled_from_env() -> bool:
    """Read TOOL_APPROVAL_ENABLED; when unset or empty, approval stays enabled."""
    raw = os.getenv("TOOL_APPROVAL_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


class ClaudeClient:
    """Wrapper around ClaudeSDKClient for thread-aware conversations."""

    def __init__(
        self,
        session_manager: SessionManager,
        connector: Optional[Any] = None,
    ):
        """
        Initialize Claude client.

        Args:
            session_manager: Session manager for thread-to-session mapping.
            connector: PlatformConnector instance used for tool approval and
                       post-execution UI updates.  May be None (tools are then
                       auto-approved — useful for development/testing).
        """
        self.session_manager = session_manager
        self.connector = connector
        self._tool_approval_enabled = _tool_approval_enabled_from_env()
        self._clients: Dict[str, ClaudeSDKClient] = {}  # thread_id -> client
        self._tools_logged = False

        if self.connector:
            log_info(
                "Tool approval: "
                + (
                    "enabled"
                    if self._tool_approval_enabled
                    else "disabled — all tools run without prompts (TOOL_APPROVAL_ENABLED=false)"
                )
            )

        mcp_config_path = Path("/app/.mcp.json")

        disallowed_tools = [
            "Bash", "BashOutput", "KillBash",
            "Task", "TodoWrite", "NotebookEdit", "ExitPlanMode",
        ]

        _data_dir = os.getenv("AGENT_DATA_DIR", "/data").strip() or "/data"
        self.base_options = ClaudeAgentOptions(
            mcp_servers=mcp_config_path if mcp_config_path.exists() else {},
            cwd=_data_dir,
            disallowed_tools=disallowed_tools,
        )
        self._disallowed_tools = disallowed_tools

    async def _get_or_create_client(self, thread_id: str) -> ClaudeSDKClient:
        """Get or create a ClaudeSDKClient for a thread."""
        if thread_id not in self._clients:
            stored_session_id = self.session_manager.get_session(thread_id)

            options = ClaudeAgentOptions(
                mcp_servers=self.base_options.mcp_servers,
                resume=stored_session_id if stored_session_id else None,
                disallowed_tools=self.base_options.disallowed_tools,
                hooks=self._create_hooks(thread_id),
            )

            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._clients[thread_id] = client
            log_info(f"New agent client created for thread | thread_ts={thread_id}")

        return self._clients[thread_id]

    def _create_hooks(self, thread_id: str) -> Dict[str, list]:
        """
        Build PreToolUse / PostToolUse hooks for a thread.

        PreToolUse  — requests approval via the connector (or auto-approves if
                      no connector is configured).
        PostToolUse — notifies the connector so it can update the approval UI.
        """

        async def pre_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext,
        ) -> Dict[str, Any]:
            tool_name = input_data.get("tool_name", "unknown")
            tool_input = input_data.get("tool_input", {})
            log_pre_tool_use(tool_name=tool_name, thread_ts=thread_id, tool_use_id=tool_use_id)

            decision = "allow"
            decision_reason = ""

            if self.connector:
                if self._tool_approval_enabled:
                    try:
                        approved = await self.connector.request_approval(
                            thread_id=thread_id,
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_use_id or "",
                        )
                        decision = "allow" if approved else "deny"
                        decision_reason = (
                            f"Tool '{tool_name}' {'approved' if approved else 'denied'} by user"
                        )
                    except Exception as e:
                        log_error(f"pre_tool_use_hook: error requesting approval: {e}")
                        decision = "deny"
                        decision_reason = f"Approval request failed: {e}"
                else:
                    try:
                        await self.connector.send_message(
                            thread_id,
                            f"🔧 Using `{tool_name}`…",
                        )
                    except Exception as e:
                        log_error(f"pre_tool_use_hook: error sending tool notice: {e}")
                    decision = "allow"
                    decision_reason = "Tool approval disabled (TOOL_APPROVAL_ENABLED=false)"

            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": decision_reason,
                }
            }

        async def post_tool_use_hook(
            input_data: Dict[str, Any],
            tool_use_id: Optional[str],
            context: HookContext,
        ) -> Dict[str, Any]:
            tool_name = input_data.get("tool_name", "unknown")
            log_post_tool_use(tool_name=tool_name, thread_ts=thread_id, tool_use_id=tool_use_id)

            if self.connector and tool_use_id:
                tool_result = input_data.get("tool_result", {})
                is_error = input_data.get("is_error", False)
                try:
                    if self._tool_approval_enabled:
                        await self.connector.on_tool_result(
                            tool_use_id=tool_use_id,
                            tool_result=tool_result,
                            is_error=is_error,
                        )
                    else:
                        if is_error:
                            line = f"❌ `{tool_name}` finished with an error."
                        else:
                            line = f"✅ `{tool_name}` finished."
                        await self.connector.send_message(thread_id, line)
                except Exception as e:
                    log_error(f"post_tool_use_hook: error updating tool result: {e}")

            return {}

        return {
            "PreToolUse": [HookMatcher(hooks=[pre_tool_use_hook])],
            "PostToolUse": [HookMatcher(hooks=[post_tool_use_hook])],
        }

    async def send_message(
        self,
        thread_id: str,
        user_message: str,
    ) -> AsyncIterator[Message]:
        """
        Send a message to Claude and stream responses.

        Yields Messages from Claude (AssistantMessage, ToolUseBlock, etc.)
        """
        try:
            client = await self._get_or_create_client(thread_id)

            message_preview = user_message[:100] + "..." if len(user_message) > 100 else user_message
            log_send_to_agent(
                thread_ts=thread_id,
                message_preview=message_preview,
                message_length=len(user_message),
            )

            await client.query(user_message)

            try:
                async for message in client.receive_response():
                    if isinstance(message, SystemMessage):
                        data = getattr(message, "data", {})
                        if isinstance(data, dict) and "tools" in data and not self._tools_logged:
                            tools = data.get("tools", [])
                            if isinstance(tools, list):
                                log_info(f"Tools available: {len(tools)} total")
                                if self._disallowed_tools:
                                    log_info(f"Disallowed tools: {', '.join(sorted(self._disallowed_tools))}")
                                else:
                                    log_info("Disallowed tools: none")
                                self._tools_logged = True
                        continue

                    if isinstance(message, ResultMessage):
                        session_id = message.session_id
                        self.session_manager.store_session(thread_id, session_id)
                        log_session_created(session_id=session_id, thread_ts=thread_id)
                        continue

                    if isinstance(message, AssistantMessage):
                        block_types = [type(b).__name__ for b in message.content]
                        tool_name = None
                        tool_use_id = None
                        for block in message.content:
                            if isinstance(block, ToolUseBlock):
                                tool_name = block.name
                                tool_use_id = block.id
                                break
                        log_agent_message(
                            message_type="AssistantMessage",
                            block_types=block_types,
                            thread_ts=thread_id,
                            tool_name=tool_name,
                            tool_use_id=tool_use_id,
                        )

                    yield message

            except StopAsyncIteration:
                pass
            except Exception as iter_error:
                log_error(f"Exception during receive_response() iteration: {iter_error}")
                import traceback
                traceback.print_exc(file=sys.stderr)
                raise

        except Exception as e:
            log_error(f"Error in send_message for thread {thread_id}: {e}")
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise

    async def get_text_response(self, thread_id: str, user_message: str) -> str:
        """
        Send a message and collect all text blocks into a single string.

        This is the primary method used by the orchestrator.
        """
        text_parts = []
        async for message in self.send_message(thread_id, user_message):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
        return "\n".join(text_parts)

    async def disconnect_session(self, thread_id: str) -> None:
        """Disconnect a single thread's SDK client."""
        if thread_id in self._clients:
            await self._clients[thread_id].disconnect()
            del self._clients[thread_id]

    async def disconnect_all(self) -> None:
        """Disconnect all active SDK clients."""
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
