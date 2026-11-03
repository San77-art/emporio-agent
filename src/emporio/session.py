"""Conversation session: message history (persisted as JSON) + per-conversation security state."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

MAX_FAILED_VERIFICATIONS = 3


def _is_tool_result(message: dict) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def trim_history(messages: list[dict], max_user_turns: int) -> list[dict]:
    """Keep the last ``max_user_turns`` customer turns, never splitting a tool_use/tool_result pair.

    A "turn" starts at a user message that is real customer text (not a tool result), so the window
    always begins at a clean boundary.
    """
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and not _is_tool_result(m)]
    if len(starts) <= max_user_turns:
        return messages
    return messages[starts[-max_user_turns]:]


class Session:
    """Holds the canonical (Anthropic-style) message list and the security state of one conversation."""

    def __init__(self, session_id: str = "default", path: Path | None = None):
        self.session_id = re.sub(r"[^A-Za-z0-9_-]", "_", session_id) or "default"
        self.path = path
        self.messages: list[dict] = []
        self.verified_customers: set[int] = set()  # customers who passed identity check in this chat
        self.failed_verifications = 0

    @property
    def locked(self) -> bool:
        return self.failed_verifications >= MAX_FAILED_VERIFICATIONS

    # -- persistence ---------------------------------------------------------------------------
    @classmethod
    def load(cls, session_id: str, state_dir: Path | None) -> "Session":
        path = None
        if state_dir is not None:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id) or "default"
            path = Path(state_dir) / "sessions" / f"{safe}.json"
        session = cls(session_id, path)
        if path and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session.messages = data.get("messages", [])
                session.verified_customers = set(data.get("verified_customers", []))
                session.failed_verifications = int(data.get("failed_verifications", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                session.messages = []  # corrupt file: start clean rather than crash the chat
        return session

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "messages": self.messages,
            "verified_customers": sorted(self.verified_customers),
            "failed_verifications": self.failed_verifications,
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)  # atomic: a crash never leaves a half-written history

    def reset(self) -> None:
        self.messages, self.verified_customers, self.failed_verifications = [], set(), 0
        if self.path and self.path.is_file():
            self.path.unlink()
