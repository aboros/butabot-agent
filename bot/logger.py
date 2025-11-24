"""Centralized logging utility for structured, informative logging."""

import sys
from typing import Any, List, Optional


def log_info(message: str):
    """Log an INFO level message."""
    print(f"[INFO] {message}", file=sys.stderr, flush=True)


def log_warning(message: str):
    """Log a WARNING level message."""
    print(f"[WARNING] {message}", file=sys.stderr, flush=True)


def log_error(message: str):
    """Log an ERROR level message."""
    print(f"[ERROR] {message}", file=sys.stderr, flush=True)


def log_tools_startup(tool_count: int, disallowed_tools: List[str]):
    """Log tool configuration on startup."""
    log_info(f"Tools available: {tool_count} total")
    if disallowed_tools:
        log_info(f"Disallowed tools: {', '.join(sorted(disallowed_tools))}")
    else:
        log_info("Disallowed tools: none")


def log_slack_event(event_type: str, event_ts: Optional[str], thread_ts: Optional[str] = None, additional_info: Optional[str] = None):
    """Log incoming Slack event."""
    info_parts = [f"Slack event: {event_type}"]
    if event_ts:
        info_parts.append(f"ts={event_ts}")
    if thread_ts:
        info_parts.append(f"thread_ts={thread_ts}")
    if additional_info:
        info_parts.append(additional_info)
    log_info(" | ".join(info_parts))


def log_session_created(session_id: str, thread_ts: str):
    """Log new agent session creation."""
    log_info(f"New agent session created | session_id={session_id} | thread_ts={thread_ts}")


def log_agent_message(message_type: str, block_types: List[str], thread_ts: Optional[str] = None, tool_name: Optional[str] = None, tool_use_id: Optional[str] = None):
    """Log message received from agent."""
    info_parts = [f"New message from agent | type={message_type}"]
    if block_types:
        block_types_str = ", ".join(block_types)
        info_parts.append(f"blocks=[{block_types_str}]")
    if thread_ts:
        info_parts.append(f"thread_ts={thread_ts}")
    if tool_name:
        info_parts.append(f"tool={tool_name}")
    if tool_use_id:
        info_parts.append(f"tool_use_id={tool_use_id}")
    log_info(" | ".join(info_parts))


def log_pre_tool_use(tool_name: str, thread_ts: str, tool_use_id: Optional[str] = None):
    """Log PreToolUse hook invocation."""
    info_parts = [f"PreToolUse hook invoked | tool={tool_name} | thread_ts={thread_ts}"]
    if tool_use_id:
        info_parts.append(f"tool_use_id={tool_use_id}")
    log_info(" | ".join(info_parts))


def log_post_tool_use(tool_name: str, thread_ts: str, tool_use_id: Optional[str] = None):
    """Log PostToolUse hook invocation."""
    info_parts = [f"PostToolUse hook invoked | tool={tool_name} | thread_ts={thread_ts}"]
    if tool_use_id:
        info_parts.append(f"tool_use_id={tool_use_id}")
    log_info(" | ".join(info_parts))


def log_slack_api_call(method: str, thread_ts: Optional[str] = None, ts: Optional[str] = None, additional_info: Optional[str] = None):
    """Log Slack API call."""
    info_parts = [f"Slack API call: {method}"]
    if ts:
        info_parts.append(f"ts={ts}")
    if thread_ts:
        info_parts.append(f"thread_ts={thread_ts}")
    if additional_info:
        info_parts.append(additional_info)
    log_info(" | ".join(info_parts))


def log_send_to_agent(thread_ts: str, message_preview: Optional[str] = None, message_length: Optional[int] = None):
    """Log when sending message to agent."""
    info_parts = [f"Sending to agent | thread_ts={thread_ts}"]
    if message_length is not None:
        info_parts.append(f"length={message_length}")
    if message_preview:
        info_parts.append(f"preview={message_preview}")
    log_info(" | ".join(info_parts))


def log_factoid_trigger(trigger: str, thread_ts: Optional[str] = None, mention_only: bool = False):
    """Log when a factoid is triggered."""
    info_parts = [f"Factoid triggered | trigger={trigger}"]
    if thread_ts:
        info_parts.append(f"thread_ts={thread_ts}")
    if mention_only:
        info_parts.append("mention_only=true")
    log_info(" | ".join(info_parts))


def log_factoid_cooldown(trigger: str):
    """Log when a factoid is blocked by cooldown."""
    log_info(f"Factoid blocked by cooldown | trigger={trigger}")


def log_factoid_reload(success: bool, factoid_count: int = 0, error: Optional[str] = None):
    """Log factoid reload attempts."""
    if success:
        info_parts = [f"Factoids reloaded | count={factoid_count}"]
        if error:
            info_parts.append(f"warnings={error}")
        log_info(" | ".join(info_parts))
    else:
        error_parts = [f"Factoid reload failed | count={factoid_count}"]
        if error:
            error_parts.append(f"error={error}")
        log_error(" | ".join(error_parts))

