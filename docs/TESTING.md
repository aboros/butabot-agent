# Testing Guide

This guide explains how to set up and manually test the Butabot Agent multi-platform bot implementation.

## First-Time Setup

### Prerequisites

- Docker and Docker Compose installed
- Slack workspace with bot app created (for Slack testing)
- Discord bot application created (for Discord testing)
- Anthropic API key

### Step 1: Environment Configuration

1. **Create `.env` file** in the project root:

```bash
cp .env.example .env  # If .env.example exists, otherwise create manually
```

2. **Configure environment variables** in `.env`:

```bash
# Anthropic Configuration (Required)
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Slack Configuration (Required for Slack testing)
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_SIGNING_SECRET=your-signing-secret-here
SLACK_APP_TOKEN=xapp-your-app-token-here

# Discord Configuration (Required for Discord testing)
DISCORD_TOKEN=your-discord-bot-token-here

# Server Configuration
PORT=3000
```

3. **Create `fastagent.secrets.yaml`** (copy from example):

```bash
cp config/fastagent.secrets.yaml.example config/fastagent.secrets.yaml
```

4. **Configure `config/fastagent.secrets.yaml`**:

```yaml
ANTHROPIC_API_KEY: "${ANTHROPIC_API_KEY}"
SLACK_BOT_TOKEN: "${SLACK_BOT_TOKEN}"
DISCORD_TOKEN: "${DISCORD_TOKEN}"
```

**Important:** Fast-agent automatically loads `fastagent.config.yaml` and `fastagent.secrets.yaml` from the project root (not from the `config/` directory). These files are automatically copied to the root during Docker build, or mounted there in docker-compose for development.

### Step 2: Configuration Files Setup

1. **Configure MCP servers** (optional, for tool testing):

   - Create or update `.mcp.json` in project root
   - Add MCP server configurations (e.g., filesystem server)

2. **Configure approval rules**:

   - Edit `config/approval_rules.yaml` to set tool approval requirements
   - Example:
     ```yaml
     rules:
       filesystem_read_file: false
       filesystem_write_file: true
     ```

3. **Configure factoids** (optional):

   - Ensure `factoids.json` exists in project root
   - Add test factoids if desired

### Step 3: Build Docker Image

```bash
# Build the Docker image
docker compose build

# Verify build succeeded
docker compose config
```

### Step 4: Start the Bot

```bash
# Start in foreground (to see logs)
docker compose up

# Or start in background
docker compose up -d

# View logs
docker compose logs -f bot

# Check container status
docker compose ps
```

### Step 5: Verify Startup

Check logs for successful initialization:

```bash
docker compose logs bot | grep -i "started\|ready\|error"
```

You should see:
- Configuration loaded successfully
- Connectors initialized
- Agent initialized (if fast-agent-mcp is configured)
- No critical errors

## Testing Checklist

Use this checklist to systematically test all major functionality:

### Configuration System

- [ ] **Configuration Loading**
  - [ ] Verify `fastagent.config.yaml` exists in project root (copied from `config/` during Docker build)
  - [ ] Verify `fastagent.secrets.yaml` exists in project root (copied from `config/` during Docker build)
  - [ ] Verify fast-agent loads config automatically when agent is instantiated (check logs for no errors)
  - [ ] Verify `config/approval_rules.yaml` loads correctly (custom config for approval system)

  **Test Commands:**
  ```bash
  # Verify approval rules load (custom config)
  docker compose exec bot python -c "from config.loader import load_approval_rules; print('Rules:', load_approval_rules())"
  
  # Verify fast-agent config files exist in root
  docker compose exec bot ls -la /app/fastagent.config.yaml /app/fastagent.secrets.yaml
  
  # Verify fast-agent can load config (will happen when agent is created)
  docker compose exec bot python -c "from pathlib import Path; import yaml; print('Config file exists:', Path('/app/fastagent.config.yaml').exists())"
  ```

### Platform Connectors

- [ ] **Slack Connector**
  - [ ] Bot appears online in Slack workspace
  - [ ] Bot responds to mentions (`@botname`)
  - [ ] Bot sends messages to Slack channels
  - [ ] Messages are sent in threads correctly
  - [ ] Thread context is maintained

  **Test Steps:**
  1. Mention the bot in a Slack channel: `@botname hello`
  2. Verify bot responds (may need agent integration for full responses)
  3. Reply in thread - verify bot maintains context

- [ ] **Discord Connector**
  - [ ] Bot appears online in Discord server
  - [ ] Bot responds to mentions
  - [ ] Bot sends messages to Discord channels
  - [ ] DM functionality works (if implemented)
  - [ ] Thread handling works correctly

  **Test Steps:**
  1. Mention the bot in a Discord channel: `@botname hello`
  2. Verify bot responds
  3. Test in different channels/DMs

### Approval System

- [ ] **ApprovalRulesManager**
  - [ ] Rules load from `config/approval_rules.yaml`
  - [ ] `requires_approval()` returns correct values
  - [ ] Glob pattern matching works (e.g., `filesystem/*`)
  - [ ] Default behavior (no approval) works for unlisted tools

  **Test Command:**
  ```bash
  docker compose exec bot python -c "from approval.rules_manager import ApprovalRulesManager; rm = ApprovalRulesManager(); print('Test tool requires approval:', rm.requires_approval('test_tool')); print('Filesystem read requires approval:', rm.requires_approval('filesystem_read_file'))"
  ```

- [ ] **ApprovalTracker**
  - [ ] Can create approval requests
  - [ ] Approval requests have unique IDs
  - [ ] Can resolve approvals (approve/deny)
  - [ ] Timeout handling works correctly
  - [ ] Cleanup removes expired approvals

  **Test Command:**
  ```bash
  docker compose exec bot python -c "
  import asyncio
  from approval.approval_tracker import ApprovalTracker
  async def test():
      tracker = ApprovalTracker()
      approval_id = await tracker.create_approval('thread1', 'test_tool', {}, 'tool_use_1')
      print(f'Created approval: {approval_id}')
      await tracker.resolve_approval(approval_id, True)
      print('Resolved approval')
  asyncio.run(test())
  "
  ```

- [ ] **Approval Workflow Integration**
  - [ ] Approval buttons appear in Slack (if implemented)
  - [ ] Approval buttons appear in Discord (if implemented)
  - [ ] Clicking approve/deny resolves the approval
  - [ ] Tool execution is blocked when denied
  - [ ] Tool execution proceeds when approved

### Factoid System

- [ ] **FactoidManager**
  - [ ] Factoids load from `factoids.json`
  - [ ] Factoid triggers work correctly
  - [ ] Cooldown mechanism works (30 seconds)
  - [ ] Random response selection works (if multiple responses)
  - [ ] `mention_only` flag works correctly

  **Test Steps:**
  1. Send a message matching a factoid trigger
  2. Verify factoid response is returned
  3. Send same trigger again immediately - should be on cooldown
  4. Wait 30+ seconds and send again - should trigger again

  **Test Command:**
  ```bash
  docker compose exec bot python -c "
  from bot.factoids import FactoidManager
  fm = FactoidManager()
  result = fm.check_factoid('test_trigger', is_mention=True)
  print('Factoid result:', result)
  "
  ```

### Agent Integration

- [ ] **Agent Configuration**
  - [ ] Fast-agent automatically loads config from `fastagent.config.yaml` in project root
  - [ ] Fast-agent automatically loads secrets from `fastagent.secrets.yaml` in project root
  - [ ] MCP servers are configured correctly in config file
  - [ ] Model configuration is correct in config file
  - [ ] Config files are copied to root during Docker build (or mounted in docker-compose)

  **Test Commands:**
  ```bash
  # Verify config files exist in project root (where fast-agent expects them)
  docker compose exec bot ls -la /app/fastagent.config.yaml /app/fastagent.secrets.yaml
  
  # Verify config file structure is valid
  docker compose exec bot python -c "import yaml; print('Config valid:', yaml.safe_load(open('/app/fastagent.config.yaml')))"
  ```

- [ ] **Fast-Agent Instance Creation**
  - [ ] `create_agent_from_config()` creates agent instance
  - [ ] Fast-agent automatically loads config when `fast.run()` is called
  - [ ] Agent context manager works correctly
  - [ ] Agent can be used to process messages
  - [ ] MCP servers connect (if configured)
  - [ ] Tool discovery works

  **Test Command:**
  ```bash
  docker compose exec bot python -c "
  import asyncio
  from agent.agent_config import create_agent_from_config
  async def test():
      # Fast-agent automatically loads config from fastagent.config.yaml in project root
      async with await create_agent_from_config() as agent:
          response = await agent('Hello!')
          print('Agent response:', response)
  asyncio.run(test())
  "
  ```

- [ ] **Fast-Agent-MCP POC**
  - [ ] `test_agent.py` runs without errors
  - [ ] Agent can be instantiated
  - [ ] MCP servers connect (if configured)
  - [ ] Tool discovery works

  **Test Command:**
  ```bash
  docker compose exec bot python test_agent.py
  ```

- [ ] **ChatPlatformToolAgent**
  - [ ] Agent can process messages
  - [ ] Tool execution hooks are called
  - [ ] Approval integration works with hooks
  - [ ] Status messages are sent correctly
  - [ ] Thread context management works (`set_thread_id()`)

- [ ] **MessageHandler (Core Message Routing)**
  - [ ] MessageHandler initializes correctly
  - [ ] Connectors can be registered
  - [ ] `handle_message()` processes messages correctly
  - [ ] Thread context is set on agent
  - [ ] Responses are routed back through correct connector
  - [ ] Error handling works (unregistered platform, agent errors)

  **Test Command:**
  ```bash
  docker compose exec bot python -c "
  import asyncio
  from core.message_handler import MessageHandler
  from connectors.interface import PlatformMessage
  from agent.tool_agent import ChatPlatformToolAgent
  from approval.approval_tracker import ApprovalTracker
  from approval.rules_manager import ApprovalRulesManager
  from connectors.slack_connector import SlackConnector
  
  async def test():
      # Create mock agent and handler
      slack = SlackConnector()
      rules = ApprovalRulesManager()
      tracker = ApprovalTracker()
      # Note: This requires a real agent instance, so may need to be tested in integration
      print('MessageHandler can be imported and initialized')
  
  asyncio.run(test())
  "
  ```

### Core Message Flow (MessageHandler Architecture)

- [ ] **Complete Message Flow**
  - [ ] Message received by connector (Slack/Discord)
  - [ ] Message normalized to PlatformMessage
  - [ ] Factoid check happens first (if applicable)
  - [ ] Message forwarded to MessageHandler
  - [ ] MessageHandler sets thread context on agent
  - [ ] Agent processes message via `agent.send()`
  - [ ] Response routed back through originating connector
  - [ ] User receives response in correct thread/channel

  **Test Steps:**
  1. Start `app_multi_platform.py`
  2. Send message mentioning bot in Slack: `@botname hello`
  3. Verify complete flow:
     - Bot receives message
     - Factoid check (if message matches factoid)
     - Message forwarded to MessageHandler
     - Agent processes and responds
     - Response appears in Slack thread
  4. Test with non-factoid message to verify agent processing
  5. Test multiple messages in same thread - verify context maintained

### Multi-Platform Integration

- [ ] **Single Platform Testing (Recommended First)**
  - [ ] Start with Slack connector only (Discord commented out)
  - [ ] Verify agent creation and initialization
  - [ ] Verify MessageHandler routing works
  - [ ] Verify end-to-end message flow
  - [ ] Test factoids, agent responses, and error handling

  **Test Steps:**
  1. Start `app_multi_platform.py` (currently configured for Slack only)
  2. Verify logs show agent initialization
  3. Send test message in Slack
  4. Verify response received
  5. Check logs for MessageHandler activity

- [ ] **Multi-Platform App (Full Integration)**
  - [ ] Both Slack and Discord connectors start
  - [ ] Shared agent instance works with both platforms
  - [ ] Messages from different platforms are handled independently
  - [ ] No cross-platform interference
  - [ ] MessageHandler routes to correct connector

  **Test Steps:**
  1. Uncomment Discord connector in `app_multi_platform.py`
  2. Start `app_multi_platform.py`
  3. Send message in Slack - verify response
  4. Send message in Discord - verify response
  5. Verify both work simultaneously
  6. Verify thread contexts are maintained separately per platform

### Error Handling

- [ ] **Configuration Errors**
  - [ ] Missing config files show graceful errors
  - [ ] Invalid YAML shows clear error messages
  - [ ] Missing environment variables are handled

- [ ] **API Errors**
  - [ ] Slack API errors are handled gracefully
  - [ ] Discord API errors are handled gracefully
  - [ ] Anthropic API errors are handled gracefully
  - [ ] Errors don't crash the bot

- [ ] **Timeout Handling**
  - [ ] Approval timeouts are handled correctly
  - [ ] Tool execution timeouts are handled
  - [ ] Network timeouts don't crash the system

## Debugging Tips

### View Logs

```bash
# All logs
docker compose logs -f

# Bot logs only
docker compose logs -f bot

# Last 100 lines
docker compose logs --tail=100 bot

# Filter for errors
docker compose logs bot | grep -i error
```

### Interactive Shell

```bash
# Enter container shell
docker compose exec bot /bin/bash

# Run Python interpreter
docker compose exec bot python

# Run specific Python script
docker compose exec bot python -c "your_code_here"
```

### Restart Services

```bash
# Restart bot
docker compose restart bot

# Rebuild and restart
docker compose up --build -d

# Stop and remove containers
docker compose down

# Stop, remove containers and volumes
docker compose down -v
```

### Common Issues

1. **Bot not starting**
   - Check logs: `docker compose logs bot`
   - Verify `.env` file exists and has correct values
   - Verify configuration files are valid YAML
   - Check Docker daemon is running

2. **Connection errors**
   - Verify API keys/tokens are correct
   - Check network connectivity
   - Verify Slack/Discord apps are properly configured
   - Check firewall/network settings

3. **Configuration errors**
   - Validate YAML syntax: `python -c "import yaml; yaml.safe_load(open('/app/fastagent.config.yaml'))"`
   - Verify config files exist in project root (fast-agent expects them there)
   - Verify environment variables are set
   - Check file permissions
   - Note: Fast-agent automatically loads config from project root, not from `config/` directory

4. **Import errors**
   - Verify all dependencies are installed: `docker compose exec bot pip list`
   - Check Python path
   - Rebuild Docker image: `docker compose build --no-cache`

## Next Steps

After manual testing:

1. Review any errors or unexpected behavior
2. Document findings
3. Create unit tests for failed scenarios
4. Add integration tests for complex workflows
5. Update documentation based on test results

## Architecture Testing Notes

### MessageHandler Architecture

The bot now uses a centralized MessageHandler architecture:
- **Connectors** (Slack/Discord): Receive messages, normalize to PlatformMessage, forward to MessageHandler
- **MessageHandler**: Central routing layer that calls `agent.send()` and routes responses back
- **ChatPlatformToolAgent**: Wraps fast-agent with approval hooks and platform interface
- **Fast-Agent**: Core AI orchestration and tool management

**Testing the Architecture:**
1. Verify connectors are thin (~50 lines of event handling)
2. Verify all `agent.send()` calls go through MessageHandler
3. Verify responses route back through correct connector
4. Verify thread context is maintained per conversation

### Agent Creation

The agent is now created via `create_agent_from_config()`:
- Fast-agent automatically loads config from `fastagent.config.yaml` in project root when `fast.run()` is called
- Fast-agent automatically loads secrets from `fastagent.secrets.yaml` in project root
- Creates fast-agent instance with `@fast.agent` decorator
- Returns async context manager for lifecycle management
- Wrapped in `ChatPlatformToolAgent` for approval hooks

**Note:** Config files are copied from `config/` directory to project root during Docker build (see `docker/Dockerfile`), or mounted in docker-compose for development.

**Testing Agent Creation:**
- Verify config files exist in project root (`/app/fastagent.config.yaml` and `/app/fastagent.secrets.yaml`)
- Verify agent context manager enters/exits correctly
- Verify agent instance is usable for message processing
- Verify MCP servers connect (if configured)
- Verify agent stays alive during bot runtime

## Notes

- Some features may require full fast-agent-mcp integration to test completely
- Approval workflow testing requires UI interaction (buttons in Slack/Discord)
- Factoid testing requires `factoids.json` with test triggers
- MCP server testing requires configured MCP servers in `fastagent.config.yaml`
- Full end-to-end testing requires all components integrated and running
- **Recommended**: Start with single platform (Slack) testing before enabling multi-platform
- Agent context manager must stay alive for entire bot runtime (managed in `app_multi_platform.py`)

