"""Continuous learning: LLM fact extraction + style lessons from every chat.

Two gears:
1. LLM extraction — when the free regex pass finds nothing but the message is
   substantive ("I'm Chidi btw", "everyone calls me Alhaji", "Mary here"),
   a small model pass pulls durable facts as JSON. Works with ANY phrasing.
2. Style lessons — from history shape: short-texter? question-asker? pidgin
   speaker? Stored as dossier notes so Devon mirrors them naturally.
"""
from __future__ import annotations

import json
import re

ALLOWED_KEYS = {"name", "like", "love", "hate", "home", "job", "age", "note",
                "no_petname"}

EXTRACTION_SYS = """You extract durable memories about the USER from ONE chat message.
Return a JSON list of {"key": ..., "value": ...} objects. Allowed keys: name,
like, love, hate, home, job, age, note, no_petname.
Rules:
- Only LONG-LASTING facts: identity, preferences, life details.
- "note" for anything else durable (max 120 chars).
- "no_petname" when they reject a nickname ("don't call me X", "not your X").
- NEVER extract: feelings about the bot ("i love you"), transient plans,
  insults, or anything said ABOUT someone else.
- [] when nothing durable. Output ONLY the JSON list, no commentary."""

PIDGIN_WORDS = ("wetin", "dey", "abeg", "nawa", "wahala", "no vex",
                "how far", "e be", "na so", "make we", "o ya")


def extract_facts_llm(router, text: str) -> list[tuple[str, str]]:
    """LLM extraction pass. Returns [] on any failure (never raises)."""
    try:
        raw = router.complete(EXTRACTION_SYS, f"MESSAGE: {(text or '')[:500]}",
                              agent="companion").text or ""
    except Exception:
        return []
    try:
        m = re.search(r"\[.*\]", raw, re.S)
        items = json.loads(m.group(0)) if m else []
    except Exception:
        return []
    out = []
    if isinstance(items, list):
        for it in items[:6]:
            if not isinstance(it, dict):
                continue
            k, v = str(it.get("key", "")), str(it.get("value", "")).strip()
            if k in ALLOWED_KEYS and 1 <= len(v) <= 120:
                out.append((k, v))
    # dedupe, keep order
    seen, ded = set(), []
    for kv in out:
        if kv not in seen:
            seen.add(kv)
            ded.append(kv)
    return ded


def style_lessons(history: list[dict]) -> list[tuple[str, str]]:
    """Infer texting-style notes from conversation shape. Pure."""
    user_msgs = [m["content"] for m in history if m.get("role") == "user"]
    if len(user_msgs) < 6:
        return []
    out = []
    avg = sum(len(m) for m in user_msgs) / len(user_msgs)
    if avg < 25:
        out.append(("note", "prefers short rapid-fire texts — match that energy"))
    elif avg > 200:
        out.append(("note", "sends long thoughtful paragraphs — give real depth back"))
    qs = sum(1 for m in user_msgs if "?" in m)
    if qs >= len(user_msgs) / 2:
        out.append(("note", "asks lots of questions — stay curious, ask back"))
    recent = " ".join(user_msgs[-5:]).lower()
    if any(w in recent for w in PIDGIN_WORDS):
        out.append(("note", "speaks Nigerian Pidgin — match it naturally"))
    if sum(m.count("😂") + m.count("🤣") for m in user_msgs) >= 4:
        out.append(("note", "heavy laugher — keep the humor coming"))
    return out
