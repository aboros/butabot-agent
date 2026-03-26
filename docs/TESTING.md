# Testing

From the repository root:

```bash
PYTHONPATH=. python3 -m unittest discover tests -v
```

Tests cover `ConversationDispatch` ordering, `SessionManager` JSON persistence, and `conversation_key` helpers.
