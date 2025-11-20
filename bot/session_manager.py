"""Session manager for mapping Slack thread IDs to Agent SDK session IDs."""

from typing import Optional


class SessionManager:
    """Manages mapping between Slack thread IDs and Agent SDK session IDs.
    
    The SDK provides session IDs in ResultMessage. We store them here
    for potential future resume (e.g., after restart).
    """
    
    def __init__(self):
        """Initialize the session manager with empty storage."""
        self._sessions: dict[str, str] = {}  # thread_id -> session_id (from SDK)
    
    def store_session(self, thread_id: str, session_id: str):
        """
        Store the SDK-provided session ID for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            session_id: Agent SDK session ID (provided by SDK in ResultMessage)
        """
        self._sessions[thread_id] = session_id
    
    def get_session(self, thread_id: str) -> Optional[str]:
        """
        Get existing session ID for a thread.
        
        Args:
            thread_id: Slack thread timestamp (thread_ts)
            
        Returns:
            Agent SDK session ID if exists, None otherwise
        """
        return self._sessions.get(thread_id)
    
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

