# Use Python 3.11 with Node.js for MCP servers
FROM python:3.11-slim

# Install Node.js and npm (required for MCP servers), Docker CLI (for STDIO MCP servers), and procps (for health check)
RUN apt-get update && apt-get install -y \
    curl \
    procps \
    ca-certificates \
    gnupg \
    lsb-release \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Claude Code CLI (required by Agent SDK)
# Try latest version to see if HTTP MCP server support is available
RUN npm install -g @anthropic-ai/claude-code@latest

# Copy application code
COPY . .

# Create config directory if it doesn't exist
RUN mkdir -p config

# Expose port (for health checks, though Socket Mode doesn't need it)
EXPOSE 3000

# Health check - simple process check (Socket Mode doesn't expose HTTP)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python.*bot.app" || exit 1

# Set Python to unbuffered mode for immediate log output in Docker
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "-m", "bot.app"]

