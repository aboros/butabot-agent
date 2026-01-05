# Use Python 3.13.5 with Node.js for MCP servers
# Per fast-agent.ai recommendation: "If you have multiple Python versions, use --python 3.13.5 to ensure you get the latest version of fast-agent."
FROM python:3.13.5-slim

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

# Copy config directory and install script early so we can parse it for dynamic npm package installation
COPY config/ ./config/
COPY docker/install_mcp_npm_packages.py .

# Install PyYAML early (needed for parsing fastagent.config.yaml)
# This is a minimal dependency needed before the full requirements.txt install
RUN pip install --no-cache-dir PyYAML

# Dynamically extract and pre-install npm packages from npx-based MCP servers
# This prevents hanging during MCP server initialization at runtime
RUN python3 install_mcp_npm_packages.py

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (new multi-platform architecture)
# Core modules
COPY agent/ ./agent/
COPY approval/ ./approval/
COPY config/ ./config/
COPY connectors/ ./connectors/
COPY core/ ./core/
# Legacy bot module (still needed for factoids and other utilities)
COPY bot/ ./bot/
# MCP server
COPY mcp_nano_banana/ ./mcp_nano_banana/
# Main application entry point
COPY app_multi_platform.py .
# Test agent (useful for debugging)
COPY test_agent.py .
# Configuration files
# Note: factoids.json is optional - FactoidManager handles missing files gracefully
# If factoids.json doesn't exist, create it from factoids.json.example or the app will start without factoids
COPY factoids.json* ./

# Copy fast-agent config files to root where fast-agent expects them
# Fast-agent automatically loads fastagent.config.yaml and fastagent.secrets.yaml from the project root
# We keep them in config/ for organization, but copy to root for fast-agent's built-in loading
RUN if [ -f ./config/fastagent.config.yaml ]; then \
        cp ./config/fastagent.config.yaml ./fastagent.config.yaml; \
    fi && \
    if [ -f ./config/fastagent.secrets.yaml ]; then \
        cp ./config/fastagent.secrets.yaml ./fastagent.secrets.yaml; \
    fi

# Create data directory for agent workspace
RUN mkdir -p /app/data

# Expose port (for health checks, though Socket Mode doesn't need it)
EXPOSE 3000

# Health check - check for app_multi_platform.py process
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python.*app_multi_platform" || exit 1

# Set Python to unbuffered mode for immediate log output in Docker
ENV PYTHONUNBUFFERED=1

# Run the multi-platform bot application
CMD ["python", "app_multi_platform.py"]

