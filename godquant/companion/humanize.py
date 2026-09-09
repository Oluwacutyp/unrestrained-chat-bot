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


def read_delay(in_len: int, depth: float = 0.0,
               rng: random.Random | None = None) -> float:
    """Pause AFTER receiving, BEFORE typing. Heavy emotional texts (depth
    0-1) take longer to absorb — up to +4s."""
    r = rng or random
    return min(1.0 + in_len / 150 + r.uniform(0.5, 2.0)
               + min(4.0, max(0.0, depth) * 4.0), 9.0)


# mood → typing-speed multiplier (excited = fast bursts, sad = slow)
_MOOD_PACE = {"excited": 1.30, "happy": 1.15, "horny": 1.15, "needy": 1.10,
              "annoyed": 1.20, "angry": 1.40, "stressed": 1.10,
              "sad": 0.60, "tired": 0.55, "distant": 0.70, "jealous": 0.90}


def typing_delay(out_len: int, wpm: int = 45, mood: str = "neutral",
                 energy: str = "calm",
                 rng: random.Random | None = None) -> float:
    """Typing indicator, scaled to length AND performance: mood sets the pace
    (angry = rapid-fire, sad = slow), rapid exchanges type faster."""
    r = rng or random
    cps = max(wpm * 5 / 60, 1.0) * _MOOD_PACE.get(mood, 1.0)
    if energy == "rapid":
        cps *= 1.15
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


def _corrupt(word: str, r) -> str:
    """One human typo: doubled letter, swapped pair, or dropped letter."""
    i = 1 + int(r.random() * (len(word) - 1))
    k = r.random()
    if k < 0.4:
        return word[:i] + word[i] + word[i:]
    if k < 0.7 and i < len(word) - 1:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    return word[:i] + word[i + 1:]


def plan_typos(bubbles: list[str], p: float = 0.10,
               rng: random.Random | None = None) -> list[str]:
    """Human typo pass: rarely corrupt one lowercase word in a long bubble,
    then append a '*correction' bubble right after. Deterministic seeded."""
    r = rng or random
    out = []
    for b in bubbles:
        out.append(b)
        if len(b) < 60 or r.random() >= p:
            continue
        cands = [w for w in re.findall(r"[A-Za-z]{4,}", b) if w.islower()]
        if not cands:
            continue
        w = cands[int(r.random() * len(cands))]
        typo = _corrupt(w, r)
        if typo == w:
            continue
        out[-1] = out[-1].replace(w, typo, 1)
        out.append("*" + w)
    return out


class ExchangeTracker:
    """'Got distracted' pauses: after N rapid back-and-forths in a window,
    the bot spaces out once (40-100s) like a real person, then resets.
    Pure logic with injectable clock — bridges call note() per inbound."""

    def __init__(self, rapid_n: int = 10, rapid_window: float = 600.0,
                 pause_lo: float = 40.0, pause_hi: float = 100.0):
        self.rapid_n = rapid_n
        self.rapid_window = rapid_window
        self.pause_lo = pause_lo
        self.pause_hi = pause_hi
        self._hits: dict[str, deque] = {}

    def gap(self, chat: str, now: float | None = None) -> float | None:
        now = _time.monotonic() if now is None else now
        dq = self._hits.get(chat)
        return (now - dq[-1]) if dq else None

    def note(self, chat: str, now: float | None = None,
             rng: random.Random | None = None) -> float:
        """Record an inbound exchange. Returns distracted-pause seconds
        (0 = reply normally)."""
        now = _time.monotonic() if now is None else now
        dq = self._hits.setdefault(chat, deque())
        dq.append(now)
        while dq and now - dq[0] > self.rapid_window:
            dq.popleft()
        if len(dq) >= self.rapid_n:
            dq.clear()
            r = rng or random
            return self.pause_lo + r.random() * (self.pause_hi - self.pause_lo)
        return 0.0
