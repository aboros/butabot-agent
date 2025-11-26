# Factoids Implementation Documentation

## Overview

Factoids are simple, configurable responses triggered by exact string matches in Slack messages. They provide a lightweight way to add quick responses without invoking Claude, making them ideal for common queries, greetings, or fun interactions.

## Architecture

Factoids are implemented as a separate layer in the bot architecture:

- **Factoid Layer** (`bot/factoids.py`): Manages factoid configuration, matching logic, cooldown tracking, and random response selection
- **Integration**: Factoid checking happens in `bot/app.py` before Claude processing, allowing factoids to intercept messages and respond immediately

**Key Design Decisions:**
- **Exact string matching**: Factoids match exact strings (case-sensitive) for predictable behavior
- **No thread creation**: Factoids respond in the same location (channel or thread) without creating new threads
- **Global cooldowns**: 30-second cooldown per trigger string across all channels/threads to prevent spam
- **Random selection**: Support for multiple responses per trigger with random selection
- **HTML entity handling**: Automatically handles Slack's HTML entity encoding (e.g., `&quest;` → `?`)

## Configuration

### File Location

Factoids are configured via a JSON file:
- **Docker**: `/app/factoids.json` (mounted from project root)
- **Local development**: `./factoids.json` (project root)
- **Example file**: `factoids.json.example` (committed to repository)

### JSON Format

```json
{
  "trigger_string": {
    "response": "Response text or array of responses",
    "mention_only": false
  }
}
```

**Properties:**
- `trigger_string`: Exact string to match (case-sensitive, supports special characters like `?`)
- `response`: Can be:
  - Single string: `"response": "Hello!"`
  - Array of strings: `"response": ["Hello!", "Hi there!", "Hey!"]` - bot randomly selects one
- `mention_only`: Boolean indicating if factoid only triggers on bot mentions
  - `false`: Triggers on any message containing the exact string
  - `true`: Only triggers when bot is mentioned (`@botname trigger_string`)

### Example Configuration

```json
{
  "hello": {
    "response": "Hi there! How can I help you?",
    "mention_only": false
  },
  "ping": {
    "response": ["pong", "pong!", "🏓"],
    "mention_only": false
  },
  "status": {
    "response": "All systems operational! ✅",
    "mention_only": true
  },
  "mivan?": {
    "response": "*MITMIVAN?!*",
    "mention_only": false
  }
}
```

## Behavior

### Matching Logic

1. **Exact match**: Factoids match exact strings (case-sensitive)
2. **HTML entity decoding**: Automatically handles Slack's HTML entity encoding
   - `&quest;` → `?`
   - `&amp;` → `&`
   - Other HTML entities are decoded as needed
3. **Whitespace handling**: Leading/trailing whitespace is stripped during matching
4. **Multiple combinations**: Checks both original and normalized versions of message text and factoid keys

### Trigger Modes

**Any Message (`mention_only: false`):**
- Triggers when the exact string appears anywhere in a message
- Works in channels, DMs, and threads
- Example: User types `"ping"` → bot responds with `"pong"`

**Mention Only (`mention_only: true`):**
- Only triggers when bot is mentioned AND message contains the exact string
- Bot mention is removed before matching
- Example: User types `"@bot status"` → bot responds with status message

### Cooldown System

- **Duration**: 30 seconds per trigger string
- **Scope**: Global across all channels and threads
- **Behavior**: If a factoid is triggered, the same trigger string cannot fire again anywhere in the workspace for 30 seconds
- **Purpose**: Prevents spam and abuse

### Response Selection

- **Single response**: Always returns the same response
- **Multiple responses**: Randomly selects one response from the array each time
- **Selection happens after cooldown check**: Cooldown is checked before random selection

### Thread Handling

**Critical**: Factoids never create new threads.

- **In channel**: Responds directly in the channel
- **In thread**: Responds in the same thread
- **Implementation**: Uses `thread_ts = event.get("thread_ts")` (None if not in thread) instead of `event.get("thread_ts") or event.get("ts")`

This differs from Claude responses, which create threads when needed.

## Integration Points

### Event Handlers

Factoids are checked in two event handlers:

1. **`handle_app_mention`** (`bot/app.py`):
   - Checks factoids before Claude processing
   - Uses `is_mention=True` for matching
   - If factoid matches, responds and returns early (skips Claude)

2. **`handle_message`** (`bot/app.py`):
   - Checks factoids in any message (not just mentions)
   - Uses `is_mention=False` for matching
   - Only processes if message is not from a bot

### Processing Order

```
1. Message received
2. Extract message text
3. Check factoids (if match → respond and exit)
4. If no factoid match → process with Claude
```

## Runtime Reloading

Factoids can be reloaded at runtime without restarting the bot:

```python
# In bot/app.py or via admin command
success = bot.factoid_manager.reload_factoids()
```

**Behavior:**
- Reloads from `factoids.json` file
- Validates all factoids before updating
- Logs errors for invalid factoids but continues with valid ones
- Preserves existing factoids if reload fails
- Cooldowns are preserved (not reset on reload)

**Note**: File watching or admin commands for automatic reloading can be added in the future.

## Error Handling

### File Not Found
- Logs warning
- Starts with empty factoids dict
- Bot continues to function normally

### Invalid JSON
- Logs error with details
- Preserves existing factoids
- Bot continues with previous configuration

### Invalid Factoid Format
- Logs error for specific factoid
- Skips invalid factoid
- Continues loading other factoids
- Example errors:
  - Non-string trigger
  - Non-dict config
  - Invalid response type (not string or array)
  - Empty response array
  - Non-boolean `mention_only`

## Logging

Factoid-related logging uses the centralized logger (`bot/logger.py`):

### Log Functions

- `log_factoid_trigger(trigger, thread_ts, mention_only)`: Logs when a factoid is triggered
- `log_factoid_cooldown(trigger)`: Logs when a factoid is blocked by cooldown
- `log_factoid_reload(success, factoid_count, error)`: Logs factoid reload attempts

### Example Log Entries

```
[INFO] Factoid triggered | trigger=ping | thread_ts=1763713315.607019
[INFO] Factoid blocked by cooldown | trigger=ping
[INFO] Factoids reloaded | count=5
[ERROR] Factoid reload failed | count=3 | error=Invalid response for 'bad_factoid': must be a string or array of strings
```

## Code Structure

### FactoidManager Class

**Location**: `bot/factoids.py`

**Key Methods:**
- `__init__(factoids_file)`: Initialize and load factoids
- `load_factoids()`: Load/reload factoids from JSON file
- `reload_factoids()`: Public method for runtime reloading
- `check_factoid(message_text, is_mention)`: Check if message matches a factoid
- `_is_on_cooldown(trigger)`: Check if trigger is on cooldown
- `_record_trigger(trigger)`: Record trigger timestamp for cooldown

**Internal State:**
- `_factoids`: Dict mapping trigger strings to factoid configs
- `_cooldowns`: Dict mapping trigger strings to last trigger timestamps

### Integration in app.py

**Initialization:**
```python
self.factoid_manager = FactoidManager()
```

**In handle_app_mention:**
```python
# Check for factoids before Claude processing
factoid_response = self.factoid_manager.check_factoid(user_message, is_mention=True)
if factoid_response is not None:
    # Send factoid response and return early
    # Uses factoid_thread_ts (no thread creation)
```

**In handle_message:**
```python
# Check for factoids in any message
factoid_response = self.factoid_manager.check_factoid(message_text, is_mention=False)
if factoid_response is not None:
    # Send factoid response
    # Uses thread_ts from event (no thread creation)
```

## Special Character Handling

Factoids support special characters in trigger strings, including:
- Question marks: `"mivan?"`
- Exclamation marks: `"hello!"`
- Other punctuation: `"what's up?"`
- Unicode characters: `"café"`, `"naïve"`

**HTML Entity Decoding:**
- Slack may encode special characters as HTML entities
- FactoidManager automatically decodes them during matching
- Both original and decoded versions are checked

## Best Practices

### When to Use Factoids

✅ **Good use cases:**
- Common greetings (`"hello"`, `"hi"`)
- Status checks (`"status"`, `"ping"`)
- Fun interactions (`"botsnack"`, `"mivan?"`)
- Quick answers that don't need Claude's reasoning

❌ **Avoid for:**
- Complex queries that need reasoning
- Context-dependent responses
- Responses that need to vary based on conversation history

### Configuration Tips

1. **Use arrays for variety**: Multiple responses make interactions more engaging
2. **Set appropriate `mention_only`**: Use `true` for commands, `false` for casual triggers
3. **Test special characters**: Verify triggers with `?`, `!`, etc. work correctly
4. **Keep responses concise**: Factoids should be quick, not lengthy explanations

### Security Considerations

- **No code execution**: Factoids are static responses, no code execution
- **No user input**: Responses don't include user input (exact match only)
- **Cooldown protection**: Prevents abuse via global cooldowns
- **File validation**: JSON is validated before loading

## Troubleshooting

### Factoid Not Triggering

1. **Check exact match**: Factoids are case-sensitive and require exact match
2. **Check `mention_only`**: Verify the factoid's `mention_only` setting matches how it's being triggered
3. **Check cooldown**: Verify the trigger isn't on cooldown (30 seconds)
4. **Check special characters**: Ensure HTML entity encoding isn't causing issues
5. **Check logs**: Look for `log_factoid_trigger` or `log_factoid_cooldown` entries

### Factoid Creating Threads

- **Should not happen**: Factoids use `event.get("thread_ts")` (not `or event.get("ts")`)
- **Check implementation**: Verify `handle_app_mention` uses `factoid_thread_ts` for factoid responses

### Reload Not Working

1. **Check file path**: Verify `factoids.json` exists at expected location
2. **Check JSON validity**: Validate JSON syntax
3. **Check logs**: Look for `log_factoid_reload` entries with error details
4. **Check permissions**: Ensure file is readable

## Future Enhancements

Potential future improvements:
- File watching for automatic reloading
- Admin commands for adding/removing factoids
- Per-channel factoid configuration
- Factoid usage statistics
- Case-insensitive matching option
- Regex pattern matching support

