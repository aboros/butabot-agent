"""Configuration loader with environment variable substitution and validation."""

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

# Load environment variables from .env file
load_dotenv()


class ConfigError(Exception):
    """Exception raised for configuration errors."""

    pass


class FastAgentConfig(BaseModel):
    """Pydantic model for fastagent.config.yaml validation."""

    model: str = Field(default="claude-3-5-sonnet-20241022")
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    mcp_servers: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        allow_population_by_field_name = True


class SecretsConfig(BaseModel):
    """Pydantic model for fastagent.secrets.yaml validation."""

    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    slack_bot_token: Optional[str] = Field(None, alias="SLACK_BOT_TOKEN")
    discord_token: Optional[str] = Field(None, alias="DISCORD_TOKEN")

    class Config:
        allow_population_by_field_name = True


class ApprovalRulesConfig(BaseModel):
    """Pydantic model for approval_rules.yaml validation."""

    rules: Dict[str, bool] = Field(default_factory=dict)

    class Config:
        allow_population_by_field_name = True


def _substitute_env_vars(obj: Any) -> Any:
    """
    Recursively substitute environment variables in config.

    Handles ${VAR_NAME} syntax in strings, dicts, and lists.
    Also supports ${VAR_NAME:-default} syntax for default values.

    Args:
        obj: Config object (dict, list, or string)

    Returns:
        Object with environment variables substituted
    """
    if isinstance(obj, dict):
        return {key: _substitute_env_vars(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_substitute_env_vars(item) for item in obj]
    elif isinstance(obj, str):
        # Substitute ${VAR_NAME} patterns with environment variable values
        def replace_var(match):
            var_name = match.group(1)
            # Support default value syntax: ${VAR_NAME:-default}
            if ":-" in var_name:
                var_name, default = var_name.split(":-", 1)
                return os.getenv(var_name, default)
            return os.getenv(var_name, match.group(0))  # Return original if not found

        # Match ${VAR_NAME} or ${VAR_NAME:-default}
        pattern = r"\$\{([^}]+)\}"
        return re.sub(pattern, replace_var, obj)
    else:
        return obj


def load_yaml_config(path: Path) -> Dict[str, Any]:
    """
    Load and parse YAML configuration file with environment variable substitution.

    Args:
        path: Path to YAML configuration file

    Returns:
        Parsed configuration dictionary with environment variables substituted

    Raises:
        ConfigError: If file cannot be read or parsed
    """
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML file {path}: {e}")
    except IOError as e:
        raise ConfigError(f"Failed to read configuration file {path}: {e}")

    if raw_config is None:
        raw_config = {}

    # Substitute environment variables
    return _substitute_env_vars(raw_config)


def load_config(config_dir: Optional[Path] = None) -> FastAgentConfig:
    """
    Load and validate fastagent.config.yaml.

    Args:
        config_dir: Directory containing config files (defaults to ./config)

    Returns:
        Validated FastAgentConfig instance

    Raises:
        ConfigError: If config cannot be loaded or validated
    """
    if config_dir is None:
        config_dir = Path("config")

    config_path = config_dir / "fastagent.config.yaml"

    try:
        config_dict = load_yaml_config(config_path)
    except ConfigError as e:
        # If file doesn't exist, use defaults
        if "not found" in str(e):
            config_dict = {}
        else:
            raise

    # Transform config_dict to handle fast-agent format (mcp: { servers: {...} })
    # and legacy format (mcpServers: {...})
    if isinstance(config_dict, dict):
        # Handle fast-agent format: mcp: { servers: {...} }
        if "mcp" in config_dict and isinstance(config_dict["mcp"], dict) and "servers" in config_dict["mcp"]:
            config_dict = config_dict.copy()
            config_dict["mcp_servers"] = config_dict["mcp"]["servers"]
            # Don't remove "mcp" as fast-agent needs it, but our code uses mcp_servers
        # Handle legacy format: mcpServers: {...}
        elif "mcpServers" in config_dict:
            config_dict = config_dict.copy()
            config_dict["mcp_servers"] = config_dict["mcpServers"]

    try:
        return FastAgentConfig(**config_dict)
    except ValidationError as e:
        raise ConfigError(f"Configuration validation failed: {e}")


def load_secrets(config_dir: Optional[Path] = None) -> SecretsConfig:
    """
    Load and validate fastagent.secrets.yaml.

    Args:
        config_dir: Directory containing config files (defaults to ./config)

    Returns:
        Validated SecretsConfig instance

    Raises:
        ConfigError: If secrets cannot be loaded or validated
    """
    if config_dir is None:
        config_dir = Path("config")

    secrets_path = config_dir / "fastagent.secrets.yaml"

    try:
        secrets_dict = load_yaml_config(secrets_path)
    except ConfigError as e:
        # If file doesn't exist, use defaults (empty)
        if "not found" in str(e):
            secrets_dict = {}
        else:
            raise

    try:
        return SecretsConfig(**secrets_dict)
    except ValidationError as e:
        raise ConfigError(f"Secrets validation failed: {e}")


def load_approval_rules(config_dir: Optional[Path] = None) -> Dict[str, bool]:
    """
    Load and validate approval_rules.yaml.

    Args:
        config_dir: Directory containing config files (defaults to ./config)

    Returns:
        Dictionary mapping tool names to approval requirements (True/False)

    Raises:
        ConfigError: If approval rules cannot be loaded or validated
    """
    if config_dir is None:
        config_dir = Path("config")

    rules_path = config_dir / "approval_rules.yaml"

    try:
        rules_dict = load_yaml_config(rules_path)
    except ConfigError as e:
        # If file doesn't exist, use defaults (empty dict)
        if "not found" in str(e):
            rules_dict = {"rules": {}}
        else:
            raise

    try:
        config = ApprovalRulesConfig(**rules_dict)
        return config.rules
    except ValidationError as e:
        raise ConfigError(f"Approval rules validation failed: {e}")

