"""History harvest: turn past chats into training trajectories.

Pure logic (no network, no telethon) so it stays unit-testable: the
Telethon driver in bridges/tg_history.py feeds plain dicts here, and the
rows land in the same trajectories.jsonl the server exports.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def pair_messages(msgs: list[dict], min_chars: int = 4,
                  ) -> list[tuple[str, str, float]]:
    """Sliding (user, response) pairs over oldest→newest messages.

    Each message followed by a *different sender's* message becomes one
    training pair. Empty/short/service rows are skipped; when the same
    sender talks twice in a row the latest message wins the slot.
    Input rows: {sender, text, ts}.
    """
    rows: list[tuple[str, str, float]] = []
    buf: tuple | None = None  # (sender, text, ts) awaiting a reply
    for m in msgs:
        t = _clean(m.get("text", ""))
        if not t or len(t) < min_chars:
            continue
        s = m.get("sender", "")
        ts = float(m.get("ts", 0) or 0)
        if buf is None:
            buf = (s, t, ts)
            continue
        if s != buf[0]:
            rows.append((buf[1][:2000], t[:4000], ts))
            buf = None
        else:
            buf = (s, t, ts)
    return rows


def channel_rows(posts: list[str], title: str, min_chars: int = 40,
                 ) -> list[tuple[str, str, float]]:
    """One-way channel posts → Alpaca-style voice-training rows."""
    rows: list[tuple[str, str, float]] = []
    for p in posts:
        t = _clean(p)
        if len(t) < min_chars:
            continue
        rows.append((f"Write a post for the channel '{title[:60]}':",
                     t[:4000], time.time()))
    return rows


def dedupe(rows: list[tuple[str, str, float]],
           ) -> list[tuple[str, str, float]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, float]] = []
    for u, a, ts in rows:
        if (u, a) in seen:
            continue
        seen.add((u, a))
        out.append((u, a, ts))
    return out


def load_state(workspace: str | Path) -> dict:
    p = Path(workspace) / "train" / "history_state.json"
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(workspace: str | Path, state: dict):
    d = Path(workspace) / "train"
    d.mkdir(parents=True, exist_ok=True)
    try:
        (d / "history_state.json").write_text(json.dumps(state))
    except Exception:
        pass
