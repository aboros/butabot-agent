"""Session manager for mapping thread IDs to fast-agent conversation history."""

import json
import logging
import re
from pathlib import Path
from typing import List, Optional

try:
    # Import from fast_agent (fast-agent-mcp package installs as fast_agent in newer versions)
    from fast_agent import PromptMessageExtended, load_prompt
except ImportError:
    # Fallback for type hints if fast-agent not available
    PromptMessageExtended = None
    load_prompt = None

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation history per thread using fast-agent's PromptMessageExtended format.
    
    Hybrid approach: stores active threads in memory for performance, persists to disk
    for durability across restarts. Lazy-loads from disk when threads are accessed.
    
    Storage format: JSON files compatible with fast-agent's `{"messages": [...]}` format.
    """
    
    def __init__(self, storage_dir: Optional[Path] = None, enable_persistence: bool = True):
        """
        Initialize the session manager.
        
        Args:
            storage_dir: Directory to store persistent session files. 
                        Defaults to `.sessions/` in current working directory.
                        If None and persistence is disabled, no directory is created.
            enable_persistence: If False, only use in-memory storage (no disk I/O).
        """
        # thread_id -> List[PromptMessageExtended]
        self._sessions: dict[str, List[PromptMessageExtended]] = {}
        self._enable_persistence = enable_persistence
        
        if enable_persistence:
            if storage_dir is None:
                storage_dir = Path(".sessions")
            self.storage_dir = Path(storage_dir)
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Session persistence enabled. Storage directory: {self.storage_dir}")
        else:
            self.storage_dir = None
            logger.info("Session persistence disabled. Using in-memory storage only.")
    
    def _sanitize_thread_id(self, thread_id: str) -> str:
        """
        Sanitize thread_id for use as filename.
        
        Args:
            thread_id: Original thread identifier
            
        Returns:
            Sanitized string safe for filesystem
        """
        # Replace unsafe characters with underscores
        # Keep alphanumeric, hyphens, and underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', thread_id)
        # Limit length to avoid filesystem issues
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized
    
    def _get_thread_file(self, thread_id: str) -> Optional[Path]:
        """
        Get the file path for a thread's history.
        
        Args:
            thread_id: Thread identifier
            
        Returns:
            Path to thread's history file, or None if persistence disabled
        """
        if not self._enable_persistence or self.storage_dir is None:
            return None
        safe_id = self._sanitize_thread_id(thread_id)
        return self.storage_dir / f"{safe_id}.json"
    
    def _load_thread_from_disk(self, thread_id: str) -> Optional[List[PromptMessageExtended]]:
        """
        Load a thread's history from disk (lazy loading).
        
        Args:
            thread_id: Thread identifier
            
        Returns:
            List of PromptMessageExtended objects, or None if not found or error
        """
        if not self._enable_persistence or load_prompt is None:
            return None
        
        thread_file = self._get_thread_file(thread_id)
        if thread_file is None or not thread_file.exists():
            return None
        
        try:
            # Use fast-agent's load_prompt for compatibility
            messages = load_prompt(thread_file)
            logger.debug(f"Loaded {len(messages)} messages from disk for thread {thread_id}")
            return messages
        except Exception as e:
            logger.warning(
                f"Failed to load thread history from {thread_file}: {e}. "
                f"Starting with empty history for thread {thread_id}."
            )
            return None
    
    def _save_thread_to_disk(self, thread_id: str, messages: List[PromptMessageExtended]) -> None:
        """
        Save a thread's history to disk.
        
        Args:
            thread_id: Thread identifier
            messages: List of PromptMessageExtended objects to save
        """
        if not self._enable_persistence or self.storage_dir is None:
            return
        
        thread_file = self._get_thread_file(thread_id)
        if thread_file is None:
            return
        
        try:
            # Convert PromptMessageExtended objects to JSON-serializable format
            # Fast-agent uses {"messages": [...]} format
            serializable_messages = []
            for msg in messages:
                # PromptMessageExtended should have a model_dump() or dict() method
                if hasattr(msg, 'model_dump'):
                    # Pydantic v2
                    serializable_messages.append(msg.model_dump())
                elif hasattr(msg, 'dict'):
                    # Pydantic v1
                    serializable_messages.append(msg.dict())
                elif hasattr(msg, '__dict__'):
                    # Fallback: use __dict__
                    serializable_messages.append(msg.__dict__)
                else:
                    # Last resort: try to serialize as-is
                    serializable_messages.append(msg)
            
            # Save in fast-agent compatible format
            with open(thread_file, 'w', encoding='utf-8') as f:
                json.dump({"messages": serializable_messages}, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"Saved {len(messages)} messages to disk for thread {thread_id}")
        except Exception as e:
            logger.error(
                f"Failed to save thread history to {thread_file}: {e}. "
                f"History will remain in memory only."
            )
    
    def get_messages(self, thread_id: str) -> List[PromptMessageExtended]:
        """
        Get full conversation history for a thread.
        
        Lazy-loads from disk if not in memory (hybrid approach).
        
        Args:
            thread_id: Thread/conversation identifier (platform-independent)
            
        Returns:
            List of PromptMessageExtended objects in conversation order
        """
        # If not in memory, try loading from disk (lazy loading)
        if thread_id not in self._sessions:
            disk_messages = self._load_thread_from_disk(thread_id)
            if disk_messages is not None:
                self._sessions[thread_id] = disk_messages
                logger.debug(f"Lazy-loaded thread {thread_id} from disk ({len(disk_messages)} messages)")
        
        # Return copy to prevent external mutation
        return self._sessions.get(thread_id, []).copy()
    
    def store_messages(self, thread_id: str, messages: List[PromptMessageExtended]) -> None:
        """
        Store/update full conversation history for a thread.
        
        Auto-saves to disk if persistence is enabled (hybrid approach).
        
        Args:
            thread_id: Thread/conversation identifier
            messages: List of PromptMessageExtended objects
        """
        # Store in memory (fast access)
        self._sessions[thread_id] = messages.copy()  # Store copy to prevent external mutation
        
        # Auto-save to disk (persistence)
        self._save_thread_to_disk(thread_id, messages)
    
    def add_message(self, thread_id: str, message: PromptMessageExtended) -> None:
        """
        Add a single message to conversation history.
        
        Auto-saves to disk if persistence is enabled.
        
        Args:
            thread_id: Thread/conversation identifier
            message: PromptMessageExtended object to add
        """
        if thread_id not in self._sessions:
            self._sessions[thread_id] = []
        self._sessions[thread_id].append(message)
        
        # Auto-save to disk after adding message
        self._save_thread_to_disk(thread_id, self._sessions[thread_id])
    
    def clear_session(self, thread_id: str) -> None:
        """
        Clear conversation history for a thread (both memory and disk).
        
        Args:
            thread_id: Thread/conversation identifier
        """
        # Remove from memory
        if thread_id in self._sessions:
            del self._sessions[thread_id]
        
        # Remove from disk if persistence enabled
        if self._enable_persistence:
            thread_file = self._get_thread_file(thread_id)
            if thread_file and thread_file.exists():
                try:
                    thread_file.unlink()
                    logger.debug(f"Deleted thread history file: {thread_file}")
                except Exception as e:
                    logger.warning(f"Failed to delete thread history file {thread_file}: {e}")
    
    def has_session(self, thread_id: str) -> bool:
        """
        Check if a session exists for a thread.
        
        Args:
            thread_id: Thread/conversation identifier
            
        Returns:
            True if session exists, False otherwise
        """
        return thread_id in self._sessions
    
    def remove_session(self, thread_id: str) -> bool:
        """
        Remove a session for a thread.
        
        Args:
            thread_id: Thread/conversation identifier
            
        Returns:
            True if session was removed, False if it didn't exist
        """
        if thread_id in self._sessions:
            del self._sessions[thread_id]
            return True
        return False
    
    def clear_all(self) -> None:
        """
        Clear all sessions from memory and disk (useful for testing or cleanup).
        
        Note: This removes all persisted history files if persistence is enabled.
        """
        # Clear memory
        self._sessions.clear()
        
        # Clear disk if persistence enabled
        if self._enable_persistence and self.storage_dir and self.storage_dir.exists():
            try:
                for thread_file in self.storage_dir.glob("*.json"):
                    thread_file.unlink()
                logger.info(f"Cleared all session files from {self.storage_dir}")
            except Exception as e:
                logger.warning(f"Failed to clear session files from {self.storage_dir}: {e}")

