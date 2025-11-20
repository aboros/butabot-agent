# Butabot Agent

A Slack bot powered by Anthropic's Agent SDK that maintains conversation context in threads and integrates with MCP (Model Context Protocol) servers for tool usage.

## Features

- **Thread-aware conversations**: Maintains context within Slack threads using Agent SDK sessions
- **MCP integration**: Supports MCP servers (Filesystem, Brave Search, Drupal MCP, etc.) via config file
- **Tool approvals**: Interactive Slack buttons for approving/denying tool usage
- **Security-focused**: Filesystem tools enabled for Drupal development, Bash and other dev tools disabled by default
- **Drupal-ready**: Configured for Drupal development with filesystem access
- **Async processing**: Full async/await support for responsive interactions
- **Docker support**: Ready for deployment on DigitalOcean or any container platform

## Architecture

The bot uses:
- **Slack Bolt** for Python - Handles Slack events and interactions
- **Anthropic Agent SDK** - Manages Claude conversations and tool execution
- **MCP Servers** - External tools accessible via Model Context Protocol
- **Session Management** - Maps Slack thread IDs to Agent SDK sessions

## Prerequisites

- **Docker** and **Docker Compose** (installed on host machine)
- **Slack workspace** with bot app created
- **Anthropic API key** (stored in `.env` file)

**Note**: Python, Node.js, and Claude Code CLI are all installed automatically inside the Docker container - you don't need to install them on your host machine.

## Setup

### 1. Clone Repository

```bash
git clone <repository-url>
cd butabot-agent
```

**Note**: Dependencies are installed automatically when building the Docker image. The `venv/` folder is optional and only needed if you want to run Python scripts locally (outside Docker).

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env` with your actual values:

```bash
# Slack Configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_SIGNING_SECRET=your-signing-secret-here
SLACK_APP_TOKEN=xapp-your-app-token-here  # Required for Socket Mode

# Anthropic Configuration
ANTHROPIC_API_KEY=your-anthropic-api-key-here

# Drupal MCP Server Configuration (optional)
DRUPAL_AUTH_USER=your-drupal-username
DRUPAL_AUTH_PASSWORD=your-drupal-password
DRUPAL_BASE_URL=http://your-site.ddev.site

# Server Configuration
PORT=3000
```

### 3. Configure MCP Servers (Optional)

Copy `.mcp.json.example` to `.mcp.json` and customize it:

```bash
cp .mcp.json.example .mcp.json
```

Then edit `.mcp.json` to add your MCP servers. **Sensitive credentials should use environment variables** (e.g., `${DRUPAL_AUTH_USER}`) which are loaded from `.env` file.

**Security Note**: `.mcp.json` is mounted as read-only, so the agent can read it but cannot modify it. **Never put secrets directly in `.mcp.json`** - always use environment variables (see "MCP Server Configuration" section below for details).

The bot is configured for Drupal development with filesystem tools enabled. You can also add MCP servers for additional functionality:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/app/agent"],
      "env": {}
    },
    "brave-search": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-brave-search"],
      "env": {
        "BRAVE_API_KEY": "${BRAVE-API-KEY}"
      }
    }
  }
}
```

**Note**: 
- By default, the bot has filesystem tools (`Read`, `Write`, `Edit`, `Glob`, `Grep`) enabled for Drupal development.
- The agent's working directory is restricted to `/app/data` for security.
- You can add MCP servers for additional functionality (Drupal MCP, Brave Search, etc.).

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

The bot runs in Docker using Docker Compose:

```bash
# Build and start the bot
docker compose up --build

# Or run in background
docker compose up -d

# View logs
docker compose logs -f

# Stop the bot
docker compose down
```

The bot will start in Socket Mode and connect to Slack. See the [Docker Deployment](#docker-deployment) section for more details.

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

The bot is configured for Drupal development with filesystem tools enabled:

**Enabled Tools:**
- **Filesystem tools**: `Read`, `Write`, `Edit`, `Glob`, `Grep` - For working with Drupal site files
- **WebSearch** - Search the web for current information
- **WebFetch** - Fetch and analyze web page content
- **Skill** - Execute specialized skills (if configured)
- **MCP Tools** - Drupal MCP server tools (configured in `.mcp.json`)

**Disabled Tools (for security):**
- **Bash** - Command execution (disabled by default, enable if you need Drush/Composer)
- **BashOutput**, **KillBash** - Process management
- **Task**, **TodoWrite**, **NotebookEdit**, **ExitPlanMode** - Other development tools

**Note**: The agent's working directory is restricted to `/app/data`. Mount your Drupal site folders (or any folders) in `docker-compose.yml` to give the agent access. You can mount folders from anywhere on the host. To enable Bash or other tools, modify `bot/claude_client.py` and update the `disallowed_tools` list.

## Drupal Development Setup

The bot is configured for Drupal development with filesystem tools enabled. To mount your Drupal site:

### 1. Edit `docker-compose.yml`

Uncomment and configure the Drupal mount points:

```yaml
volumes:
  # Base data directory (agent's cwd)
  - ./data:/app/data:rw
  
  # Option 1: Mount entire docroot directly as data directory
  # - /path/to/drupal/docroot:/app/data:rw
  
  # Option 2: Mount docroot as subdirectory (recommended)
  # - /path/to/drupal/docroot:/app/data/drupal:rw
  
  # Option 3: Mount specific folders (most secure)
  # - /path/to/drupal/docroot/modules/custom:/app/data/drupal/modules/custom:rw
  # - /path/to/drupal/docroot/themes/custom:/app/data/drupal/themes/custom:rw
  
  # Optional: Mount CLAUDE.md for project guidance
  # - /path/to/drupal/CLAUDE.md:/app/data/CLAUDE.md:ro
```

### 2. Configure Drupal MCP Server

If you haven't already, copy `.mcp.json.example` to `.mcp.json`:

```bash
cp .mcp.json.example .mcp.json
```

Then edit `.mcp.json` to point to your Drupal site. The example file already includes the Drupal MCP server configuration with environment variable placeholders:

```json
{
  "mcpServers": {
    "drupal": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "DRUPAL_AUTH_USER",
        "-e",
        "DRUPAL_AUTH_PASSWORD",
        "--network=host",
        "ghcr.io/omedia/mcp-server-drupal:latest",
        "--drupal-url=${DRUPAL_BASE_URL}"
      ],
      "env": {
        "DRUPAL_AUTH_USER": "${DRUPAL_AUTH_USER}",
        "DRUPAL_AUTH_PASSWORD": "${DRUPAL_AUTH_PASSWORD}"
      }
    }
  }
}
```

**Important**: All secrets use environment variable placeholders (e.g., `${DRUPAL_AUTH_USER}`). Make sure these variables are set in your `.env` file. Never put actual secrets directly in `.mcp.json` as the Agent will be able to read that file.

### 3. Workflow

- **Filesystem tools** (`Read`, `Write`, `Edit`, `Glob`, `Grep`) - For file operations
- **Drupal MCP server** - For Drupal-specific operations (enable modules, clear cache, etc.)
- **CLAUDE.md** - For project-specific guidelines and patterns

The agent can now create modules, modify themes, update configuration files, and use Drupal MCP tools for site operations.

## Project Structure

```
butabot-agent/
├── data/                  # Agent's working directory (mounted to /app/data)
├── bot/
│   ├── __init__.py        # Package init
│   ├── app.py            # Slack Bolt app and event handlers
│   ├── claude_client.py  # ClaudeSDKClient wrapper
│   ├── tool_approval.py  # Tool approval workflow
│   └── session_manager.py # Thread-to-session mapping
├── docker/
│   └── Dockerfile        # Docker container definition
├── requirements.txt      # Python dependencies
├── docker-compose.yml    # Docker Compose config
├── .env.example          # Example environment variables (copy to .env)
├── .mcp.json.example     # Example MCP server config (copy to .mcp.json)
├── .mcp.json            # MCP server configurations (copied into image)
└── README.md             # This file
```

**Security Note**: The agent's working directory (`cwd`) is restricted to `/app/data` inside the container. You can mount any folder from the host to `/app/data` or mount additional folders anywhere in the container. Source code (`bot/`) and config (`.mcp.json`) are copied into the image, not mounted.

## Docker Deployment

The bot runs exclusively in Docker. Use Docker Compose for local development and production deployment.

```bash
# Build and run
docker compose up --build

# Run in background
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
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
# Clone repository (adjust path as needed)
git clone <your-repo-url> ~/butabot-agent
cd ~/butabot-agent

# Copy example files and configure
cp .env.example .env
nano .env
# Fill in your environment variables (tokens, API keys, etc.)

cp .mcp.json.example .mcp.json
nano .mcp.json
# Configure MCP servers (use environment variables for secrets)
```

#### 4. Build and Run

```bash
# Build and start with Docker Compose
docker compose up --build -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

#### 5. Systemd Service (Recommended for Production)

Create `/etc/systemd/system/butabot-agent.service`:

```ini
[Unit]
Description=Butabot Agent Slack Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=your-username
WorkingDirectory=/home/your-username/butabot-agent
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Important**: Replace `your-username` with your actual username and update `WorkingDirectory` to match your clone location.

Enable and start:

```bash
sudo systemctl enable butabot-agent
sudo systemctl start butabot-agent
sudo systemctl status butabot-agent
```

**Note**: Make sure your user is in the `docker` group:
```bash
sudo usermod -aG docker $USER
# Log out and back in for group changes to take effect
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SLACK_BOT_TOKEN` | Slack bot token (xoxb-...) | Yes |
| `SLACK_SIGNING_SECRET` | Slack signing secret | Yes |
| `SLACK_APP_TOKEN` | Slack app-level token (xapp-...) for Socket Mode | Yes |
| `ANTHROPIC_API_KEY` | Anthropic API key | Yes |
| `DRUPAL_AUTH_USER` | Drupal username for MCP server authentication | No (required if using Drupal MCP) |
| `DRUPAL_AUTH_PASSWORD` | Drupal password for MCP server authentication | No (required if using Drupal MCP) |
| `DRUPAL_BASE_URL` | Drupal site URL (e.g., http://site.ddev.site) | No (required if using Drupal MCP) |
| `PORT` | HTTP server port (for health checks) | No (default: `3000`) |

## MCP Server Configuration

### ⚠️ Security Best Practice: Never Put Secrets in `.mcp.json`

**CRITICAL**: Even though `.mcp.json` is gitignored, **never put secrets, API keys, tokens, or passwords directly in `.mcp.json`**. Here's why:

1. **Agent can read it**: The agent can read `.mcp.json` (it's mounted read-only), so secrets would be exposed
2. **Version control risk**: If `.mcp.json` is accidentally committed, secrets leak
3. **Container image risk**: `.mcp.json` is copied into the Docker image, so secrets would be baked in
4. **Sharing risk**: If you share `.mcp.json` with team members, secrets are exposed

**✅ Correct Approach**: Use environment variables with `${VAR_NAME}` syntax:

```json
{
  "mcpServers": {
    "my-server": {
      "env": {
        "API_KEY": "${MY_API_KEY}",           // ✅ Good: References .env
        "PASSWORD": "${MY_PASSWORD}"          // ✅ Good: References .env
      },
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}" // ✅ Good: References .env
      }
    }
  }
}
```

**❌ Wrong Approach**: Hardcoding secrets:

```json
{
  "mcpServers": {
    "my-server": {
      "env": {
        "API_KEY": "sk-1234567890abcdef",     // ❌ Bad: Secret in config
        "PASSWORD": "mypassword123"            // ❌ Bad: Secret in config
      }
    }
  }
}
```

**How to set it up:**

1. Add secrets to `.env` file (gitignored):
   ```bash
   MY_API_KEY=sk-1234567890abcdef
   MY_PASSWORD=secure-password
   API_TOKEN=your-token-here
   ```

2. Reference them in `.mcp.json` using `${VAR_NAME}`:
   ```json
   {
     "env": {
       "API_KEY": "${MY_API_KEY}"
     }
   }
   ```

3. Docker Compose automatically loads `.env` → Variables available in container
4. MCP SDK substitutes values → Secrets resolved at runtime from environment

The bot supports three types of MCP servers:

### Stdio Servers (most common)

```json
{
  "server-name": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-name"],
    "env": {
      "API_KEY": "${MY_API_KEY}"  // ✅ Use environment variable, not hardcoded value
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
      "Authorization": "Bearer ${API_TOKEN}"  // ✅ Use environment variable
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
      "Authorization": "Bearer ${API_TOKEN}"  // ✅ Use environment variable
    }
  }
}
```

**Remember**: Add the actual values to your `.env` file:
```bash
MY_API_KEY=your-actual-api-key-here
API_TOKEN=your-actual-token-here
```

## Troubleshooting

### Bot not responding

1. Check logs: `docker compose logs -f` or `docker compose logs` (if using Docker Compose)
2. Verify environment variables are set correctly in `.env` file
3. Ensure bot is invited to the channel
4. Check Slack app permissions and scopes

### Tool approvals not working

1. Verify interactive components are enabled in Slack app settings
2. Check that button action IDs match (`tool_approve`, `tool_deny`)
3. Ensure bot has `chat:write` permission

### MCP servers not loading

1. Verify `.mcp.json` syntax is valid JSON (located in project root)
2. Check that Node.js is installed (required for npx)
3. Test MCP server command manually: `npx -y @modelcontextprotocol/server-filesystem /tmp`
4. Check environment variables for MCP servers are set in `.env` file
5. Verify `.mcp.json` is mounted correctly: `docker compose exec bot cat /app/.mcp.json` (or `docker exec butabot-agent cat /app/.mcp.json` if using docker run)

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

**Security Note**: Enabling `Bash` or filesystem tools (`Read`, `Write`, etc.) allows the bot to execute system commands in the container. Only enable these if you trust the bot and have proper access controls in place.

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

