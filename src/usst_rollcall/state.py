from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Rollcall


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rollcall_events (
                rollcall_key TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                course_title TEXT,
                type_label TEXT,
                status TEXT,
                notification_sent_at TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def upsert_seen(self, rollcall: Rollcall) -> bool:
        key = rollcall.key
        existing = self.conn.execute(
            "SELECT rollcall_key FROM rollcall_events WHERE rollcall_key = ?",
            (key,),
        ).fetchone()
        timestamp = now_iso()
        if existing:
            self.conn.execute(
                """
                UPDATE rollcall_events
                SET last_seen_at = ?, status = ?, raw_json = ?
                WHERE rollcall_key = ?
                """,
                (timestamp, rollcall.status, rollcall.model_dump_json(), key),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            """
            INSERT INTO rollcall_events (
                rollcall_key, first_seen_at, last_seen_at, course_title,
                type_label, status, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                timestamp,
                timestamp,
                rollcall.display_title,
                rollcall.type_label,
                rollcall.status,
                rollcall.model_dump_json(),
            ),
        )
        self.conn.commit()
        return True

    def mark_notified(self, rollcall_key: str) -> None:
        self.conn.execute(
            "UPDATE rollcall_events SET notification_sent_at = ? WHERE rollcall_key = ?",
            (now_iso(), rollcall_key),
        )
        self.conn.commit()
