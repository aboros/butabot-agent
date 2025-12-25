#!/usr/bin/env python3
"""Pre-install npm packages from npx-based MCP servers in .mcp.json.

This script is run during Docker build to pre-install npm packages,
preventing hanging during MCP server initialization at runtime.
"""
import json
import subprocess
import sys
import signal

# Configure npm to be more aggressive with timeouts and retries
def configure_npm():
    """Configure npm with shorter timeouts to prevent hanging."""
    configs = [
        ['npm', 'config', 'set', 'fetch-timeout', '30000'],  # 30 second timeout
        ['npm', 'config', 'set', 'fetch-retries', '2'],      # Only 2 retries
        ['npm', 'config', 'set', 'fetch-retry-mintimeout', '10000'],  # 10s min retry
        ['npm', 'config', 'set', 'fetch-retry-maxtimeout', '30000'],  # 30s max retry
    ]
    for cmd in configs:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10, check=False)
        except Exception:
            pass  # Ignore config errors

def install_package(pkg, timeout=120):
    """Install npm package with timeout."""
    print(f"[INFO] Installing {pkg} (timeout: {timeout}s)...", file=sys.stderr)
    sys.stderr.flush()
    
    try:
        # Use verbose output and progress to see what's happening
        result = subprocess.run(
            ['npm', 'install', '-g', '--progress=false', '--loglevel=warn', pkg],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout
        )
        
        if result.returncode != 0:
            print(f"[WARNING] Failed to install {pkg}:", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
            return False
        else:
            print(f"[INFO] Successfully installed {pkg}", file=sys.stderr)
            return True
    except subprocess.TimeoutExpired:
        print(f"[ERROR] Installation of {pkg} timed out after {timeout} seconds", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[ERROR] Exception installing {pkg}: {e}", file=sys.stderr)
        return False

def main():
    try:
        # Configure npm first
        print("[INFO] Configuring npm with shorter timeouts...", file=sys.stderr)
        configure_npm()
        
        with open('.mcp.json', 'r') as f:
            config = json.load(f)
        
        mcp_servers = config.get('mcpServers', {})
        npm_packages = []
        
        for server_name, server_config in mcp_servers.items():
            command = server_config.get('command', '')
            args = server_config.get('args', [])
            
            # Check if this server uses npx
            if command == 'npx' and args:
                # The package name is typically the first non-flag argument after "-y"
                # Look for the first argument that looks like a package name
                for arg in args:
                    # Skip flags like -y, --transport, etc.
                    if arg.startswith('-'):
                        continue
                    # Found a package name (starts with @ or is a valid package name)
                    if arg.startswith('@') or '/' in arg or not arg.startswith('-'):
                        npm_packages.append(arg)
                        break
        
        if npm_packages:
            print(f"[INFO] Found {len(npm_packages)} npx-based MCP servers to pre-install:", file=sys.stderr)
            for pkg in npm_packages:
                print(f"  - {pkg}", file=sys.stderr)
            sys.stderr.flush()
            
            # Install packages globally with timeout
            success_count = 0
            for pkg in npm_packages:
                if install_package(pkg, timeout=120):
                    success_count += 1
            
            if success_count > 0:
                # Clean npm cache to reduce image size
                print("[INFO] Cleaning npm cache...", file=sys.stderr)
                subprocess.run(['npm', 'cache', 'clean', '--force'], capture_output=True, timeout=30)
                print(f"[INFO] Pre-installed {success_count}/{len(npm_packages)} npm packages for MCP servers", file=sys.stderr)
            else:
                print(f"[WARNING] Failed to install all npm packages. Continuing anyway...", file=sys.stderr)
        else:
            print("[INFO] No npx-based MCP servers found in .mcp.json", file=sys.stderr)
    except Exception as e:
        print(f"[WARNING] Error parsing .mcp.json for npm packages: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        print("[INFO] Continuing build without pre-installing npm packages", file=sys.stderr)
        # Don't fail the build if we can't parse the config

if __name__ == '__main__':
    main()

