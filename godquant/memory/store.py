"""Persistent memory: SQLite-backed store for facts, lessons, runs, costs.

Search is a dependency-free TF overlap scorer — good enough on-device,
swappable for embeddings later without changing the API.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,           -- fact|lesson|artifact|run
  content TEXT NOT NULL,
  tags TEXT DEFAULT '',
  score REAL DEFAULT 0,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS costs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT, provider TEXT, model TEXT,
  prompt_tokens INTEGER, completion_tokens INTEGER,
  cost_usd REAL, latency REAL, created REAL
);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
"""


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", s.lower()))


@dataclass
class Memory:
    id: int
    kind: str
    content: str
    tags: str
    score: float
    created: float


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---- writes ----
    def add(self, kind: str, content: str, tags: str = "", score: float = 0.0) -> int:
        cur = self._conn.execute(
            "INSERT INTO memories(kind, content, tags, score, created) VALUES(?,?,?,?,?)",
            (kind, content, tags, score, time.time()))
        self._conn.commit()
        return int(cur.lastrowid)

    def add_lesson(self, lesson: str, tags: str = "", score: float = 0.0) -> int:
        return self.add("lesson", lesson, tags, score)

    def log_cost(self, agent: str, provider: str, model: str, pt: int, ct: int,
                 cost: float, latency: float):
        self._conn.execute(
            "INSERT INTO costs(agent,provider,model,prompt_tokens,completion_tokens,"
            "cost_usd,latency,created) VALUES(?,?,?,?,?,?,?,?)",
            (agent, provider, model, pt, ct, cost, latency, time.time()))
        self._conn.commit()

    # ---- reads ----
    def _rows(self, where: str = "", args: tuple = ()):
        cur = self._conn.execute(
            f"SELECT id,kind,content,tags,score,created FROM memories {where} "
            f"ORDER BY id DESC LIMIT 500", args)
        return [Memory(*r) for r in cur.fetchall()]

    def lessons(self, limit: int = 20) -> list[Memory]:
        cur = self._conn.execute(
            "SELECT id,kind,content,tags,score,created FROM memories "
            "WHERE kind='lesson' ORDER BY score DESC, id DESC LIMIT ?", (limit,))
        return [Memory(*r) for r in cur.fetchall()]

    def search(self, query: str, kind: str = "", limit: int = 8) -> list[Memory]:
        q = _tokens(query)
        if not q:
            return []
        cands = self._rows("WHERE kind=?" if kind else "", (kind,) if kind else ())
        scored = []
        for m in cands:
            t = _tokens(m.content + " " + m.tags)
            overlap = len(q & t)
            if overlap:
                # TF-overlap + recency + quality prior
                scored.append((overlap * 2 + m.score * 0.1, m))
        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:limit]]

    def stats(self) -> dict:
        cur = self._conn.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind")
        counts = dict(cur.fetchall())
        cur = self._conn.execute("SELECT COALESCE(SUM(cost_usd),0), COUNT(*) FROM costs")
        cost, calls = cur.fetchone()
        return {"memories": counts, "llm_calls": calls, "spend_usd": round(cost or 0, 4)}

    def lesson_context(self, query: str, limit: int = 5) -> str:
        hits = self.search(query, kind="lesson", limit=limit) or self.lessons(limit)
        if not hits:
            return ""
        lines = "\n".join(f"- {m.content}" for m in hits)
        return f"\n[LEARNED LESSONS — apply these]\n{lines}\n"
