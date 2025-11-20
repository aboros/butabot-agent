# Butabot Agent

A Slack bot powered by Anthropic's Agent SDK that maintains conversation context in threads and integrates with MCP (Model Context Protocol) servers for tool usage.

## Features

- **Thread-aware conversations**: Maintains context within Slack threads using Agent SDK sessions
- **MCP integration**: Supports MCP servers (Filesystem, Brave Search, Drupal MCP, etc.) via config file
- **Tool approvals**: Interactive Slack buttons for approving/denying tool usage
- **Security-focused**: Built-in filesystem and development tools disabled by default
- **Limited tool access**: Only web search/fetch and skills enabled for safety
- **Async processing**: Full async/await support for responsive interactions
- **Docker support**: Ready for deployment on DigitalOcean or any container platform

## Architecture

The bot uses:
- **Slack Bolt** for Python - Handles Slack events and interactions
- **Anthropic Agent SDK** - Manages Claude conversations and tool execution
- **MCP Servers** - External tools accessible via Model Context Protocol
- **Session Management** - Maps Slack thread IDs to Agent SDK sessions

## Prerequisites

- Python 3.11+
- Node.js 20+ (required for MCP servers)
- Slack workspace with bot app created
- Anthropic API key
- Claude Code CLI (installed automatically with Agent SDK)

## Setup

### 1. Clone and Install Dependencies

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_SIGNING_SECRET=your-signing-secret-here
SLACK_APP_TOKEN=xapp-your-app-token-here  # Required for Socket Mode

# Anthropic Configuration
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# MCP Configuration (optional)
MCP_CONFIG_PATH=config/mcp.config.json

# Server Configuration
PORT=3000
```

### 3. Configure MCP Servers (Optional)

Edit `config/mcp.config.json` to add your MCP servers. The bot is configured with security in mind - built-in filesystem tools are disabled, so if you need filesystem access, you must use an MCP filesystem server:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"],
      "env": {}
    },
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "your-brave-api-key"
      }
    }
  }
}
```

**Note**: By default, the bot has no MCP servers configured and only has access to `WebSearch`, `WebFetch`, and `Skill` tools. This is a security-focused configuration that prevents filesystem access and command execution.

### 4. Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create a new app or use an existing one
3. Enable **Socket Mode** (required for this bot)
4. Add the following OAuth scopes:
   - `app_mentions:read`
   - `chat:write`
   - `channels:read`
   - `groups:read`
   - `im:read`
   - `mpim:read`
5. Install the app to your workspace
6. Copy the tokens:
   - **Bot Token** (starts with `xoxb-`) → `SLACK_BOT_TOKEN`
   - **App-Level Token** (starts with `xapp-`) → `SLACK_APP_TOKEN`
   - **Signing Secret** → `SLACK_SIGNING_SECRET`

### 5. Run the Bot

```bash
# Activate virtual environment
source venv/bin/activate

# Run the bot
python main.py
```

The bot will start in Socket Mode and connect to Slack.

## Usage

### Basic Interaction

1. Invite the bot to a channel: `/invite @Butabot`
2. Mention the bot: `@Butabot What's the weather like?`
3. The bot responds in the same thread, maintaining context

### Thread Context

- Each Slack thread maintains its own conversation session
- The bot remembers previous messages in the thread
- Start a new thread for a new conversation

### Tool Approvals

When Claude wants to use a tool:
1. The bot posts an approval request with tool details
2. Click **Approve** or **Deny** buttons
3. Tool execution proceeds or is blocked accordingly
4. Approval requests timeout after 5 minutes (default)

### Available Tools

The bot has a security-focused configuration with most tools disabled. Only the following tools are available:

**Enabled Tools:**
- **WebSearch** - Search the web for current information
- **WebFetch** - Fetch and analyze web page content
- **Skill** - Execute specialized skills (if configured)

**Disabled Tools (for security):**
- Filesystem tools: `Read`, `Write`, `Edit`, `Glob`, `Grep`
- Development tools: `Task`, `Bash`, `TodoWrite`, `NotebookEdit`, `ExitPlanMode`
- Process management: `BashOutput`, `KillBash`

This configuration prevents the bot from accessing the filesystem or executing system commands, making it safer for production use. To enable additional tools, modify `bot/claude_client.py` and update the `disallowed_tools` list.

## Project Structure

```
butabot-agent/
├── bot/
│   ├── __init__.py          # Package init
│   ├── app.py              # Slack Bolt app and event handlers
│   ├── claude_client.py    # ClaudeSDKClient wrapper
│   ├── tool_approval.py    # Tool approval workflow
│   ├── session_manager.py  # Thread-to-session mapping
│   └── mcp_config.py       # MCP config loader
├── config/
│   └── mcp.config.json     # MCP server configurations
├── docker/
│   └── Dockerfile          # Docker container definition
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── docker-compose.yml      # Docker Compose config
└── README.md               # This file
```

## Docker Deployment

### Local Development with Docker Compose

```bash
# Build and run
docker-compose up --build

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### DigitalOcean Droplet Deployment

#### 1. Create Droplet

- Choose Ubuntu 22.04 LTS
- Minimum: 1GB RAM, 1 vCPU (2GB+ recommended)
- Enable Docker on creation or install manually

#### 2. Install Docker (if not pre-installed)

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

#### 3. Clone and Setup

```bash
# Clone repository
git clone <your-repo-url> butabot-agent
cd butabot-agent

# Create .env file
nano .env
# Paste your environment variables

# Create config directory and MCP config
mkdir -p config
nano config/mcp.config.json
# Paste your MCP server configurations
```

#### 4. Build and Run

```bash
# Build Docker image
docker build -f docker/Dockerfile -t butabot-agent .

# Run container
docker run -d \
  --name butabot-agent \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/config:/app/config:ro \
  butabot-agent

# View logs
docker logs -f butabot-agent
```

#### 5. Systemd Service (Optional)

Create `/etc/systemd/system/butabot-agent.service`:

```ini
[Unit]
Description=Butabot Agent Slack Bot
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/butabot-agent
ExecStart=/usr/bin/docker start -a butabot-agent
ExecStop=/usr/bin/docker stop butabot-agent
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable butabot-agent
sudo systemctl start butabot-agent
sudo systemctl status butabot-agent
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SLACK_BOT_TOKEN` | Slack bot token (xoxb-...) | Yes |
| `SLACK_SIGNING_SECRET` | Slack signing secret | Yes |
| `SLACK_APP_TOKEN` | Slack app-level token (xapp-...) for Socket Mode | Yes |
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes |
| `MCP_CONFIG_PATH` | Path to MCP config JSON file | No (default: `config/mcp.config.json`) |
| `PORT` | HTTP server port (for health checks) | No (default: `3000`) |

## MCP Server Configuration

The bot supports three types of MCP servers:

### Stdio Servers (most common)

```json
{
  "server-name": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-name"],
    "env": {
      "API_KEY": "value"
    }
  }
}
```

### HTTP Servers

```json
{
  "server-name": {
    "type": "http",
    "url": "https://mcp-server.example.com",
    "headers": {
      "Authorization": "Bearer token"
    }
  }
}
```

### SSE Servers

```json
{
  "server-name": {
    "type": "sse",
    "url": "https://mcp-server.example.com/sse",
    "headers": {
      "Authorization": "Bearer token"
    }
  }
}
```

## Troubleshooting

### Bot not responding

1. Check logs: `docker logs butabot-agent` or console output
2. Verify environment variables are set correctly
3. Ensure bot is invited to the channel
4. Check Slack app permissions and scopes

### Tool approvals not working

1. Verify interactive components are enabled in Slack app settings
2. Check that button action IDs match (`tool_approve`, `tool_deny`)
3. Ensure bot has `chat:write` permission

### MCP servers not loading

1. Verify `mcp.config.json` syntax is valid JSON
2. Check that Node.js is installed (required for npx)
3. Test MCP server command manually: `npx -y @modelcontextprotocol/server-filesystem /tmp`
4. Check environment variables for MCP servers

### Session not maintaining context

1. Ensure you're replying in the same thread (use thread_ts)
2. Check that session_manager is working (logs should show session creation)
3. Verify `resume` parameter is being passed to ClaudeSDKClient

### Tool access restrictions

The bot has a security-focused configuration that disables most built-in tools. If you need additional tools:

1. Edit `bot/claude_client.py`
2. Modify the `disallowed_tools` list in `ClaudeClient.__init__()`
3. Remove tools you want to enable from the list
4. Rebuild and restart the container

**Security Note**: Enabling `Bash` or filesystem tools (`Read`, `Write`, etc.) allows the bot to execute system commands and access files. Only enable these if you trust the bot and have proper access controls in place.

## Development

### Running Tests

```bash
# Activate venv
source venv/bin/activate

# Run with debug logging
python main.py
```

### Code Structure

- **Session Management**: `bot/session_manager.py` - Maps threads to sessions
- **Claude Integration**: `bot/claude_client.py` - Wraps Agent SDK client
- **Tool Approvals**: `bot/tool_approval.py` - Handles approval workflow
- **Slack Events**: `bot/app.py` - Main Slack Bolt app

## License

MIT License - see LICENSE file for details

## Contributing

Contributions welcome! Please open an issue or pull request.

## Support

For issues and questions:
- Check the [Anthropic Agent SDK docs](https://docs.claude.com/en/docs/agent-sdk/python)
- Review [Slack Bolt for Python docs](https://slack.dev/bolt-python/)
- Open an issue on GitHub

