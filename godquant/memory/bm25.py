"""Dependency-free retrieval math: BM25 ranking + trigram fuzzy match.

Pure functions, no numpy — instant on phone CPUs for candidate sets of a
few thousand docs. When an embedding backend is configured, Mind reranks
these candidates with vectors; until then this IS the recall engine.
"""
from __future__ import annotations

import math
import re

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def tokenize(s: str) -> list[str]:
    return _TOKEN_RE.findall((s or "").lower())


def bm25_scores(query: list[str], docs: list[list[str]],
                k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Okapi BM25 of one query against tokenized docs. Pure."""
    if not query or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    lens = [len(d) or 1 for d in docs]
    avg = sum(lens) / n
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    out = []
    for d, dl in zip(docs, lens):
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(query):
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avg))
        out.append(s)
    return out


def trigrams(s: str) -> set[str]:
    t = f"  {(s or '').lower()} "
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else set()


def trigram_sim(a: str, b: str) -> float:
    """Dice coefficient over char trigrams. 1.0 = identical."""
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))
