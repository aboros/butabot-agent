"""Discord connector implementing PlatformConnector."""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

import nextcord
from nextcord import Intents
from nextcord.ext import commands
from dotenv import load_dotenv

from .interface import IncomingMessage, PlatformConnector

load_dotenv()

logger = logging.getLogger(__name__)

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

    Responds to @mentions only.  Thread continuity follows Discord's own
    thread model: messages inside a Discord thread share the thread's ID as
    their session key; top-level channel messages each get the originating
    message's ID so every mention starts a fresh context.
    """

    def __init__(self) -> None:
        intents = Intents.default()
        intents.message_content = True
        intents.guilds = True

        self.bot = commands.Bot(command_prefix="!", intents=intents)

        # thread_id -> discord Channel / Thread object
        self._thread_channels: Dict[str, Any] = {}
        # thread_id -> Message object for the "Thinking…" placeholder
        self._thinking_messages: Dict[str, nextcord.Message] = {}
        # tool_use_id -> Message object for the approval prompt
        self._approval_messages: Dict[str, nextcord.Message] = {}

        self._message_handler = None

        self._register_handlers()

    # ------------------------------------------------------------------
    # PlatformConnector interface
    # ------------------------------------------------------------------

    async def send_message(self, thread_id: str, content: str) -> None:
        """
        Send a response to the user.

        Updates the "Thinking…" placeholder in-place if one exists,
        otherwise posts a new message.  Long responses are split into
        multiple messages to stay within Discord's 2 000-character limit.
        """
        thinking_msg: Optional[nextcord.Message] = self._thinking_messages.pop(thread_id, None)
        channel = self._thread_channels.get(thread_id)

        chunks = _split_message(content)

        for i, chunk in enumerate(chunks):
            if i == 0 and thinking_msg:
                await thinking_msg.edit(content=chunk)
            elif channel:
                await channel.send(content=chunk)
            else:
                logger.error(f"send_message: no channel for thread {thread_id}")
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
            logger.error(
                f"request_approval: no channel for thread {thread_id}, "
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
        self, tool_use_id: str, tool_result: Any, is_error: bool
    ) -> None:
        """Edit the approval message to confirm the tool has finished."""
        msg = self._approval_messages.pop(tool_use_id, None)
        if not msg:
            return
        status = "❌ Error during execution." if is_error else "✅ Results received."
        try:
            # Preserve the existing message text, just append the status line
            updated = msg.content.split("\n")[0]  # keep the header line
            await msg.edit(content=f"{updated}\n\n_{status}_", view=None)
        except Exception as e:
            logger.error(f"on_tool_result: failed to update approval message: {e}")

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
            logger.info(f"Discord bot ready: {self.bot.user} (id={self.bot.user.id})")

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

            # Thread-ID strategy:
            #   • Inside a Discord thread  → use the thread's ID (shared context)
            #   • Top-level channel message → use the message's own ID (fresh context)
            if isinstance(message.channel, nextcord.Thread):
                thread_id = str(message.channel.id)
            else:
                thread_id = str(message.id)

            self._thread_channels[thread_id] = message.channel

            if not content:
                await message.channel.send("Hello! How can I help you?")
                return

            # Post "Thinking…" placeholder before handing off to the agent
            thinking_msg = await message.channel.send("🤔 Thinking...")
            self._thinking_messages[thread_id] = thinking_msg

            if self._message_handler:
                try:
                    incoming = IncomingMessage(
                        thread_id=thread_id,
                        channel_id=str(message.channel.id),
                        user_id=str(message.author.id),
                        content=content,
                        platform="discord",
                    )
                    await self._message_handler(incoming)
                except Exception as e:
                    logger.error(f"on_message: handler error: {e}", exc_info=True)
                    import traceback
                    traceback.print_exc(file=sys.stderr)
            else:
                logger.error("on_message: no message handler registered")
                await self.send_message(thread_id, "❌ Bot not properly initialized.")

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
