"""Tests for SessionManager persistence."""

import json
import tempfile
import unittest
from pathlib import Path

from bot.session_manager import SessionManager


class TestSessionManager(unittest.TestCase):
    def test_roundtrip_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            sm = SessionManager(persist_path=path)
            sm.store_session("thread-a", "sess-1")
            sm2 = SessionManager(persist_path=path)
            self.assertEqual(sm2.get_session("thread-a"), "sess-1")

    def test_clear_all_writes_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            sm = SessionManager(persist_path=path)
            sm.store_session("k", "v")
            sm.clear_all()
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {})


if __name__ == "__main__":
    unittest.main()
