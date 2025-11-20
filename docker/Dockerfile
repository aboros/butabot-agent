# Use Python 3.11 with Node.js for MCP servers
FROM python:3.11-slim

# Install Node.js and npm (required for MCP servers) and procps (for health check)
RUN apt-get update && apt-get install -y \
    curl \
    procps \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Claude Code CLI (required by Agent SDK)
RUN npm install -g @anthropic-ai/claude-code

# Copy application code
COPY . .

# Create config directory if it doesn't exist
RUN mkdir -p config

# Expose port (for health checks, though Socket Mode doesn't need it)
EXPOSE 3000

# Health check - simple process check (Socket Mode doesn't expose HTTP)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python.*bot.app" || exit 1

# Run the bot
CMD ["python", "-m", "bot.app"]

