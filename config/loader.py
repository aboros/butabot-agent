"""Configuration loader for custom configuration files (e.g., approval_rules.yaml).

Note: Fast-agent automatically loads fastagent.config.yaml and fastagent.secrets.yaml
from the project root. This module is only for custom config files like approval_rules.yaml.
"""

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

