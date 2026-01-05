# Multi-Platform Bot Architecture (fast-agent Core)

## Overview

This architecture enables a single bot implementation to operate across multiple chat platforms (Slack, Discord, and future platforms) with full tool visibility, approval workflows, and minimal custom code maintenance.

## Why fast-agent Hooks Are the Right Solution

### The Alternative: Direct Messages API

Without fast-agent, you would need to:
1. Manually implement the entire agentic loop (tool detection, execution, result handling)
2. Write custom MCP client code for 5-6 servers
3. Handle tool discovery and management for 90 tools
4. Implement conversation history management
5. Handle multi-modal content (files, images, PDFs)
6. Write error handling and retry logic
7. Manage LLM provider specifics

**Estimated custom code: 2,000+ lines**

### With fast-agent Hooks

fast-agent handles all the complexity above. You only write:
1. Custom agent class with hooks (~200-300 lines)
2. Platform connectors (~50 lines each)
3. Configuration files (YAML)

**Total custom code: ~400-500 lines**

### Key Advantages

**1. Less Code = Less Bugs**
- 75% reduction in custom code
- Framework-tested orchestration logic
- Community-validated patterns

**2. Built-in Tool Management**
- Automatic tool discovery from MCP servers
- Smart tool selection (with 90 tools, this matters)
- Tool filtering per agent
- Multi-modal content handling

**3. Future-Proof**
- Framework updates improve your bot automatically
- MCP ecosystem compatibility
- New workflow patterns available immediately (chaining, parallelism, orchestration)

**4. Hooks Provide Exactly What You Need**
- ✅ Async operations (wait for user approval)
- ✅ Execution blocking (raise exceptions to deny)
- ✅ MCP tool compatibility (all 90 tools)
- ✅ Platform-agnostic (hooks don't know about Slack/Discord)

**5. Clean Separation of Concerns**
- Platform logic: Connectors (receive/send messages)
- Tool control: Hooks (report, approve, block)
- AI orchestration: fast-agent core (everything else)

## Architecture Layers

### 1. Platform Connectors (Minimal Custom Code)

**Purpose:** Translate platform-specific events into standardized messages and route responses back.

**Components:**
- **SlackConnector:** Webhook handler → extract message → forward to core
- **DiscordConnector:** Event handler → extract message → forward to core
- **Future Connectors:** Teams, WhatsApp, etc.

**Scope:** Each connector ~50 lines
- Receive platform events
- Normalize message format
- Send to Tool Flow Layer
- Return response to platform

### 2. Tool Flow Orchestration Layer (Custom Code - Required)

**Purpose:** Provide full visibility and control over AI tool execution with approval workflows.

**Implementation Approach:** Custom Agent class extending `ToolAgent` with `ToolRunnerHookCapable`

**Responsibilities:**
- Implements fast-agent's `ToolRunnerHooks`:
  - `before_tool_call`: Report tool usage, check approval rules, block execution if denied
  - `after_tool_call`: Report tool results (success/failure)
  - `before_llm_call`: (optional) for additional context injection
  - `after_llm_call`: (optional) for response processing
- Reports tool usage: "🔧 Using tool X with parameters Y"
- Implements approval logic:
  - Check if tool requires approval (based on configuration)
  - Send approval request to chat platform (async)
  - Wait for user response (approve/deny)
  - Block tool execution by raising exception if denied
- Reports results: "✅ Tool X returned result" or "❌ Tool X failed"
- Maintains approval rules per tool (configurable)
- Handles asynchronous approval flows across concurrent conversations

**Scope:** ~200-300 lines for full tool lifecycle management

**Confirmed Capabilities:**
- ✅ Hooks support `async def` for asynchronous operations (waiting for user approval)
- ✅ Hooks work with MCP tools (Tool Runner powers "ToolAgent and MCP agents")
- ✅ Tool execution can be blocked by raising exceptions in `before_tool_call`
- ✅ The `runner` parameter provides control over execution flow

**Key Features:**
- Platform-agnostic: Works identically across all chat platforms
- Configurable approval rules per tool
- Complete audit trail of all tool interactions
- User-friendly status reporting in chat

### 3. Bot Core (fast-agent Framework)

**Purpose:** Handle all AI orchestration, MCP tool management, and conversation logic.

**Components:**
- Single `@fast.agent` definition with all MCP servers configured
- Handles 90 tools across 5-6 MCP servers
- Manages conversation history and context
- Multi-modal content support (files, images, PDFs)
- Error handling and retry logic

**Configuration:**
- Defined in `fastagent.config.yaml`
- Model selection and parameters
- MCP server definitions

**API:**
- Programmatic interface: `await agent.send(message)` returns response
- No CLI interaction required
- Embeddable in any Python application

## Configuration Files

### `fastagent.config.yaml`
- MCP server definitions (all 5-6 servers)
- Model settings (Anthropic, OpenAI, etc.)
- Agent behavior parameters

### `fastagent.secrets.yaml`
- API keys for LLM providers
- MCP server credentials

### `approval_rules.yaml` (New)
- Tool-specific approval requirements
- Configurable per tool or tool category
- Example: Git push requires approval, read operations don't

### Environment Variables
- Platform tokens (Slack bot token, Discord bot token)
- Deployment-specific settings

## Data Flow

```
User Message (Slack/Discord)
    ↓
Platform Connector
    ↓
Tool Flow Orchestration Layer
    ├─→ Report: "Thinking..."
    ↓
fast-agent Core (agent.send())
    ├─→ Tool call detected
    ↓
Tool Flow Layer intercepts
    ├─→ Report: "🔧 Using tool X"
    ├─→ Check approval rules
    ├─→ If required: Request approval, wait for response
    ├─→ Execute or cancel based on approval
    ├─→ Report: "✅ Result received"
    ↓
Continue agent loop until complete
    ↓
Final response
    ↓
Platform Connector
    ↓
User sees response (Slack/Discord)
```

## Key Benefits

### Minimal Custom Code
- **~400-500 lines total custom code**
- Platform Connectors: ~150 lines (50 per platform)
- Tool Flow Layer: ~200-300 lines (custom agent class with hooks)
- Configuration: YAML files
- Everything else: fast-agent handles

### Full Visibility
- Every tool interaction reported to chat
- Users see what the bot is doing in real-time
- Complete transparency in AI operations

### Safety & Control
- Dangerous operations require explicit approval
- Configurable approval rules per tool
- Users maintain control over high-impact actions

### Platform Agnostic
- Core logic works identically across all platforms
- Adding new platforms: just a new 50-line connector
- Tool flow works the same everywhere

### MCP Ecosystem
- All 90 existing tools work immediately
- Easy to add new MCP servers
- Standard protocol for tool integration

### Maintainability
- fast-agent handles complex orchestration
- Updates to fast-agent improve all functionality
- Less custom code = fewer bugs

## Migration Strategy

### Phase 1: Extract & Generalize Tool Flow Logic
- Extract tool flow logic from current Slack bot
- Implement as `ToolRunnerHooks` in custom agent class extending `ToolAgent`
- Make it platform-agnostic (remove Slack-specific code)
- Create `approval_rules.yaml` configuration
- Implement approval workflow:
  - `before_tool_call`: Check rules, request approval (async), raise exception if denied
  - `after_tool_call`: Report results to platform

### Phase 2: Build Discord Integration (New Platform)
- Implement `DiscordConnector` (~50 lines)
  - Webhook/event handler
  - Message normalization
  - Forward to custom agent with hooks
  - Route responses back
- Connect to custom agent with tool flow hooks
- Test with subset of tools
- Validate approval workflow end-to-end
- Verify reporting in Discord

### Phase 3: Migrate Slack Integration
- Implement new `SlackConnector` (~50 lines)
  - Replace old Slack-specific bot
  - Use same pattern as DiscordConnector
- Connect to shared custom agent with hooks
- Run both Slack and Discord in parallel (validation)
- Delete old custom orchestration code
- Verify feature parity with old bot

### Phase 4: Optimize & Scale
- Fine-tune approval rules based on usage patterns
- Add reporting enhancements (better status messages, threading)
- Performance optimization if needed
- Add additional platforms (Teams, WhatsApp, etc.)
- Consider workflow patterns (chaining, parallelism) for complex tasks

## Technical Stack

- **Language:** Python 3.13+
- **Framework:** fast-agent-mcp (with ToolRunnerHooks)
- **MCP Servers:** 5-6 existing servers (GitLab, Slack, Filesystem, etc.)
- **Platforms:** Slack, Discord (extensible to others)
- **LLM Provider:** Anthropic Claude (configurable to others)
- **Deployment:** Compatible with existing infrastructure (DigitalOcean, Docker, etc.)

## Implementation Details

### Tool Flow Hooks Implementation

The Tool Flow Layer extends fast-agent's `ToolAgent` class and implements `ToolRunnerHookCapable` to intercept and control tool execution:

```python
class ChatPlatformToolAgent(ToolAgent, ToolRunnerHookCapable):
    def __init__(self, config, platform_interface, approval_rules):
        super().__init__(config, tools, context)
        self.platform = platform_interface
        self.approval_rules = approval_rules
        self._hooks = ToolRunnerHooks(
            before_tool_call=self._handle_tool_approval,
            after_tool_call=self._report_tool_result
        )
    
    @property
    def tool_runner_hooks(self):
        return self._hooks
    
    async def _handle_tool_approval(self, runner, tool_call):
        # Report to platform
        await self.platform.send(f"🔧 Using tool: {tool_call.name}")
        await self.platform.send(f"Parameters: {tool_call.input}")
        
        # Check approval rules
        if self.approval_rules.requires_approval(tool_call.name):
            # Request approval from user (async - waits for response)
            approved = await self.platform.request_approval(
                tool_name=tool_call.name,
                parameters=tool_call.input
            )
            
            if not approved:
                # Block execution by raising exception
                raise ToolApprovalDenied(f"User denied: {tool_call.name}")
    
    async def _report_tool_result(self, runner, message):
        if message.tool_results:
            tool_names = ", ".join(message.tool_results.keys())
            await self.platform.send(f"✅ Completed: {tool_names}")
```

### Hook Capabilities (Confirmed)

Based on fast-agent documentation:

1. **Async Support**: Hooks use `async def`, enabling waiting for external input (user approval)
2. **Execution Control**: Raising exceptions in `before_tool_call` blocks tool execution
3. **MCP Compatibility**: Tool Runner powers both ToolAgent and MCP agents - all 90 MCP tools supported
4. **Runner Access**: The `runner` parameter provides execution flow control (e.g., `runner.append_messages()`)

### Approval Workflow Example

```python
# User says: "Delete all files in /tmp"
# Bot detects tool: filesystem.delete_directory

# 1. before_tool_call hook triggers
#    → Posts to Slack: "🔧 Using tool: delete_directory"
#    → Checks approval_rules.yaml: delete_directory = requires_approval
#    → Posts to Slack: "⚠️ Approve deletion? [Yes] [No]"
#    → Waits for user response (async)

# 2a. If user clicks [Yes]:
#     → Tool executes
#     → after_tool_call hook: "✅ Completed: delete_directory"

# 2b. If user clicks [No]:
#     → Raises ToolApprovalDenied
#     → Bot responds: "Tool execution denied by user"
```

## Future Extensibility

### Easy Additions
- New chat platforms (Teams, WhatsApp, Telegram)
- Additional MCP servers/tools
- Workflow patterns (chaining, parallelism, orchestration)
- Custom approval flows (multi-approver, time-based)

### Advanced Features (When Needed)
- Tool usage analytics
- Performance monitoring
- A/B testing different prompts
- Fine-tuning approval thresholds based on usage patterns

## Success Metrics

- **Code Reduction:** From thousands of lines to ~500 lines
- **Platform Time:** New platform support in <1 day
- **Reliability:** fast-agent handles edge cases
- **User Experience:** Full visibility and control
- **Maintenance:** Framework updates improve bot automatically

## Common Implementation Challenges

### Challenge 1: Exception Handling in Hooks

**Problem:** How to handle tool denial gracefully?

**Solution:** Define custom exception and catch it in your connector:

```python
class ToolApprovalDenied(Exception):
    pass

# In hook:
if not approved:
    raise ToolApprovalDenied(f"User denied: {tool_name}")

# In platform connector:
try:
    response = await agent.send(message)
except ToolApprovalDenied as e:
    await platform.send(f"❌ {e}")
```

### Challenge 2: Concurrent Approval Requests

**Problem:** Multiple users trigger tools simultaneously.

**Solution:** Track approvals per conversation/session:

```python
class ApprovalTracker:
    def __init__(self):
        self.pending = {}  # conversation_id -> approval_future
    
    async def request_approval(self, conversation_id, tool_call):
        future = asyncio.Future()
        self.pending[conversation_id] = future
        await platform.send_approval_request(conversation_id, tool_call)
        return await future  # Waits until user responds
    
    def resolve(self, conversation_id, approved):
        if conversation_id in self.pending:
            self.pending[conversation_id].set_result(approved)
```

### Challenge 3: Timeout for Approvals

**Problem:** User never responds to approval request.

**Solution:** Use `asyncio.wait_for` with timeout:

```python
async def _handle_tool_approval(self, runner, tool_call):
    if self.approval_rules.requires_approval(tool_call.name):
        try:
            approved = await asyncio.wait_for(
                self.platform.request_approval(tool_call),
                timeout=300  # 5 minutes
            )
            if not approved:
                raise ToolApprovalDenied()
        except asyncio.TimeoutError:
            raise ToolApprovalDenied("Approval timeout")
```

### Challenge 4: Tool Result Size

**Problem:** Some tool results are huge (e.g., file contents).

**Solution:** Truncate or summarize in reporting hook:

```python
async def _report_tool_result(self, runner, message):
    if message.tool_results:
        for tool_name, result in message.tool_results.items():
            result_preview = str(result)[:200]
            if len(str(result)) > 200:
                result_preview += "... (truncated)"
            await self.platform.send(f"✅ {tool_name}: {result_preview}")
```

## References & Resources

- **fast-agent Documentation:** https://fast-agent.ai/
- **MCP Specification:** https://modelcontextprotocol.io/
- **Tool Runner Hooks:** https://fast-agent.ai/agents/tool_runner/#hooks-optional
- **Anthropic Messages API:** https://docs.anthropic.com/en/api/messages
- **Building Effective Agents:** https://www.anthropic.com/research/building-effective-agents
