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
  created REAL NOT NULL,
  layer TEXT DEFAULT 'semantic',  -- episodic|semantic|procedural
  scope TEXT DEFAULT 'global',    -- global|channel:chat|owner
  importance REAL DEFAULT 0.5,
  confidence REAL DEFAULT 0.8,
  pinned INTEGER DEFAULT 0,
  access_count INTEGER DEFAULT 0,
  last_access REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope TEXT NOT NULL,
  who TEXT DEFAULT '',
  what TEXT NOT NULL,
  salience REAL DEFAULT 0.5,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS working (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL,
  expires REAL NOT NULL
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
    layer: str = "semantic"
    scope: str = "global"
    importance: float = 0.5
    confidence: float = 0.8
    pinned: int = 0
    access_count: int = 0
    last_access: float = 0.0


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        have = {r[1] for r in self._conn.execute(
            "PRAGMA table_info(memories)").fetchall()}
        for col, ddl in (("layer", "TEXT DEFAULT 'semantic'"),
                         ("scope", "TEXT DEFAULT 'global'"),
                         ("importance", "REAL DEFAULT 0.5"),
                         ("confidence", "REAL DEFAULT 0.8"),
                         ("pinned", "INTEGER DEFAULT 0"),
                         ("access_count", "INTEGER DEFAULT 0"),
                         ("last_access", "REAL DEFAULT 0")):
            if col not in have:
                self._conn.execute(
                    f"ALTER TABLE memories ADD COLUMN {col} {ddl}")
        self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    # ---- writes ----
    def add(self, kind: str, content: str, tags: str = "", score: float = 0.0,
            layer: str = "semantic", scope: str = "global",
            importance: float = 0.5, confidence: float = 0.8,
            pinned: bool = False) -> int:
        cur = self._conn.execute(
            "INSERT INTO memories(kind, content, tags, score, created, layer,"
            "scope, importance, confidence, pinned) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (kind, content, tags, score, time.time(), layer, scope,
             importance, confidence, 1 if pinned else 0))
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
    _COLS = ("id,kind,content,tags,score,created,layer,scope,importance,"
             "confidence,pinned,access_count,last_access")

    def _rows(self, where: str = "", args: tuple = ()):
        cur = self._conn.execute(
            f"SELECT {self._COLS} FROM memories {where} "
            f"ORDER BY id DESC LIMIT 500", args)
        return [Memory(*r) for r in cur.fetchall()]

    def lessons(self, limit: int = 20) -> list[Memory]:
        cur = self._conn.execute(
            f"SELECT {self._COLS} FROM memories "
            "WHERE kind='lesson' ORDER BY score DESC, id DESC LIMIT ?", (limit,))
        return [Memory(*r) for r in cur.fetchall()]

    def candidates(self, limit: int = 2000) -> list[Memory]:
        """All memories newest-first (Mind ranks these with BM25)."""
        cur = self._conn.execute(
            f"SELECT {self._COLS} FROM memories ORDER BY id DESC LIMIT ?",
            (limit,))
        return [Memory(*r) for r in cur.fetchall()]

    def get(self, mid: int) -> Memory | None:
        cur = self._conn.execute(f"SELECT {self._COLS} FROM memories WHERE id=?",
                                 (mid,))
        r = cur.fetchone()
        return Memory(*r) if r else None

    def update(self, mid: int, content: str | None = None,
               importance: float | None = None,
               confidence: float | None = None,
               pinned: bool | None = None) -> bool:
        sets, args = [], []
        if content is not None:
            sets.append("content=?")
            args.append(content)
        if importance is not None:
            sets.append("importance=?")
            args.append(importance)
        if confidence is not None:
            sets.append("confidence=?")
            args.append(confidence)
        if pinned is not None:
            sets.append("pinned=?")
            args.append(1 if pinned else 0)
        if not sets:
            return False
        args.append(mid)
        cur = self._conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id=?", tuple(args))
        self._conn.commit()
        return cur.rowcount > 0

    def delete(self, mid: int) -> bool:
        cur = self._conn.execute("DELETE FROM memories WHERE id=?", (mid,))
        self._conn.commit()
        return cur.rowcount > 0

    def touch(self, mid: int):
        self._conn.execute(
            "UPDATE memories SET access_count=access_count+1,last_access=? "
            "WHERE id=?", (time.time(), mid))
        self._conn.commit()

    def layer_stats(self) -> dict:
        cur = self._conn.execute(
            "SELECT layer, COUNT(*) FROM memories GROUP BY layer")
        return dict(cur.fetchall())

    def add_episode(self, scope: str, who: str, what: str,
                    salience: float = 0.5) -> int:
        cur = self._conn.execute(
            "INSERT INTO episodes(scope,who,what,salience,created) VALUES(?,?,?,?,?)",
            (scope, who, what[:500], salience, time.time()))
        self._conn.commit()
        return int(cur.lastrowid)

    def recent_episodes(self, scope: str = "", limit: int = 20) -> list[dict]:
        if scope:
            cur = self._conn.execute(
                "SELECT scope,who,what,salience,created FROM episodes "
                "WHERE scope=? ORDER BY id DESC LIMIT ?", (scope, limit))
        else:
            cur = self._conn.execute(
                "SELECT scope,who,what,salience,created FROM episodes "
                "ORDER BY id DESC LIMIT ?", (limit,))
        return [{"scope": r[0], "who": r[1], "what": r[2],
                 "salience": r[3], "created": r[4]} for r in cur.fetchall()]

    def wset(self, k: str, v: str, ttl_s: float = 3600):
        self._conn.execute(
            "INSERT OR REPLACE INTO working(k,v,expires) VALUES(?,?,?)",
            (k, v, time.time() + ttl_s))
        self._conn.commit()

    def wget(self, k: str, default: str = "") -> str:
        self._conn.execute("DELETE FROM working WHERE expires<=?",
                           (time.time(),))
        self._conn.commit()
        cur = self._conn.execute("SELECT v FROM working WHERE k=?", (k,))
        r = cur.fetchone()
        return r[0] if r else default

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
