"""Factoid manager for handling simple string-triggered responses."""

import html
import json
import random
import time
from pathlib import Path
from typing import Dict, Optional, Any

from .logger import log_info, log_error, log_warning, log_factoid_reload, log_factoid_cooldown


class FactoidManager:
    """Manages factoids - simple responses triggered by exact string matches."""
    
    def __init__(self, factoids_file: Optional[Path] = None):
        """
        Initialize factoid manager.
        
        Args:
            factoids_file: Path to factoids JSON file (defaults to /app/factoids.json)
        """
        if factoids_file is None:
            # Default to /app/factoids.json (Docker container path)
            # Fall back to ./factoids.json for local development
            docker_path = Path("/app/factoids.json")
            local_path = Path("factoids.json")
            if docker_path.exists():
                factoids_file = docker_path
            elif local_path.exists():
                factoids_file = local_path
            else:
                factoids_file = docker_path  # Will try to load, handle error gracefully
        
        self.factoids_file = factoids_file
        self._factoids: Dict[str, Dict[str, Any]] = {}  # trigger -> {response, mention_only}
        self._cooldowns: Dict[str, float] = {}  # trigger -> last trigger timestamp
        
        # Load factoids on initialization
        self.load_factoids()
    
    def load_factoids(self) -> bool:
        """
        Load factoids from JSON file.
        
        Returns:
            True if loaded successfully, False otherwise
        """
        if not self.factoids_file.exists():
            log_warning(f"Factoids file not found: {self.factoids_file}. Starting with empty factoids.")
            self._factoids = {}
            log_factoid_reload(success=False, factoid_count=0, error="File not found")
            return False
        
        try:
            with open(self.factoids_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate and load factoids
            loaded_factoids = {}
            errors = []
            
            for trigger, config in data.items():
                if not isinstance(trigger, str):
                    errors.append(f"Invalid trigger (not a string): {trigger}")
                    continue
                
                if not isinstance(config, dict):
                    errors.append(f"Invalid config for '{trigger}': not a dict")
                    continue
                
                response = config.get("response")
                mention_only = config.get("mention_only", False)
                
                # Support both single string and array of strings for responses
                if isinstance(response, str):
                    # Single response - convert to list for consistent handling
                    responses = [response]
                elif isinstance(response, list):
                    # Multiple responses - validate all are strings
                    if not all(isinstance(r, str) for r in response):
                        errors.append(f"Invalid response for '{trigger}': array contains non-string values")
                        continue
                    if len(response) == 0:
                        errors.append(f"Invalid response for '{trigger}': empty array")
                        continue
                    responses = response
                else:
                    errors.append(f"Invalid response for '{trigger}': must be a string or array of strings")
                    continue
                
                if not isinstance(mention_only, bool):
                    errors.append(f"Invalid mention_only for '{trigger}': not a boolean")
                    continue
                
                loaded_factoids[trigger] = {
                    "responses": responses,  # Store as list for consistent handling
                    "mention_only": mention_only
                }
            
            # Update factoids dict
            old_count = len(self._factoids)
            self._factoids = loaded_factoids
            new_count = len(self._factoids)
            
            if errors:
                error_msg = "; ".join(errors)
                log_error(f"Errors loading some factoids: {error_msg}")
                log_factoid_reload(success=True, factoid_count=new_count, error=error_msg)
            else:
                log_factoid_reload(success=True, factoid_count=new_count)
            
            if old_count != new_count:
                log_info(f"Loaded {new_count} factoids from {self.factoids_file}")
            
            return True
            
        except json.JSONDecodeError as e:
            log_error(f"Invalid JSON in factoids file {self.factoids_file}: {e}")
            log_factoid_reload(success=False, factoid_count=len(self._factoids), error=f"JSON decode error: {e}")
            return False
        except Exception as e:
            log_error(f"Error loading factoids from {self.factoids_file}: {e}")
            log_factoid_reload(success=False, factoid_count=len(self._factoids), error=str(e))
            return False
    
    def reload_factoids(self) -> bool:
        """
        Reload factoids from file (for runtime reloading).
        
        Returns:
            True if reloaded successfully, False otherwise
        """
        return self.load_factoids()
    
    def check_factoid(self, message_text: str, is_mention: bool) -> Optional[str]:
        """
        Check if message matches a factoid and cooldown allows it.
        
        Args:
            message_text: The message text to check
            is_mention: Whether this is a bot mention event
        
        Returns:
            Response text if factoid matches and cooldown allows, None otherwise
        """
        # Decode HTML entities (Slack may encode special characters like ? as &quest;)
        normalized_text = html.unescape(message_text)
        
        # Check for exact match (try both original and normalized text)
        trigger = None
        if message_text in self._factoids:
            trigger = message_text
        elif normalized_text in self._factoids:
            trigger = normalized_text
        
        if trigger is None:
            return None
        
        factoid = self._factoids[trigger]
        
        # Check mention_only requirement
        if factoid["mention_only"] and not is_mention:
            return None
        
        # Check cooldown (use trigger key for cooldown tracking)
        if self._is_on_cooldown(trigger):
            log_factoid_cooldown(trigger)
            return None
        
        # Record trigger (use trigger key for cooldown tracking)
        self._record_trigger(trigger)
        
        # Select random response if multiple available
        responses = factoid["responses"]
        if len(responses) == 1:
            return responses[0]
        else:
            return random.choice(responses)
    
    def _is_on_cooldown(self, trigger: str) -> bool:
        """
        Check if trigger is on cooldown (30 seconds).
        
        Args:
            trigger: The trigger string to check
        
        Returns:
            True if on cooldown, False otherwise
        """
        last_trigger_time = self._cooldowns.get(trigger, 0)
        time_since_trigger = time.time() - last_trigger_time
        return time_since_trigger < 30.0
    
    def _record_trigger(self, trigger: str):
        """
        Record current timestamp for trigger (for cooldown tracking).
        
        Args:
            trigger: The trigger string that was matched
        """
        self._cooldowns[trigger] = time.time()

