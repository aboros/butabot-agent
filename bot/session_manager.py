"""Session manager for mapping conversation keys to Agent SDK session IDs."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional


class SessionManager:
    """Manages mapping between conversation thread IDs and Agent SDK session IDs.

    The SDK provides session IDs in ResultMessage. We store them here for
    resume. Optional JSON persistence survives process restarts.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        """
        Args:
            persist_path: If set, load/save thread_id -> session_id as JSON (atomic writes).
        """
        self._sessions: dict[str, str] = {}
        self._persist_path = persist_path
        self._file_lock = threading.Lock()
        if self._persist_path:
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        path = self._persist_path
        if not path or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._sessions = {str(k): str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError) as e:
            from .logger import log_error

            log_error(f"SessionManager: failed to load {path}: {e}")

    def _save_to_disk(self) -> None:
        path = self._persist_path
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._sessions, indent=2, sort_keys=True)
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=".sessions-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def store_session(self, thread_id: str, session_id: str) -> None:
        """Store the SDK-provided session ID for a conversation key."""
        self._sessions[thread_id] = session_id
        if self._persist_path:
            with self._file_lock:
                self._save_to_disk()

    def get_session(self, thread_id: str) -> Optional[str]:
        """Return stored Agent SDK session ID if any."""
        return self._sessions.get(thread_id)

    def has_session(self, thread_id: str) -> bool:
        return thread_id in self._sessions

    def remove_session(self, thread_id: str) -> bool:
        if thread_id in self._sessions:
            del self._sessions[thread_id]
            if self._persist_path:
                with self._file_lock:
                    self._save_to_disk()
            return True
        return False

    def clear_all(self) -> None:
        self._sessions.clear()
        if self._persist_path:
            with self._file_lock:
                self._save_to_disk()


def session_persist_path_from_env() -> Optional[Path]:
    """Resolve SESSIONS_JSON_PATH or default under AGENT_DATA_DIR when PERSIST_SESSION_IDS is set."""
    explicit = os.getenv("SESSIONS_JSON_PATH", "").strip()
    if explicit:
        return Path(explicit)
    flag = os.getenv("PERSIST_SESSION_IDS", "").lower().strip()
    if flag in ("1", "true", "yes"):
        base = os.getenv("AGENT_DATA_DIR", "/app/data").strip()
        return Path(base) / "sessions.json"
    return None
