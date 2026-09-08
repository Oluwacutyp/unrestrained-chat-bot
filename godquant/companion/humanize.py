"""Human texting behavior: read pauses, typing time, bubbles, rate limits.

Pure, deterministic-under-seed math — bridges `await asleep(...)` the results.
`asleep` is monkeypatched to a no-op in tests so the suite stays instant.
"""
from __future__ import annotations

import asyncio
import random
import re
import time as _time
from collections import deque


async def asleep(seconds: float):
    await asyncio.sleep(max(0.0, seconds))


def read_delay(in_len: int, rng: random.Random | None = None) -> float:
    """Pause AFTER receiving, BEFORE typing (reading + thinking)."""
    r = rng or random
    return min(1.0 + in_len / 150 + r.uniform(0.5, 2.0), 7.0)


def typing_delay(out_len: int, wpm: int = 45,
                 rng: random.Random | None = None) -> float:
    """How long the typing indicator shows — scales with reply length."""
    r = rng or random
    cps = max(wpm * 5 / 60, 1.0)
    return min(1.2 + out_len / cps * r.uniform(0.85, 1.25), 22.0)


def bubble_gap(rng: random.Random | None = None) -> float:
    r = rng or random
    return r.uniform(0.8, 2.2)


def split_bubbles(text: str, max_bubbles: int = 3,
                  max_chars: int = 320) -> list[str]:
    """Split a long reply into human-style short bubbles.

    Paragraph breaks win first; then sentences are greedily packed to
    ~max_chars; overlong sentences are word-chunked. Overflow merges into
    the last bubble so output never exceeds max_bubbles.
    """
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) > 1:
        return paras[:max_bubbles]
    body = paras[0] if paras else text
    if len(body) <= max_chars:
        return [body]
    bubbles, cur = [], ""
    for s in re.split(r"(?<=[.!?…])\s+", body):
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars:  # word-chunk an overlong sentence
            if cur:
                bubbles.append(cur)
                cur = ""
            for w in s.split(" "):
                if len(cur) + len(w) + 1 <= max_chars or not cur:
                    cur = (cur + " " + w).strip()
                else:
                    bubbles.append(cur)
                    cur = w
            continue
        if len(cur) + len(s) + 1 <= max_chars or not cur:
            cur = (cur + " " + s).strip()
        else:
            bubbles.append(cur)
            cur = s
    if cur:
        bubbles.append(cur)
    if len(bubbles) > max_bubbles:  # merge overflow into last bubble
        bubbles = (bubbles[:max_bubbles - 1] +
                   [" ".join(bubbles[max_bubbles - 1:])])
    return bubbles


class RateLimiter:
    """Ban-safety: global msgs/min bucket + per-chat minimum gap."""

    def __init__(self, max_per_min: int = 20, min_chat_gap: float = 4.0):
        self.max_per_min = max_per_min
        self.min_gap = min_chat_gap
        self._hits: deque = deque()
        self._last: dict = {}

    def _prune(self, now: float):
        while self._hits and now - self._hits[0] > 60:
            self._hits.popleft()

    def wait_time(self, chat_id: str, now: float | None = None) -> float:
        """Seconds to wait before sending (0 = send now). Pure/testable."""
        now = _time.monotonic() if now is None else now
        self._prune(now)
        wait = 0.0
        if len(self._hits) >= self.max_per_min:
            wait = max(wait, 60 - (now - self._hits[0]) + 0.1)
        last = self._last.get(chat_id, 0.0)
        if now - last < self.min_gap:
            wait = max(wait, self.min_gap - (now - last))
        return max(wait, 0.0)

    async def wait(self, chat_id: str):
        while True:
            w = self.wait_time(chat_id)
            if w <= 0:
                break
            await asleep(min(w, 5.0))  # chunked so Ctrl+C stays responsive
        now = _time.monotonic()
        self._hits.append(now)
        self._last[chat_id] = now
