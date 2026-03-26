"""Slack connector implementing PlatformConnector."""

import os
import re
import sys
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from .interface import IncomingMessage, PlatformConnector
from ..conversation_key import build_slack_conversation_key, thinking_map_key
from ..tool_approval import ToolApprovalManager
from ..logger import (
    log_error,
    log_info,
    log_slack_api_call,
    log_slack_event,
)

load_dotenv()


class SlackConnector(PlatformConnector):
    """
    Slack platform connector.

    Owns all Slack Bolt event handling, the approval UI (Block Kit buttons
    and in-place message updates), and the "Thinking…" placeholder pattern.
    Nothing Slack-specific leaks beyond this class.
    """

    def __init__(self) -> None:
        self.app = AsyncApp(
            token=os.getenv("SLACK_BOT_TOKEN"),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
        )
        self.slack_client = AsyncWebClient(token=os.getenv("SLACK_BOT_TOKEN"))
        self.tool_approval_manager = ToolApprovalManager(self.slack_client)

        self._bot_user_id: Optional[str] = None
        # conversation_key -> channel_id
        self._thread_channels: Dict[str, str] = {}
        # conversation_key -> Slack API thread_ts for posting in that thread
        self._slack_api_thread_ts: Dict[str, str] = {}
        # thinking_map_key(conv_key, event_ts) -> (channel_id, thinking_ts)
        self._thinking_messages: Dict[str, Tuple[str, str]] = {}
        # tool_use_id -> (channel_id, message_ts) for "Using …" when approval is off
        self._tool_progress_messages: Dict[str, Tuple[str, str]] = {}

        self._message_handler = None
        self._socket_handler: Optional[AsyncSocketModeHandler] = None

        self._register_handlers()

    # ------------------------------------------------------------------
    # PlatformConnector interface
    # ------------------------------------------------------------------

    def _slack_api_ts(self, thread_id: str) -> str:
        """Slack thread_ts for API calls (may differ from conversation key)."""
        return self._slack_api_thread_ts.get(thread_id, thread_id)

    async def send_message(
        self,
        thread_id: str,
        content: str,
        *,
        source_message_id: Optional[str] = None,
        replace_thinking_placeholder: bool = True,
        tool_use_id: Optional[str] = None,
        release_thinking_placeholder: bool = False,
    ) -> None:
        """
        Send a response to the user.

        By default, if a "Thinking…" placeholder exists for this thread it is
        updated in-place; otherwise a new message is posted.  When
        release_thinking_placeholder is True, tracking for Thinking is
        dropped without editing that message, and this content is posted as a
        new message.  When replace_thinking_placeholder is False without
        release, a new message is posted and Thinking is left unchanged (e.g.
        tool status lines).
        """
        tk = thinking_map_key(thread_id, source_message_id)
        if release_thinking_placeholder:
            self._thinking_messages.pop(tk, None)
            pending = None
        elif replace_thinking_placeholder:
            pending = self._thinking_messages.pop(tk, None)
        else:
            pending = None

        if pending:
            channel_id, thinking_ts = pending
        else:
            channel_id = self._thread_channels.get(thread_id, thread_id)
            thinking_ts = None

        api_ts = self._slack_api_ts(thread_id)

        _, content = self._detect_api_error(content)
        blocks = [{"type": "markdown", "text": content}]
        text_fallback = self._blocks_to_plain_text(blocks)

        if thinking_ts:
            log_slack_api_call(
                method="chat_update",
                thread_ts=api_ts,
                ts=thinking_ts,
                additional_info="type=response",
            )
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=thinking_ts,
                text=text_fallback,
                blocks=blocks,
                thread_ts=api_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
        else:
            extra = "type=response"
            if release_thinking_placeholder:
                extra = "type=response_release"
            elif tool_use_id and not replace_thinking_placeholder:
                extra = "type=tool_status"
            log_slack_api_call(
                method="chat_postMessage",
                thread_ts=api_ts,
                additional_info=extra,
            )
            post_resp = await self.slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=api_ts,
                text=text_fallback,
                blocks=blocks,
                unfurl_links=False,
                unfurl_media=False,
            )
            if (
                tool_use_id
                and not replace_thinking_placeholder
                and not release_thinking_placeholder
            ):
                posted_ts = post_resp.get("ts") if post_resp else None
                if posted_ts:
                    self._tool_progress_messages[tool_use_id] = (
                        channel_id,
                        posted_ts,
                    )

    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> bool:
        """Post Slack Block Kit approval buttons and wait for the user's click."""
        channel_id = self._thread_channels.get(thread_id)
        if not channel_id:
            log_error(
                f"request_approval: no channel_id for thread {thread_id}, "
                f"denying tool '{tool_name}'"
            )
            return False

        slack_ts = self._slack_api_ts(thread_id)

        approval_id, approved, message_ts = await self.tool_approval_manager.request_approval(
            thread_id=slack_ts,
            channel_id=channel_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
        )

        if approved and message_ts:
            self.tool_approval_manager.store_tool_use_mapping(tool_use_id, approval_id)
        elif not approved:
            self.tool_approval_manager._cleanup_approval(approval_id)

        return approved

    async def on_tool_result(
        self,
        tool_use_id: str,
        tool_result: Any,
        is_error: bool,
        *,
        tool_name: Optional[str] = None,
    ) -> None:
        """Update the approval or tool-status message once a tool has finished."""
        prog = self._tool_progress_messages.pop(tool_use_id, None)
        if prog:
            channel_id, msg_ts = prog
            name = tool_name or "tool"
            if is_error:
                line = f"❌ `{name}` finished with an error."
            else:
                line = f"✅ `{name}` finished."
            blocks = [{"type": "markdown", "text": line}]
            text_fallback = self._blocks_to_plain_text(blocks)
            try:
                log_slack_api_call(
                    method="chat_update",
                    ts=msg_ts,
                    additional_info="type=tool_status_result",
                )
                await self.slack_client.chat_update(
                    channel=channel_id,
                    ts=msg_ts,
                    text=text_fallback,
                    blocks=blocks,
                    unfurl_links=False,
                    unfurl_media=False,
                )
            except Exception as e:
                log_error(
                    f"on_tool_result: failed to update tool status message: {e}"
                )
            return

        try:
            await self.tool_approval_manager.update_approval_message_with_result(
                tool_use_id=tool_use_id,
                tool_result=tool_result,
                is_error=is_error,
            )
        except Exception as e:
            log_error(f"on_tool_result: failed to update approval message: {e}")

    async def start(self) -> None:
        """Start the bot in Socket Mode."""
        app_token = os.getenv("SLACK_APP_TOKEN")
        if not app_token:
            raise ValueError("SLACK_APP_TOKEN environment variable is required for Socket Mode")
        self._socket_handler = AsyncSocketModeHandler(self.app, app_token)
        await self._socket_handler.start_async()

    async def stop(self) -> None:
        """Disconnect the Socket Mode handler."""
        if self._socket_handler:
            await self._socket_handler.close_async()

    # ------------------------------------------------------------------
    # Slack event / action handlers
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:

        @self.app.middleware
        async def log_all_events(body: Dict[str, Any], next):
            event = body.get("event", {})
            event_type = event.get("type", body.get("type", "unknown"))
            log_info(f"Received Slack event: {event_type}")
            return await next()

        @self.app.event("app_mention")
        async def handle_app_mention(event: Dict[str, Any], say, client):
            try:
                event_ts = event.get("ts")
                log_slack_event(event_type="app_mention", event_ts=event_ts)

                channel_id = event.get("channel")
                thread_ts = event.get("thread_ts") or event.get("ts")
                event_ts = event.get("ts")

                bot_user_id = await self._get_bot_user_id()
                user_message = event.get("text", "")
                if f"<@{bot_user_id}>" in user_message:
                    user_message = user_message.replace(f"<@{bot_user_id}>", "").strip()

                conv_key, _ = build_slack_conversation_key(
                    thread_ts=thread_ts,
                    channel_id=channel_id,
                    user_id=event.get("user", ""),
                )
                self._thread_channels[conv_key] = channel_id
                self._slack_api_thread_ts[conv_key] = thread_ts

                if not user_message:
                    await say(
                        text="Hello! How can I help you?",
                        blocks=[{"type": "markdown", "text": "Hello! How can I help you?"}],
                        thread_ts=thread_ts,
                        unfurl_links=False,
                        unfurl_media=False,
                    )
                    return

                # Post thinking placeholder before handing off to the agent
                log_slack_api_call(
                    method="say",
                    thread_ts=thread_ts,
                    additional_info="type=thinking",
                )
                thinking_response = await say(
                    text="🤔 Thinking...",
                    blocks=[{"type": "markdown", "text": "🤔 Thinking..."}],
                    thread_ts=thread_ts,
                    unfurl_links=False,
                    unfurl_media=False,
                )
                thinking_ts = thinking_response.get("ts") if thinking_response else None
                if thinking_ts and event_ts:
                    self._thinking_messages[
                        thinking_map_key(conv_key, event_ts)
                    ] = (channel_id, thinking_ts)

                if self._message_handler:
                    message = IncomingMessage(
                        thread_id=conv_key,
                        channel_id=channel_id,
                        user_id=event.get("user", ""),
                        content=user_message,
                        platform="slack",
                        source_message_id=event_ts,
                        slack_thread_ts=thread_ts,
                    )
                    await self._message_handler(message)
                else:
                    log_error("handle_app_mention: no message handler registered")
                    await self.send_message(
                        conv_key,
                        "❌ Bot not properly initialized.",
                        source_message_id=event_ts,
                        replace_thinking_placeholder=False,
                        release_thinking_placeholder=True,
                    )

            except Exception as e:
                log_error(f"handle_app_mention: unexpected error: {e}")
                import traceback
                traceback.print_exc(file=sys.stderr)

        @self.app.action("tool_approve")
        async def handle_tool_approve(ack, body: Dict[str, Any]):
            await ack()
            approval_id = body["actions"][0]["value"]
            approval_data = self.tool_approval_manager.get_pending_approval(approval_id)

            msg = body.get("message", {})
            event_ts = msg.get("ts")
            thread_ts = msg.get("thread_ts")
            tool_name = approval_data.get("tool_name", "unknown") if approval_data else "unknown"
            tool_use_id = approval_data.get("tool_use_id", "") if approval_data else ""
            extra = f"tool={tool_name}" + (f" | tool_use_id={tool_use_id}" if tool_use_id else "")
            log_slack_event(
                event_type="tool_approve",
                event_ts=event_ts,
                thread_ts=thread_ts,
                additional_info=extra,
            )

            self.tool_approval_manager.handle_approval_response(approval_id, approved=True)

            message_ts = body["message"].get("ts")
            channel_id = body["channel"]["id"]

            if approval_data:
                updated_blocks = self.tool_approval_manager.format_approval_message(
                    tool_name=approval_data.get("tool_name", "unknown"),
                    tool_input=approval_data.get("tool_input", {}),
                    approved=True,
                )
            else:
                updated_blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "✅ Tool approved. Executing..."}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Executing tool..._"}]},
                ]

            log_slack_api_call(
                method="chat_update",
                thread_ts=thread_ts,
                ts=message_ts,
                additional_info=f"type=approval | {extra}",
            )
            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Tool approved: {tool_name}",
                blocks=updated_blocks,
                unfurl_links=False,
                unfurl_media=False,
            )

        @self.app.action("tool_deny")
        async def handle_tool_deny(ack, body: Dict[str, Any]):
            await ack()
            approval_id = body["actions"][0]["value"]
            approval_data = self.tool_approval_manager.get_pending_approval(approval_id)

            msg = body.get("message", {})
            event_ts = msg.get("ts")
            thread_ts = msg.get("thread_ts")
            tool_name = approval_data.get("tool_name", "unknown") if approval_data else "unknown"
            tool_use_id = approval_data.get("tool_use_id", "") if approval_data else ""
            extra = f"tool={tool_name}" + (f" | tool_use_id={tool_use_id}" if tool_use_id else "")
            log_slack_event(
                event_type="tool_deny",
                event_ts=event_ts,
                thread_ts=thread_ts,
                additional_info=extra,
            )

            self.tool_approval_manager.handle_approval_response(approval_id, approved=False)

            message_ts = body["message"].get("ts")
            channel_id = body["channel"]["id"]

            if approval_data:
                updated_blocks = self.tool_approval_manager.format_approval_message(
                    tool_name=approval_data.get("tool_name", "unknown"),
                    tool_input=approval_data.get("tool_input", {}),
                    approved=False,
                )
            else:
                updated_blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "❌ Tool denied."}},
                    {"type": "context", "elements": [{"type": "mrkdwn", "text": "_Tool execution cancelled._"}]},
                ]

            await self.slack_client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Tool denied: {tool_name}",
                blocks=updated_blocks,
                unfurl_links=False,
                unfurl_media=False,
            )

        @self.app.event("message")
        async def handle_message(event: Dict[str, Any]):
            log_slack_event(event_type="message", event_ts=event.get("ts"))
            # Bot messages are ignored; non-mention messages are not processed.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_bot_user_id(self) -> str:
        """Return the bot's Slack user ID, fetching and caching it on first call."""
        if not self._bot_user_id:
            auth_response = await self.slack_client.auth_test()
            self._bot_user_id = auth_response.get("user_id", "")
        return self._bot_user_id

    def _detect_api_error(self, text: str) -> Tuple[bool, str]:
        """Detect known Anthropic API error patterns and return a user-friendly message."""
        if "529" in text and "overloaded" in text.lower():
            return True, "⚠️ *API Temporarily Overloaded*\n\nThe Claude API is currently experiencing high load. Please try again in a few moments."
        if "API Error" in text or '"type":"error"' in text:
            if "overloaded" in text.lower():
                return True, "⚠️ *API Temporarily Overloaded*\n\nThe Claude API is currently experiencing high load. Please try again in a few moments."
            if "rate_limit" in text.lower() or "429" in text:
                return True, "⚠️ *Rate Limit Exceeded*\n\nToo many requests. Please wait a moment before trying again."
            if "401" in text or "unauthorized" in text.lower():
                return True, "❌ *Authentication Error*\n\nThere's an issue with API authentication. Please contact support."
            if "500" in text or "internal" in text.lower():
                return True, "❌ *API Internal Error*\n\nThe API encountered an internal error. Please try again later."
            return True, "❌ *API Error*\n\nAn error occurred while processing your request. Please try again."
        return False, text

    def _blocks_to_plain_text(self, blocks: list) -> str:
        """Extract plain text from Slack Block Kit blocks for use as the fallback ``text`` field."""
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "markdown" and "text" in block:
                parts.append(block["text"])
            elif btype == "section" and "text" in block:
                obj = block["text"]
                if isinstance(obj, dict):
                    parts.append(obj.get("text", ""))
            elif btype == "context":
                for el in block.get("elements", []):
                    if isinstance(el, dict) and "text" in el:
                        parts.append(el["text"])
        combined = " ".join(parts)
        # Strip markdown for plain-text fallback
        combined = re.sub(r"\*\*(.*?)\*\*", r"\1", combined)
        combined = re.sub(r"\*(.*?)\*", r"\1", combined)
        combined = re.sub(r"`(.*?)`", r"\1", combined)
        combined = re.sub(r"```[\s\S]*?```", "", combined)
        return combined.strip() or "Message updated"
