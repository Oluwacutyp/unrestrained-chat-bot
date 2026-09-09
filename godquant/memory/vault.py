"""HistoryVault: append-only, ordered, per-chat conversation history.

Replaces search-based history (relevance-ordered, lossy) with a dedicated
table: every message persisted, nothing expires, ordered loads, restart-safe.
Thread-safe for the threaded HTTP server.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cid TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_history_cid ON history(cid, id);"""


class HistoryVault:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def append(self, cid: str, role: str, content: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history(cid,role,content,ts) VALUES(?,?,?,?)",
                (cid, role, (content or "")[:4000], time.time()))
            self._conn.commit()
            return int(cur.lastrowid)

    def load(self, cid: str, limit: int = 40) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,role,content,ts FROM history WHERE cid=? "
                "ORDER BY id DESC LIMIT ?", (cid, limit))
            rows = cur.fetchall()
        return [{"id": r[0], "role": r[1], "content": r[2], "ts": r[3]}
                for r in reversed(rows)]

    def count(self, cid: str) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM history WHERE cid=?",
                                     (cid,))
            return int(cur.fetchone()[0])

    def import_legacy(self, rows: list[dict]) -> int:
        """Best-effort import of old kind=chat blobs {cid,role,content}."""
        n = 0
        with self._lock:
            for m in rows:
                try:
                    if not m.get("cid") or not m.get("content"):
                        continue
                    self._conn.execute(
                        "INSERT INTO history(cid,role,content,ts) VALUES(?,?,?,?)",
                        (m["cid"], m.get("role", "user"),
                         str(m["content"])[:4000], time.time()))
                    n += 1
                except Exception:
                    continue
            self._conn.commit()
        return n

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass
