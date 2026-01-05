"""Manager for loading and querying tool approval rules."""

import fnmatch
from pathlib import Path
from typing import Dict, Optional

from config.loader import load_approval_rules, ConfigError


class ApprovalRulesManager:
    """Manages tool approval rules loaded from approval_rules.yaml."""

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize ApprovalRulesManager.

        Args:
            config_path: Path to config directory containing approval_rules.yaml.
                        If None, defaults to ./config
        """
        self.config_path = config_path
        self._rules: Dict[str, bool] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        """Load approval rules from approval_rules.yaml."""
        try:
            self._rules = load_approval_rules(self.config_path)
        except ConfigError as e:
            # Graceful fallback: use empty rules dict if file doesn't exist or is invalid
            self._rules = {}
            # Log warning but don't fail initialization
            import sys

            print(
                f"Warning: Could not load approval rules: {e}. Using default (no approvals required).",
                file=sys.stderr,
            )

    def requires_approval(self, tool_name: str) -> bool:
        """
        Check if a tool requires approval.

        Supports:
        - Exact match: 'filesystem_read_file' matches 'filesystem_read_file'
        - Glob patterns: 'filesystem/*' matches 'filesystem_read_file', 'filesystem_write_file', etc.
        - Default: Returns False if tool not found in rules (safe default - no approval required)

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if approval is required, False otherwise (default: False)
        """
        # Check for exact match first (most specific)
        if tool_name in self._rules:
            return self._rules[tool_name]

        # Check for glob pattern matches
        for pattern, requires in self._rules.items():
            if "*" in pattern or "?" in pattern:
                if fnmatch.fnmatch(tool_name, pattern):
                    return requires

        # Default: no approval required (safe default)
        return False

    def reload_rules(self) -> None:
        """Reload rules from configuration file (useful for hot-reloading)."""
        self._load_rules()

    def get_all_rules(self) -> Dict[str, bool]:
        """
        Get all loaded approval rules.

        Returns:
            Dictionary mapping tool names/patterns to approval requirements
        """
        return self._rules.copy()

