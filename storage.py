from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT_DIR


class LocalStore:
    """Small local SQLite store for the demo admin queue and reviewer feedback."""

    def __init__(self, path: Path | None = None):
        self.path = path or (ROOT_DIR / "data" / "copilot.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS queue (
                ticket_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                lang TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                needs_human INTEGER NOT NULL,
                status TEXT NOT NULL,
                draft TEXT NOT NULL,
                summary_json TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                original_draft TEXT NOT NULL,
                edited_draft TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
            )""")

    def add_ticket(self, result: dict[str, Any], kind: str) -> None:
        ticket_id = str(result["ticket_id"])
        created_at = datetime.now(timezone.utc).isoformat()
        summary = {
            "extracted": result.get("extracted", {}),
            "reasons": result.get("reasons", []),
            "confidence": result.get("confidence"),
            "is_spam_or_duplicate": result.get("is_spam_or_duplicate", False),
            "draft_cited": result.get("draft_reply", {}).get("cited", []),
        }
        with self._connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO queue
                (ticket_id, created_at, kind, lang, category, priority, needs_human, status, draft, summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticket_id, created_at, kind, result.get("lang", "und"), result.get("category", "unknown"),
                 result.get("priority", "p2"), int(bool(result.get("needs_human"))), "needs_human" if result.get("needs_human") else "draft_ready",
                 result.get("draft_reply", {}).get("text", ""), json.dumps(summary, ensure_ascii=False)),
            )

    def list_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM queue ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [{
            "ticket_id": row["ticket_id"], "created_at": row["created_at"], "kind": row["kind"],
            "lang": row["lang"], "category": row["category"], "priority": row["priority"],
            "needs_human": bool(row["needs_human"]), "status": row["status"], "draft": row["draft"],
            "summary": json.loads(row["summary_json"]),
        } for row in rows]

    def save_feedback(self, *, ticket_id: str, action: str, original_draft: str, edited_draft: str, note: str = "") -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO feedback (ticket_id, created_at, action, original_draft, edited_draft, note) VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, created_at, action, original_draft, edited_draft, note),
            )
            db.execute("UPDATE queue SET status = ? WHERE ticket_id = ?", ("escalated" if action == "escalated" else "edited", ticket_id))
            return int(cursor.lastrowid)

    def feedback_stats(self) -> dict[str, int]:
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
            edits = db.execute("SELECT COUNT(*) FROM feedback WHERE action = 'edited'").fetchone()[0]
            escalations = db.execute("SELECT COUNT(*) FROM feedback WHERE action = 'escalated'").fetchone()[0]
        return {"total_feedback": int(total), "edits": int(edits), "escalations": int(escalations)}
