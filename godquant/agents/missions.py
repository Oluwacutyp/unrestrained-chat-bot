"""Missions v2: persistent, resumable multi-step goals.

A mission survives restarts (SQLite), executes in dependency waves, can
spawn one level of sub-missions, and reports progress to the owner's chat
via the outbox when `report_to` is set ("channel:chat_id").
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  goal TEXT NOT NULL,
  status TEXT DEFAULT 'running',   -- running|done|failed
  steps_json TEXT NOT NULL,        -- [{agent,instruction,depends_on,status,result}]
  context_json TEXT DEFAULT '{}',
  report_to TEXT DEFAULT '',
  reported_steps INTEGER DEFAULT 0,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
"""


class MissionStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     timeout=30)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "goal": r[1], "status": r[2],
                "steps": json.loads(r[3]), "context": json.loads(r[4] or "{}"),
                "report_to": r[5], "reported_steps": r[6],
                "created": r[7], "updated": r[8]}

    def create(self, goal: str, steps: list, context: dict | None = None,
               report_to: str = "") -> dict:
        now = time.time()
        norm = [{"agent": s.get("agent", "researcher"),
                 "instruction": s.get("instruction", goal),
                 "depends_on": s.get("depends_on", []),
                 "status": "pending", "result": ""} for s in steps]
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO missions(goal,status,steps_json,context_json,"
                "report_to,created,updated) VALUES(?,?,?,?,?,?,?)",
                (goal, "running", json.dumps(norm),
                 json.dumps(context or {}), report_to, now, now))
            self._conn.commit()
            mid = int(cur.lastrowid)
        return self.get(mid)

    def get(self, mid: int) -> dict | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,goal,status,steps_json,context_json,report_to,"
                "reported_steps,created,updated FROM missions WHERE id=?", (mid,))
            r = cur.fetchone()
            return self._row(r) if r else None

    def list(self, status: str = "", limit: int = 20) -> list[dict]:
        with self._lock:
            if status:
                cur = self._conn.execute(
                    "SELECT id,goal,status,steps_json,context_json,report_to,"
                    "reported_steps,created,updated FROM missions "
                    "WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
            else:
                cur = self._conn.execute(
                    "SELECT id,goal,status,steps_json,context_json,report_to,"
                    "reported_steps,created,updated FROM missions "
                    "ORDER BY id DESC LIMIT ?", (limit,))
            return [self._row(r) for r in cur.fetchall()]

    def save_step(self, mid: int, idx: int, status: str, result: str):
        m = self.get(mid)
        if not m or idx >= len(m["steps"]):
            return
        m["steps"][idx]["status"] = status
        m["steps"][idx]["result"] = (result or "")[:4000]
        with self._lock:
            self._conn.execute(
                "UPDATE missions SET steps_json=?,updated=? WHERE id=?",
                (json.dumps(m["steps"]), time.time(), mid))
            self._conn.commit()

    def finish(self, mid: int, status: str):
        with self._lock:
            self._conn.execute(
                "UPDATE missions SET status=?,updated=? WHERE id=?",
                (status, time.time(), mid))
            self._conn.commit()

    def unreported(self) -> list[dict]:
        """Running/done missions with report_to and fresh finished steps."""
        out = []
        for m in self.list():
            if not m["report_to"] or ":" not in m["report_to"]:
                continue
            done = [s for s in m["steps"] if s["status"] in ("ok", "fail")]
            if len(done) > m["reported_steps"]:
                out.append(m)
        return out

    def mark_reported(self, mid: int, n: int):
        with self._lock:
            self._conn.execute(
                "UPDATE missions SET reported_steps=?,updated=? WHERE id=?",
                (n, time.time(), mid))
            self._conn.commit()
