"""Configuration loader module for custom configuration files."""

from .loader import load_approval_rules, ConfigError

__all__ = ["load_approval_rules", "ConfigError"]

