# Logging Implementation Documentation

## Overview

The Butabot Agent uses a structured logging system that provides informative, consistent log entries for debugging and monitoring. All logs are written to `stderr` with immediate flushing to ensure they appear in Docker logs in real-time.

## Log Format

All log entries follow a consistent structure:
```
[LEVEL] Category | key1=value1 | key2=value2 | ...
```

- **Level**: `INFO`, `WARNING`, or `ERROR`
- **Category**: Describes the type of event (e.g., "Slack event", "New message from agent")
- **Key-value pairs**: Pipe-separated (`|`) for easy parsing and filtering

## Log Levels

- **INFO**: Normal operational events (events, messages, API calls)
- **WARNING**: Non-critical issues (e.g., API errors detected in responses)
- **ERROR**: Errors that require attention (exceptions, failed operations)

## Log Entry Types

### 1. Startup Logging

#### Tools Available
Logged when the first SystemMessage with tools is received from the Agent SDK.

```
[INFO] Tools available: 47 total
[INFO] Disallowed tools: Bash, BashOutput, ExitPlanMode, KillBash, NotebookEdit, Task, TodoWrite
```

**Location**: `bot/claude_client.py` - `send_message()` method  
**When**: First SystemMessage with tools received

---

### 2. Slack Event Logging

#### Incoming Slack Events
Logged for all incoming Slack events (app mentions, messages, button clicks).

```
[INFO] Slack event: app_mention | ts=1763713315.607019
[INFO] Slack event: message | ts=1763713315.607019
[INFO] Slack event: tool_approve | ts=1763713324.967689 | thread_ts=1763713315.607019 | tool=mcp__google-maps__maps_distance_matrix | tool_use_id=toolu_01ABC123
[INFO] Slack event: tool_deny | ts=1763713325.123456 | thread_ts=1763713315.607019 | tool=mcp__drupal__tools_system_status | tool_use_id=toolu_01XYZ789
```

**Fields**:
- `event_type`: Type of Slack event (app_mention, message, tool_approve, tool_deny)
- `ts`: Event timestamp
- `thread_ts`: Thread timestamp (when applicable)
- `tool`: Tool name (for tool_approve/tool_deny events)
- `tool_use_id`: Tool use ID (for tool_approve/tool_deny events)

**Location**: `bot/app.py` - Event handlers

---

### 3. Agent Communication Logging

#### Sending to Agent
Logged when sending a message to Claude.

```
[INFO] Sending to agent | thread_ts=1763713315.607019 | length=45 | preview=What's the distance between New York and Los Angeles?
```

**Fields**:
- `thread_ts`: Slack thread timestamp
- `length`: Message length in characters
- `preview`: First 100 characters of the message (truncated if longer)

**Location**: `bot/claude_client.py` - `send_message()` method

#### Messages from Agent
Logged when receiving messages from Claude.

```
[INFO] New message from agent | type=AssistantMessage | blocks=[TextBlock] | thread_ts=1763713315.607019
[INFO] New message from agent | type=AssistantMessage | blocks=[ToolUseBlock] | thread_ts=1763713315.607019 | tool=mcp__google-maps__maps_distance_matrix | tool_use_id=toolu_01ABC123
```

**Fields**:
- `type`: Message type (AssistantMessage)
- `blocks`: List of block types in the message (TextBlock, ToolUseBlock, ToolResultBlock)
- `thread_ts`: Slack thread timestamp
- `tool`: Tool name (when ToolUseBlock is present)
- `tool_use_id`: Tool use ID (when ToolUseBlock is present)

**Location**: `bot/claude_client.py` and `bot/app.py` - Message processing

---

### 4. Session Management Logging

#### New Agent Client Created
Logged when a new ClaudeSDKClient is created for a thread.

```
[INFO] New agent client created for thread | thread_ts=1763713315.607019
```

**Location**: `bot/claude_client.py` - `_get_or_create_client()` method

#### New Agent Session Created
Logged when a new session is created (when ResultMessage is received).

```
[INFO] New agent session created | session_id=69dc03cd-f664-4bab-873d-1a7ed38b2615 | thread_ts=1763713315.607019
```

**Fields**:
- `session_id`: Agent SDK session ID (provided by SDK)
- `thread_ts`: Slack thread timestamp

**Location**: `bot/claude_client.py` - `send_message()` method

---

### 5. Tool Use Hook Logging

#### PreToolUse Hook Invoked
Logged when a tool use is requested before execution.

```
[INFO] PreToolUse hook invoked | tool=mcp__google-maps__maps_distance_matrix | thread_ts=1763713315.607019 | tool_use_id=toolu_01ABC123
```

**Fields**:
- `tool`: Tool name
- `thread_ts`: Slack thread timestamp
- `tool_use_id`: Tool use ID

**Location**: `bot/claude_client.py` - `_create_feedback_hooks()` method

#### PostToolUse Hook Invoked
Logged after tool execution completes.

```
[INFO] PostToolUse hook invoked | tool=mcp__google-maps__maps_distance_matrix | thread_ts=1763713315.607019 | tool_use_id=toolu_01ABC123
```

**Fields**:
- `tool`: Tool name
- `thread_ts`: Slack thread timestamp
- `tool_use_id`: Tool use ID

**Location**: `bot/claude_client.py` - `_create_feedback_hooks()` method

---

### 6. Slack API Call Logging

#### Slack API Calls
Logged for all Slack API calls (chat_postMessage, chat_update, say).

```
[INFO] Slack API call: say | thread_ts=1763713315.607019 | type=thinking
[INFO] Slack API call: say | thread_ts=1763713315.607019 | ts=1763713316.123456 | type=thinking
[INFO] Slack API call: chat_postMessage | thread_ts=1763713315.607019 | type=approval_request | tool=mcp__google-maps__maps_distance_matrix | tool_use_id=toolu_01ABC123
[INFO] Slack API call: chat_postMessage | thread_ts=1763713315.607019 | ts=1763713320.789012 | type=approval_request | tool=mcp__google-maps__maps_distance_matrix | tool_use_id=toolu_01ABC123
[INFO] Slack API call: chat_update | thread_ts=1763713315.607019 | ts=1763713324.967689 | type=approval | tool=mcp__google-maps__maps_distance_matrix | tool_use_id=toolu_01ABC123
[INFO] Slack API call: chat_update | thread_ts=1763713315.607019 | ts=1763713316.123456 | type=response
[INFO] Slack API call: say | thread_ts=1763713315.607019 | type=error
```

**Fields**:
- `method`: API method (say, chat_postMessage, chat_update)
- `thread_ts`: Slack thread timestamp
- `ts`: Message timestamp (from API response, when available)
- `type`: Message type (thinking, response, error, hello, feedback, approval_request, approval, denial, timeout, approval_result, completion)
- `tool`: Tool name (for tool-related messages)
- `tool_use_id`: Tool use ID (for tool-related messages)

**Location**: `bot/app.py` and `bot/tool_approval.py` - All Slack API call sites

**Note**: Some API calls log twice - once before the call (without `ts`) and once after (with `ts` from the response).

---

## Logging Module

The logging functionality is centralized in `bot/logger.py` with the following functions:

### Core Logging Functions
- `log_info(message: str)`: Log INFO level message
- `log_warning(message: str)`: Log WARNING level message
- `log_error(message: str)`: Log ERROR level message

### Structured Logging Functions
- `log_tools_startup(tool_count: int, disallowed_tools: List[str])`: Log tool configuration
- `log_slack_event(event_type: str, event_ts: Optional[str], thread_ts: Optional[str] = None, additional_info: Optional[str] = None)`: Log Slack events
- `log_send_to_agent(thread_ts: str, message_preview: Optional[str] = None, message_length: Optional[int] = None)`: Log messages sent to agent
- `log_agent_message(message_type: str, block_types: List[str], thread_ts: Optional[str] = None, tool_name: Optional[str] = None, tool_use_id: Optional[str] = None)`: Log messages from agent
- `log_session_created(session_id: str, thread_ts: str)`: Log session creation
- `log_pre_tool_use(tool_name: str, thread_ts: str, tool_use_id: Optional[str] = None)`: Log PreToolUse hook
- `log_post_tool_use(tool_name: str, thread_ts: str, tool_use_id: Optional[str] = None)`: Log PostToolUse hook
- `log_slack_api_call(method: str, thread_ts: Optional[str] = None, ts: Optional[str] = None, additional_info: Optional[str] = None)`: Log Slack API calls

## Usage Examples

### Filtering Logs

#### View all tool-related events:
```bash
docker compose logs | grep "tool="
```

#### View all events for a specific thread:
```bash
docker compose logs | grep "thread_ts=1763713315.607019"
```

#### View all Slack API calls:
```bash
docker compose logs | grep "Slack API call"
```

#### View all tool use hooks:
```bash
docker compose logs | grep "hook invoked"
```

#### View all errors:
```bash
docker compose logs | grep "\[ERROR\]"
```

### Tracking a Complete Tool Use Flow

To track a complete tool use flow, filter by `tool_use_id`:

```bash
docker compose logs | grep "toolu_01ABC123"
```

This will show:
1. PreToolUse hook invoked
2. Slack event: tool_approve (if approved)
3. Slack API call: chat_postMessage (approval request)
4. Slack API call: chat_update (approval result)
5. PostToolUse hook invoked
6. New message from agent (with ToolUseBlock)

## Implementation Details

### Log Output
- All logs are written to `stderr` with `flush=True` to ensure immediate output in Docker logs
- Logs use structured format with pipe-separated key-value pairs for easy parsing
- No sensitive data (API keys, tokens) is logged

### Performance Considerations
- Logging is lightweight and non-blocking
- Message previews are truncated to 100 characters to avoid log bloat
- Tool names and IDs are included for correlation but don't add significant overhead

### Error Handling
- Errors in logging functions won't crash the application
- If logging fails, the operation continues (logging is best-effort)

## Best Practices

1. **Use structured logging**: Always use the provided logging functions rather than raw `print()` statements
2. **Include context**: Always include `thread_ts` and relevant IDs (`tool_use_id`, `session_id`) for correlation
3. **Keep previews short**: Message previews are automatically truncated to 100 characters
4. **Log before and after**: For API calls, log both before (request) and after (response with `ts`) when possible
5. **Use appropriate levels**: Use `INFO` for normal operations, `WARNING` for non-critical issues, `ERROR` for failures

## Future Enhancements

Potential improvements to the logging system:

1. **Structured JSON logging**: Option to output logs in JSON format for better parsing
2. **Log rotation**: Implement log rotation for long-running instances
3. **Metrics collection**: Extract metrics from logs (tool usage, response times, etc.)
4. **Log aggregation**: Integration with log aggregation services (e.g., ELK stack, Datadog)
5. **Correlation IDs**: Add correlation IDs to track requests across services
6. **Performance logging**: Add timing information for API calls and tool execution

