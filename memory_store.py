import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional


class MemoryStore:
    """SQLite-backed local memory database for conversation turns, task runs, preferences, and facts."""

    DEFAULT_DB_PATH = r"C:\Users\USER\kenny\jasper_memory.db"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                raw_transcript TEXT,
                corrected_transcript TEXT,
                route TEXT,
                action_name TEXT,
                response TEXT,
                created_at REAL NOT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary TEXT NOT NULL,
                turn_range TEXT,
                created_at REAL NOT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                action_name TEXT NOT NULL,
                arguments_json TEXT,
                status TEXT NOT NULL,
                result_json TEXT,
                duration_sec REAL,
                created_at REAL NOT NULL
            )
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                source TEXT,
                confidence REAL DEFAULT 1.0,
                created_at REAL NOT NULL
            )
            """)
            conn.commit()

    @staticmethod
    def redact_secrets(text: str) -> str:
        """Redact potential API keys, passwords, or credentials before saving."""
        if not text:
            return text
        # Redact patterns like sk-..., Bearer ..., key-..., passwords
        redacted = re.sub(r"(?:sk-[a-zA-Z0-9_]{16,}|bearer\s+[a-zA-Z0-9_\-\.]{16,}|password\s*=\s*\S+)", "[REDACTED_SECRET]", text, flags=re.IGNORECASE)
        return redacted

    def add_turn(
        self,
        request_id: str,
        raw_transcript: str,
        route: str,
        action_name: Optional[str],
        response: str,
        corrected_transcript: Optional[str] = None
    ) -> None:
        """Record a conversation turn."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO conversation_turns (request_id, raw_transcript, corrected_transcript, route, action_name, response, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    self.redact_secrets(raw_transcript),
                    self.redact_secrets(corrected_transcript or raw_transcript),
                    route,
                    action_name,
                    self.redact_secrets(response),
                    time.time(),
                ),
            )
            conn.commit()

    def get_recent_turns(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get the N most recent conversation turns."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT request_id, raw_transcript, corrected_transcript, route, action_name, response, created_at
                FROM conversation_turns
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            turns = [dict(row) for row in reversed(rows)]
            return turns

    def log_task_run(
        self,
        request_id: str,
        action_name: str,
        arguments: Dict[str, Any],
        status: str,
        result: Any,
        duration_sec: float
    ) -> None:
        """Log a local action or tool execution."""
        import json
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO task_runs (request_id, action_name, arguments_json, status, result_json, duration_sec, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    action_name,
                    self.redact_secrets(json.dumps(arguments)),
                    status,
                    self.redact_secrets(json.dumps(result)),
                    duration_sec,
                    time.time(),
                ),
            )
            conn.commit()

    def add_fact(self, fact: str, source: str = "user", confidence: float = 1.0) -> None:
        """Add a durable fact."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO facts (fact, source, confidence, created_at) VALUES (?, ?, ?, ?)",
                (self.redact_secrets(fact), source, confidence, time.time()),
            )
            conn.commit()

    def get_facts(self) -> List[str]:
        """Retrieve all stored durable facts."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT fact FROM facts ORDER BY id ASC")
            return [row["fact"] for row in cursor.fetchall()]

    def set_preference(self, key: str, value: str) -> None:
        """Set a user preference."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, time.time()),
            )
            conn.commit()

    def get_preferences(self) -> Dict[str, str]:
        """Get all user preferences."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM user_preferences")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    def forget_all(self) -> None:
        """Reset and wipe all conversation memory tables."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM conversation_turns")
            cursor.execute("DELETE FROM conversation_summaries")
            cursor.execute("DELETE FROM user_preferences")
            cursor.execute("DELETE FROM task_runs")
            cursor.execute("DELETE FROM facts")
            conn.commit()


_memory_instance: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = MemoryStore()
    return _memory_instance
