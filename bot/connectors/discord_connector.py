"""Discord connector implementing PlatformConnector."""

import json
import os
import sys
from typing import Any, Dict, Optional

import nextcord
from nextcord import Intents
from nextcord.ext import commands
from dotenv import load_dotenv

from .interface import IncomingMessage, PlatformConnector
from ..conversation_key import build_discord_conversation_key, thinking_map_key
from ..logger import log_error, log_info, log_warning

load_dotenv()

# Discord's hard limit for a single message
_MAX_MESSAGE_LENGTH = 1990


class _ApprovalView(nextcord.ui.View):
    """
    An ephemeral View with Approve / Deny buttons.

    Rendered inline in the approval message.  Whoever clicks first wins;
    the view is then stopped so ``view.wait()`` unblocks.
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(timeout=300)
        self.decision: Optional[bool] = None
        self._tool_name = tool_name

    @nextcord.ui.button(label="Approve", style=nextcord.ButtonStyle.green)
    async def approve_button(
        self, button: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        self.decision = True
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ **Tool approved:** `{self._tool_name}` — executing...",
            view=None,
        )

    @nextcord.ui.button(label="Deny", style=nextcord.ButtonStyle.red)
    async def deny_button(
        self, button: nextcord.ui.Button, interaction: nextcord.Interaction
    ) -> None:
        self.decision = False
        self.stop()
        await interaction.response.edit_message(
            content=f"❌ **Tool denied:** `{self._tool_name}`",
            view=None,
        )


class DiscordConnector(PlatformConnector):
    """
    Discord platform connector.

    Responds to @mentions only. Session keys are built via
    ``build_discord_conversation_key`` (Discord threads vs DISCORD_TOP_LEVEL_KEY
    for channel messages).
    """

    def __init__(self) -> None:
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True

        self.bot = commands.Bot(command_prefix="!", intents=intents)

        # thread_id -> discord Channel / Thread object
        self._thread_channels: Dict[str, Any] = {}
        # thinking_map_key(thread_id, source_message_id) -> Message for "Thinking…"
        self._thinking_messages: Dict[str, nextcord.Message] = {}
        # tool_use_id -> Message object for the approval prompt
        self._approval_messages: Dict[str, nextcord.Message] = {}
        # tool_use_id -> "Using …" status message (when approval is disabled)
        self._tool_progress_messages: Dict[str, nextcord.Message] = {}

        self._message_handler = None

        self._register_handlers()

    # ------------------------------------------------------------------
    # PlatformConnector interface
    # ------------------------------------------------------------------

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

        By default, updates the "Thinking…" placeholder in-place if one exists,
        otherwise posts a new message.  Set release_thinking_placeholder to
        drop tracking without editing Thinking, then post the reply as new
        messages.  Set replace_thinking_placeholder to False (without release)
        to post alongside Thinking (e.g. tool status).  Long responses are split
        into multiple messages to stay within Discord's 2 000-character limit.
        """
        tk = thinking_map_key(thread_id, source_message_id)
        if release_thinking_placeholder:
            self._thinking_messages.pop(tk, None)
            thinking_msg = None
        elif replace_thinking_placeholder:
            thinking_msg = self._thinking_messages.pop(tk, None)
        else:
            thinking_msg = None

        channel = self._thread_channels.get(thread_id)

        chunks = _split_message(content)

        for i, chunk in enumerate(chunks):
            if i == 0 and thinking_msg:
                await thinking_msg.edit(content=chunk)
            elif channel:
                sent = await channel.send(content=chunk)
                if (
                    tool_use_id
                    and not replace_thinking_placeholder
                    and not release_thinking_placeholder
                    and i == 0
                ):
                    self._tool_progress_messages[tool_use_id] = sent
            else:
                log_error(f"[Discord] send_message: no channel for thread {thread_id}")
                return

    async def request_approval(
        self,
        thread_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> bool:
        """
        Post an approval prompt with Approve / Deny buttons and wait for a click.

        Times out after 300 seconds (same default as Slack).
        """
        channel = self._thread_channels.get(thread_id)
        if not channel:
            log_error(
                f"[Discord] request_approval: no channel for thread {thread_id}, "
                f"denying tool '{tool_name}'"
            )
            return False

        tool_input_str = json.dumps(tool_input, indent=2, ensure_ascii=False)
        if len(tool_input_str) > 900:
            tool_input_str = tool_input_str[:900] + "\n… (truncated)"

        content = (
            f"**🔧 Tool Approval Request**\n\n"
            f"**Tool:** `{tool_name}`\n\n"
            f"**Input:**\n```json\n{tool_input_str}\n```"
        )

        view = _ApprovalView(tool_name=tool_name)
        approval_msg = await channel.send(content=content, view=view)
        self._approval_messages[tool_use_id] = approval_msg

        await view.wait()

        if view.decision is None:
            # Timed out — update message and deny
            await approval_msg.edit(
                content=f"⏱️ Approval timed out. Denying `{tool_name}`.",
                view=None,
            )
            return False

        return view.decision

    async def on_tool_result(
        self,
        tool_use_id: str,
        tool_result: Any,
        is_error: bool,
        *,
        tool_name: Optional[str] = None,
    ) -> None:
        """Edit the approval or tool-status message when the tool has finished."""
        msg = self._approval_messages.pop(tool_use_id, None)
        if msg:
            status = "❌ Error during execution." if is_error else "✅ Results received."
            try:
                updated = msg.content.split("\n")[0]
                await msg.edit(content=f"{updated}\n\n_{status}_", view=None)
            except Exception as e:
                log_error(
                    f"[Discord] on_tool_result: failed to update approval message: {e}"
                )
            return

        prog = self._tool_progress_messages.pop(tool_use_id, None)
        if not prog:
            return
        name = tool_name or "tool"
        if is_error:
            line = f"❌ `{name}` finished with an error."
        else:
            line = f"✅ `{name}` finished."
        try:
            await prog.edit(content=line)
        except Exception as e:
            log_error(
                f"[Discord] on_tool_result: failed to update tool status message: {e}"
            )

    async def start(self) -> None:
        """Start the Discord bot."""
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN environment variable is required")
        await self.bot.start(token)

    async def stop(self) -> None:
        """Shut down the Discord bot."""
        await self.bot.close()

    # ------------------------------------------------------------------
    # Discord event handlers
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:

        @self.bot.event
        async def on_ready() -> None:
            log_info(f"[Discord] Bot ready: {self.bot.user} (id={self.bot.user.id})")

        @self.bot.event
        async def on_connect() -> None:
            log_info("[Discord] Connected to gateway.")

        @self.bot.event
        async def on_disconnect() -> None:
            log_warning("[Discord] Disconnected from gateway.")

        @self.bot.event
        async def on_message(message: nextcord.Message) -> None:
            # Ignore bot messages
            if message.author.bot:
                return

            # Only respond to @mentions
            if not (self.bot.user and self.bot.user in message.mentions):
                return

            # Strip the mention from the content
            content = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
            # Also handle nickname mentions: <@!id>
            content = content.replace(f"<@!{self.bot.user.id}>", "").strip()

            in_thread = isinstance(message.channel, nextcord.Thread)
            discord_tid = int(message.channel.id) if in_thread else None
            thread_id = build_discord_conversation_key(
                channel_id=str(message.channel.id),
                user_id=str(message.author.id),
                message_id=message.id,
                in_thread=in_thread,
                discord_thread_id=discord_tid,
            )
            source_message_id = str(message.id)

            self._thread_channels[thread_id] = message.channel

            if not content:
                await message.channel.send("Hello! How can I help you?")
                return

            # Post "Thinking…" placeholder before handing off to the agent
            thinking_msg = await message.channel.send("🤔 Thinking...")
            self._thinking_messages[
                thinking_map_key(thread_id, source_message_id)
            ] = thinking_msg

            if self._message_handler:
                try:
                    incoming = IncomingMessage(
                        thread_id=thread_id,
                        channel_id=str(message.channel.id),
                        user_id=str(message.author.id),
                        content=content,
                        platform="discord",
                        source_message_id=source_message_id,
                    )
                    await self._message_handler(incoming)
                except Exception as e:
                    log_error(f"[Discord] on_message: handler error: {e}")
                    import traceback
                    traceback.print_exc(file=sys.stderr)
            else:
                log_error("[Discord] on_message: no message handler registered")
                await self.send_message(
                    thread_id,
                    "❌ Bot not properly initialized.",
                    source_message_id=source_message_id,
                    replace_thinking_placeholder=False,
                    release_thinking_placeholder=True,
                )

            await self.bot.process_commands(message)


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _split_message(content: str, max_len: int = _MAX_MESSAGE_LENGTH) -> list:
    """
    Split a string into chunks that fit within Discord's message length limit.

    Prefers splitting on paragraph breaks, then newlines, then word
    boundaries, falling back to a hard cut only when necessary.
    """
    if len(content) <= max_len:
        return [content]

    chunks = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break

        # Try paragraph break first, then newline, then space
        for sep in ("\n\n", "\n", " "):
            pos = content.rfind(sep, 0, max_len)
            if pos > 0:
                chunks.append(content[:pos])
                content = content[pos + len(sep):]
                break
        else:
            # No suitable break found — hard cut
            chunks.append(content[:max_len])
            content = content[max_len:]

    return chunks
