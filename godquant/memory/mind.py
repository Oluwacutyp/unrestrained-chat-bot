"""Mind — unified god-tier memory facade over MemoryStore + BondStore.

Layers: episodic (events), semantic (facts), procedural (how-to).
Scopes: `global` | `owner` | `channel:chat`. Default-deny: a chat recall
sees global + its own scope only; scope="*" (owner tooling) sees everything.
Retrieval: BM25 over candidates + pinned/importance/recency priors, with
access tracking (recall strengthens memory). `chat` blobs are history, not
knowledge — always excluded from recall.
"""
from __future__ import annotations

import time

from godquant.memory.bm25 import bm25_scores, tokenize

_SKIP_KINDS = frozenset({"chat"})


class Mind:
    def __init__(self, memory, bonds):
        self.mem = memory
        self.bonds = bonds

    # ---- writes ----
    def remember(self, content: str, layer: str = "semantic",
                 scope: str = "global", importance: float = 0.5,
                 confidence: float = 0.8, kind: str = "fact",
                 tags: str = "", pinned: bool = False) -> int:
        return self.mem.add(kind, (content or "").strip()[:2000], tags, 0.0,
                            layer, scope, importance, confidence, pinned)

    def log_episode(self, scope: str, who: str, what: str,
                    salience: float = 0.5) -> int:
        return self.mem.add_episode(scope, who, what, salience)

    # ---- reads ----
    def _visible(self, scope: str | None) -> list:
        out = []
        for m in self.mem.candidates():
            if m.kind in _SKIP_KINDS:
                continue
            if scope == "*":
                out.append(m)
            elif m.scope == "global" or (scope and m.scope == scope):
                out.append(m)
        return out

    def recall(self, query: str, scope: str | None = None,
               layers: list | None = None, limit: int = 8) -> list[dict]:
        vis = self._visible(scope)
        if layers:
            vis = [m for m in vis if m.layer in layers]
        if not vis or not (query or "").strip():
            return []
        scores = bm25_scores(tokenize(query),
                             [tokenize(m.content + " " + m.tags) for m in vis])
        now = time.time()
        ranked = []
        for m, s in zip(vis, scores):
            if s <= 0:
                continue
            boost = 1.0 + (2.0 if m.pinned else 0.0) + (m.importance or 0)
            age_days = (now - m.created) / 86400 if m.created else 365
            rec = 1.0 / (1.0 + age_days / 30)
            ranked.append((s * boost * (0.5 + 0.5 * rec), m))
        ranked.sort(key=lambda x: -x[0])
        out = []
        for _, m in ranked[:limit]:
            try:
                self.mem.touch(m.id)
            except Exception:
                pass
            out.append({"id": m.id, "content": m.content, "layer": m.layer,
                        "scope": m.scope, "importance": m.importance,
                        "pinned": bool(m.pinned)})
        return out

    def recall_block(self, query: str, scope: str | None = None,
                     limit: int = 4) -> str:
        hits = self.recall(query, scope, limit=limit)
        if not hits:
            return ""
        lines = "\n".join(f"- [{h['layer']}] {h['content'][:220]}"
                          for h in hits)
        return f"\n[MEMORY — things you know, still true]\n{lines}\n"

    # ---- manage ----
    def pin(self, mid: int, pinned: bool = True) -> bool:
        return self.mem.update(mid, pinned=pinned)

    def edit(self, mid: int, content: str) -> bool:
        return self.mem.update(mid, content=content)

    def forget(self, mid: int) -> bool:
        return self.mem.delete(mid)

    def inspect(self, scope: str = "", layer: str = "",
                limit: int = 50) -> list[dict]:
        out = []
        for m in self.mem.candidates(limit=500):
            if m.kind in _SKIP_KINDS:
                continue
            if scope and m.scope != scope:
                continue
            if layer and m.layer != layer:
                continue
            out.append({"id": m.id, "kind": m.kind, "layer": m.layer,
                        "scope": m.scope, "content": m.content[:300],
                        "importance": m.importance, "pinned": bool(m.pinned),
                        "access_count": m.access_count})
            if len(out) >= limit:
                break
        return out

    def episodes(self, scope: str = "", limit: int = 10) -> list[dict]:
        return self.mem.recent_episodes(scope, limit)
