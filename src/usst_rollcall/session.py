from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import SessionTokens


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SessionTokens:
        if not self.path.exists():
            return SessionTokens()
        return SessionTokens.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, tokens: SessionTokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tokens.updated_at = datetime.now(timezone.utc)
        self.path.write_text(tokens.model_dump_json(indent=2), encoding="utf-8")

    def update(self, *, x_session_id: str | None = None, cookies: dict[str, str] | None = None) -> SessionTokens:
        tokens = self.load()
        if x_session_id:
            tokens.x_session_id = x_session_id
        if cookies:
            tokens.cookies.update(cookies)
        self.save(tokens)
        return tokens
