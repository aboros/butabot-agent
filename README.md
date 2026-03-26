# Butabot Agent

A multi-platform chat bot powered by Anthropic's Agent SDK. Each deployment instance connects to a single platform (Slack or Discord) and maintains conversation context per thread.

## Features

- **Multi-platform**: Slack and Discord supported via a shared connector abstraction — one instance, one platform, selected at startup
- **Thread-aware conversations**: Maintains context within threads/replies using Agent SDK sessions
- **MCP integration**: Supports any MCP server (Filesystem, Brave Search, custom servers, etc.) via config file
- **Tool approvals**: Interactive buttons (Slack Block Kit / Discord UI components) for approving or denying tool usage before execution
- **Async**: Full async/await throughout for responsive interactions
- **Docker support**: Ready for deployment on any Docker host (Raspberry Pi 5, VPS, etc.)

## Architecture

```
┌─────────────────────────────────────────────┐
│                  bot/app.py                  │
│           (orchestrator + entry point)        │
│                                               │
│   PlatformConnector ◄──► ClaudeClient        │
│   (selected via PLATFORM env var)             │
└─────────────────────────────────────────────┘
          │                    │
   ┌──────┴──────┐      ┌──────┴──────┐
   │  Slack      │      │  Discord    │
   │  Connector  │      │  Connector  │
   └─────────────┘      └─────────────┘
```

- **`PlatformConnector`** (interface) — `send_message`, `request_approval`, `on_tool_result`, `start/stop`
- **`SlackConnector`** — Slack Bolt, Socket Mode, Block Kit approval buttons
- **`DiscordConnector`** — nextcord, @mention detection, Discord UI buttons
- **`ClaudeClient`** — Anthropic Agent SDK wrapper; platform-agnostic
- **`SessionManager`** — maps conversation keys to Agent SDK session IDs (optional JSON persistence)
- **`ConversationDispatch`** — per-conversation FIFO queue plus a global limit on concurrent agent turns
- **`ToolApprovalManager`** — Slack-specific approval state machine (used internally by `SlackConnector`)

The `PLATFORM` environment variable selects which connector is loaded at startup. No other code changes are needed to switch platforms.

### Concurrency, conversation keys, and persistence

- **Queues**: Incoming messages are enqueued per stable `thread_id`. Different conversations can be processed in parallel up to `MAX_CONCURRENT_AGENT_TURNS` (default 8). The same `thread_id` is handled strictly in order.
- **Conversation keys**: Connectors compute `thread_id` from platform events. Discord top-level mentions use `DISCORD_TOP_LEVEL_KEY` (`per_message`, `channel`, or `channel_user`). Slack uses `SLACK_KEY` (`thread_ts`, `channel`, or `channel_user`). See `.env.example`.
- **Persistence**: Set `SESSIONS_JSON_PATH` or `PERSIST_SESSION_IDS=true` to save `thread_id → session_id` under `/data` (or `AGENT_DATA_DIR`) so Agent SDK `resume` works after restart.

Run unit tests from the repo root:

```bash
PYTHONPATH=. python3 -m unittest discover tests -v
```

## Prerequisites

- **Docker** and **Docker Compose**
- An **Anthropic API key**
- A bot app on the target platform (Slack *or* Discord — one per instance)

Python, Node.js, and all dependencies are installed inside the Docker image.

## Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd butabot-agent
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your values. Only the variables relevant to your chosen platform are required.

```bash
# --- Platform selection ---
PLATFORM=slack   # or: discord

# --- Anthropic ---
ANTHROPIC_API_KEY=your-anthropic-api-key

# --- Slack (only needed when PLATFORM=slack) ---
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=your-signing-secret
SLACK_APP_TOKEN=xapp-...     # Required for Socket Mode

# --- Discord (only needed when PLATFORM=discord) ---
DISCORD_TOKEN=your-discord-bot-token
```

### 3. Configure MCP Servers (Optional)

```bash
cp .mcp.json.example .mcp.json
```

The file must exist and contain at least `{"mcpServers": {}}`. The default `.mcp.json` ships as an empty config so the bot starts with no extra tools.

Edit `.mcp.json` to add MCP servers. **Do not put secrets in this file** — add them to `.env` instead. MCP server processes automatically inherit the container environment, so any variable in `.env` is available to them without extra configuration.

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    },
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@brave/brave-search-mcp-server"]
    }
  }
}
```

Add `BRAVE_API_KEY=...` to `.env` and it will be picked up automatically.

For Docker-based MCP servers, pass env vars through with `-e VAR_NAME` (no value — Docker inherits from the parent process):

```json
{
  "mcpServers": {
    "github": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"]
    }
  }
}
```

See `.mcp.json.example` for a full set of pre-configured servers.

### 4. Platform-specific App Setup

#### Slack

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app
2. Enable **Socket Mode**
3. Add OAuth scopes: `app_mentions:read`, `chat:write`, `channels:read`, `groups:read`, `im:read`, `mpim:read`
4. Enable **Interactivity** (required for approval buttons)
5. Install the app to your workspace
6. Copy tokens to `.env`:
   - **Bot Token** (`xoxb-...`) → `SLACK_BOT_TOKEN`
   - **App-Level Token** (`xapp-...`) → `SLACK_APP_TOKEN`
   - **Signing Secret** → `SLACK_SIGNING_SECRET`

#### Discord

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) and create a new application
2. Under **Bot**, create a bot and copy the token → `DISCORD_TOKEN`
3. Enable **Privileged Gateway Intents**: `Message Content Intent`
4. Under **OAuth2 → URL Generator**, select scopes: `bot` + permissions: `Send Messages`, `Read Message History`, `Use Application Commands`
5. Use the generated URL to invite the bot to your server

### 5. Run

```bash
# Build and start
docker compose up --build

# Background
docker compose up -d

# Logs
docker compose logs -f

# Stop
docker compose down
```

## Usage

### Slack

1. Invite the bot to a channel: `/invite @Butabot`
2. Mention it: `@Butabot What can you help me with?`
3. The bot responds in the same thread, maintaining context throughout the thread

### Discord

1. @mention the bot in any channel: `@Butabot What can you help me with?`
2. For ongoing conversations, use a Discord thread — the bot shares context within a thread
3. Each new @mention in a regular channel starts a fresh context

### Tool Approvals

When Claude wants to use a tool:
1. The bot posts an approval request with the tool name and input
2. Click **Approve** or **Deny**
3. On approval, the tool executes and the message is updated with the result
4. Requests time out after 5 minutes and are auto-denied

## Project Structure

```
butabot-agent/
├── bot/
│   ├── __init__.py
│   ├── app.py                # Orchestrator: wires connector + Claude client
│   ├── claude_client.py      # Anthropic Agent SDK wrapper
│   ├── session_manager.py    # Thread-ID → Agent SDK session mapping
│   ├── tool_approval.py      # Slack approval state machine
│   ├── logger.py             # Structured logging helpers
│   └── connectors/
│       ├── __init__.py
│       ├── interface.py          # PlatformConnector ABC + IncomingMessage
│       ├── slack_connector.py    # Slack Bolt implementation
│       └── discord_connector.py  # nextcord implementation
├── data/                     # Agent working directory (mount point)
├── docker/
│   └── Dockerfile
├── requirements.txt
├── docker-compose.yml
├── .env.example
├── .mcp.json.example
└── README.md
```

## Environment Variables

| Variable | Platform | Required | Description |
|---|---|---|---|
| `PLATFORM` | — | No (default: `slack`) | `slack` or `discord` |
| `ANTHROPIC_API_KEY` | — | **Yes** | Anthropic API key |
| `SLACK_BOT_TOKEN` | Slack | **Yes** | Bot token (`xoxb-...`) |
| `SLACK_SIGNING_SECRET` | Slack | **Yes** | Signing secret |
| `SLACK_APP_TOKEN` | Slack | **Yes** | App-level token for Socket Mode (`xapp-...`) |
| `DISCORD_TOKEN` | Discord | **Yes** | Discord bot token |

Any additional variables referenced in `.mcp.json` (e.g. `BRAVE_API_KEY`) also go in `.env`.

## MCP Server Configuration

The agent loads MCP servers from `.mcp.json` at startup. Secrets must be referenced via `${VAR_NAME}` — never hardcoded — because the agent can read this file.

Three server transport types are supported:

**stdio** (most common)
```json
{
  "my-server": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-name"],
    "env": { "API_KEY": "${MY_API_KEY}" }
  }
}
```

**HTTP**
```json
{
  "my-server": {
    "type": "http",
    "url": "https://mcp.example.com",
    "headers": { "Authorization": "Bearer ${API_TOKEN}" }
  }
}
```

**SSE**
```json
{
  "my-server": {
    "type": "sse",
    "url": "https://mcp.example.com/sse",
    "headers": { "Authorization": "Bearer ${API_TOKEN}" }
  }
}
```

## Deployment

The bot runs as a single Docker Compose service. Any host with Docker installed works.

### Systemd Service (Recommended)

Create `/etc/systemd/system/butabot.service`:

```ini
[Unit]
Description=Butabot Agent
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/your-username/butabot-agent
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable butabot
sudo systemctl start butabot
sudo systemctl status butabot
```

### Running Multiple Instances

Each instance is an independent deployment with its own `.env` file:

```
butabot-slack/    PLATFORM=slack,  SLACK_BOT_TOKEN=..., etc.
butabot-discord/  PLATFORM=discord, DISCORD_TOKEN=..., etc.
```

Each directory has its own `docker-compose.yml` and `.env`. They do not share state.

## Troubleshooting

### Bot not responding

1. Check logs: `docker compose logs -f`
2. Verify env variables are set in `.env`
3. **Slack**: ensure the bot is invited to the channel
4. **Discord**: confirm `Message Content Intent` is enabled in the Developer Portal

### Tool approvals not working

- **Slack**: ensure Interactivity is enabled in your Slack app settings
- **Discord**: buttons expire after 15 minutes (Discord platform limit); the bot enforces a 5-minute timeout before that

### `MCP config is not a valid JSON` on startup

The Claude CLI expands `${VAR_NAME}` tokens in `.mcp.json` before parsing it. If any env var value contains a JSON-special character (quote, backslash, newline), the resulting file is invalid JSON and the agent fails to start.

**Fix**: remove `env` blocks from `.mcp.json` entirely. MCP server processes inherit the container environment — secrets in `.env` are available automatically without being listed in `.mcp.json`. See the MCP configuration section above.

### MCP servers not loading

1. Check `.mcp.json` is valid JSON (`python3 -m json.tool .mcp.json`)
2. Confirm the file exists and contains at least `{"mcpServers": {}}`
3. Test a server manually: `npx -y @modelcontextprotocol/server-filesystem /tmp`

### Session context lost

- **Slack**: reply in the same thread (not a new message in the channel)
- **Discord**: use a Discord thread for multi-turn conversations; each top-level @mention starts a new session

### Enabling / disabling tools

Edit `bot/claude_client.py` and modify the `disallowed_tools` list in `ClaudeClient.__init__()`, then rebuild:

```bash
docker compose up --build -d
```

## License

MIT License — see LICENSE file for details.

## Contributing

Contributions welcome. Please open an issue or pull request.

## Resources

- [Anthropic Agent SDK docs](https://docs.claude.com/en/docs/agent-sdk/python)
- [Slack Bolt for Python](https://slack.dev/bolt-python/)
- [nextcord (Discord)](https://docs.nextcord.dev/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
