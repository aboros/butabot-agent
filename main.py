#!/usr/bin/env python3
"""Main entry point for Butabot Agent."""

import asyncio
import sys
from bot.app import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.exit(0)

