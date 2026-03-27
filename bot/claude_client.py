"""Claude client wrapper for thread-aware conversations."""

import os
import sys
from dataclasses import replace
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


def _parse_optional_env_bool(name: str) -> Optional[bool]:
    """Return True/False if the env var is set to a non-empty value, else None."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _tool_feedback_settings_from_env() -> tuple[bool, bool, bool]:
    """
    Per-flag tool chat feedback (Slack / Discord).

    TOOL_FEEDBACK_START — "🔧 Using …" when approval is off.
    TOOL_FEEDBACK_FINISH — ✅/❌ on that status message when the tool completes.
    TOOL_FEEDBACK_APPROVAL_RESULT — update the approval message after the tool runs.

    Legacy: if TOOL_FEEDBACK_ENABLED is set, it acts as the default for any flag
    not explicitly set (false mutes all three unless overridden).
    """
    legacy = _parse_optional_env_bool("TOOL_FEEDBACK_ENABLED")

    def one(flag_name: str) -> bool:
        v = _parse_optional_env_bool(flag_name)
        if v is not None:
            return v
        if legacy is not None:
            return legacy
        return True

    return (
        one("TOOL_FEEDBACK_START"),
        one("TOOL_FEEDBACK_FINISH"),
        one("TOOL_FEEDBACK_APPROVAL_RESULT"),
    )


def _tool_feedback_flow_from_env() -> str:
    """
    TOOL_FEEDBACK_FLOW: normal (default), update, or thinking_log.

    update — after approval, the same message is edited to "Using …", then to
    the final result (single in-thread status line).
    thinking_log — with approval off, tool lines append under the Thinking
    placeholder; final reply is still a new message (release_thinking).
    normal — current behavior (e.g. Slack approve button updates the message
    to an approved/executing state; finish updates separately without a dedicated
    notify_tool_running step).
    """
    raw = (os.getenv("TOOL_FEEDBACK_FLOW") or "").strip().lower()
    if raw in ("update", "in_place", "chain"):
        return "update"
    if raw in ("thinking_log", "thinking-log", "thinking_stream", "thinking-stream"):
        return "thinking_log"
    return "normal"


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
        (
            self._feedback_start,
            self._feedback_finish,
            self._feedback_approval_result,
        ) = _tool_feedback_settings_from_env()
        self._feedback_flow = _tool_feedback_flow_from_env()
        #: Per active agent turn: maps thread_id -> source_message_id for Thinking key.
        self._turn_source_message_id: Dict[str, Optional[str]] = {}
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
            log_info(
                "Tool feedback (chat): "
                f"flow={self._feedback_flow} "
                f"start={self._feedback_start} "
                f"finish={self._feedback_finish} "
                f"approval_result={self._feedback_approval_result}"
            )
            if self._feedback_flow == "thinking_log" and self._tool_approval_enabled:
                log_info(
                    "thinking_log requires TOOL_APPROVAL_ENABLED=false; "
                    "tool feedback uses normal channels until approval is off"
                )

        mcp_config_path = Path("/app/.mcp.json")

        disallowed_tools = [
            "Bash", "BashOutput", "KillBash",
            "Task", "TodoWrite", "NotebookEdit", "ExitPlanMode",
        ]

        _data_dir = os.getenv("AGENT_DATA_DIR", "/data").strip() or "/data"
        # Project skills: `.claude/skills/` under the agent workspace (AGENT_DATA_DIR / cwd).
        # Requires setting_sources + Skill in allowed_tools per Agent SDK.
        self.base_options = ClaudeAgentOptions(
            mcp_servers=mcp_config_path if mcp_config_path.exists() else {},
            cwd=_data_dir,
            setting_sources=["project"],
            allowed_tools=["Skill"],
            disallowed_tools=disallowed_tools,
        )
        self._disallowed_tools = disallowed_tools
        log_info(
            f"Agent workspace (cwd): {_data_dir} — load project skills from "
            f"{_data_dir}/.claude/skills/ (setting_sources includes project)"
        )

    async def _get_or_create_client(self, thread_id: str) -> ClaudeSDKClient:
        """Get or create a ClaudeSDKClient for a thread."""
        if thread_id not in self._clients:
            stored_session_id = self.session_manager.get_session(thread_id)

            options = replace(
                self.base_options,
                resume=stored_session_id if stored_session_id else None,
                hooks=self._create_hooks(thread_id),
            )

            client = ClaudeSDKClient(options=options)
            await client.connect()
            self._clients[thread_id] = client
            log_info(f"New agent client created for thread | thread_ts={thread_id}")

        return self._clients[thread_id]

    def _effective_thinking_log(self) -> bool:
        """Stream tool lines into the Thinking message (requires approval off)."""
        return self._feedback_flow == "thinking_log" and not self._tool_approval_enabled

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
                        if (
                            approved
                            and self._feedback_flow == "update"
                            and self._feedback_start
                        ):
                            try:
                                await self.connector.notify_tool_running(
                                    thread_id=thread_id,
                                    tool_name=tool_name,
                                    tool_use_id=tool_use_id or "",
                                )
                            except Exception as e:
                                log_error(
                                    f"pre_tool_use_hook: notify_tool_running: {e}"
                                )
                    except Exception as e:
                        log_error(f"pre_tool_use_hook: error requesting approval: {e}")
                        decision = "deny"
                        decision_reason = f"Approval request failed: {e}"
                else:
                    sid = self._turn_source_message_id.get(thread_id)
                    if self._effective_thinking_log() and sid and self._feedback_start:
                        try:
                            await self.connector.append_thinking_tool_feedback(
                                thread_id,
                                sid,
                                f"🔧 Using `{tool_name}`…",
                            )
                        except Exception as e:
                            log_error(
                                f"pre_tool_use_hook: append_thinking_tool_feedback: {e}"
                            )
                    elif self._feedback_start:
                        try:
                            await self.connector.send_message(
                                thread_id,
                                f"🔧 Using `{tool_name}`…",
                                replace_thinking_placeholder=False,
                                tool_use_id=tool_use_id or None,
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
                if self._tool_approval_enabled:
                    try:
                        await self.connector.on_tool_result(
                            tool_use_id=tool_use_id,
                            tool_result=tool_result,
                            is_error=is_error,
                            tool_name=tool_name,
                            update_approval_message=self._feedback_approval_result,
                            update_progress_message=False,
                        )
                    except Exception as e:
                        log_error(f"post_tool_use_hook: error updating tool result: {e}")
                elif self._effective_thinking_log():
                    sid = self._turn_source_message_id.get(thread_id)
                    if sid and self._feedback_finish:
                        name = tool_name or "tool"
                        line = (
                            f"❌ `{name}` finished with an error."
                            if is_error
                            else f"✅ `{name}` finished."
                        )
                        try:
                            await self.connector.append_thinking_tool_feedback(
                                thread_id,
                                sid,
                                line,
                            )
                        except Exception as e:
                            log_error(
                                f"post_tool_use_hook: append_thinking_tool_feedback: {e}"
                            )
                elif self._feedback_start or self._feedback_finish:
                    try:
                        await self.connector.on_tool_result(
                            tool_use_id=tool_use_id,
                            tool_result=tool_result,
                            is_error=is_error,
                            tool_name=tool_name,
                            update_approval_message=False,
                            update_progress_message=self._feedback_finish,
                        )
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
        *,
        source_message_id: Optional[str] = None,
    ) -> AsyncIterator[Message]:
        """
        Send a message to Claude and stream responses.

        Yields Messages from Claude (AssistantMessage, ToolUseBlock, etc.)
        """
        self._turn_source_message_id[thread_id] = source_message_id
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
                                log_tools_startup(len(tools), self._disallowed_tools)
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
        finally:
            self._turn_source_message_id.pop(thread_id, None)

    async def get_text_response(
        self,
        thread_id: str,
        user_message: str,
        *,
        source_message_id: Optional[str] = None,
    ) -> str:
        """
        Send a message and collect all text blocks into a single string.

        This is the primary method used by the orchestrator.
        """
        text_parts = []
        async for message in self.send_message(
            thread_id, user_message, source_message_id=source_message_id
        ):
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
