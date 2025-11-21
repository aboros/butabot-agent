"""Session manager for mapping Slack thread IDs to conversation message history."""

from typing import Optional


class SessionManager:
    """Manages conversation history per Slack thread.
    
    Stores full message arrays for each thread to maintain conversation context
    for the Anthropic Messages API.
    """
    
    def __init__(self):
        """Initialize the session manager with empty storage."""
        self._sessions: dict[str, list[dict]] = {}  # thread_id -> messages[]
    
    def get_messages(self, thread_id: str) -> list[dict]:
        """
        Get full conversation history for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            
        Returns:
            List of message dictionaries in conversation order
        """
        return self._sessions.get(thread_id, [])
    
    def store_messages(self, thread_id: str, messages: list[dict]):
        """
        Store/update full conversation history for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            messages: List of message dictionaries
        """
        self._sessions[thread_id] = messages
    
    def add_message(self, thread_id: str, message: dict):
        """
        Add a single message to conversation history.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            message: Message dictionary to add
        """
        if thread_id not in self._sessions:
            self._sessions[thread_id] = []
        self._sessions[thread_id].append(message)
    
    def clear_session(self, thread_id: str):
        """
        Clear conversation history for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
        """
        if thread_id in self._sessions:
            del self._sessions[thread_id]
    
    # Keep existing methods for compatibility during migration:
    def store_session(self, thread_id: str, session_id: str):
        """
        Deprecated: kept for compatibility.
        
        No longer used - session IDs are not needed with direct API usage.
        """
        pass
    
    def get_session(self, thread_id: str) -> Optional[str]:
        """
        Deprecated: kept for compatibility.
        
        No longer used - session IDs are not needed with direct API usage.
        """
        return None
    
    def has_session(self, thread_id: str) -> bool:
        """
        Check if a session exists for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            
        Returns:
            True if session exists, False otherwise
        """
        return thread_id in self._sessions
    
    def remove_session(self, thread_id: str) -> bool:
        """
        Remove a session for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            
        Returns:
            True if session was removed, False if it didn't exist
        """
        if thread_id in self._sessions:
            del self._sessions[thread_id]
            return True
        return False
    
    def clear_all(self):
        """Clear all sessions (useful for testing or cleanup)."""
        self._sessions.clear()

