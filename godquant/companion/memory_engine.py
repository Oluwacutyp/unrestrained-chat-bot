"""Memory engine — superb recall: facts, dossier, boundaries, summaries.

Heuristic fact extraction (no LLM needed, works offline): names, boundaries
("don't call me that"), likes/hates, home, job, age, and explicit
"remember that ..." notes. Facts live in the bonds DB (bond_facts table) so
recall is exact per-sender — never mixed up between people.
"""
from __future__ import annotations

import hashlib
import re

# single-value keys (new value replaces old); everything else accumulates
SINGLE_KEYS = {"name", "home", "job", "age", "no_petname"}

_CALLME_BLOCK = {"later", "back", "maybe", "when", "if", "tomorrow", "tonight",
                 "sometime", "again"}
_FEELING_BLOCK = {"good", "fine", "tired", "busy", "happy", "sad", "ok",
                  "okay", "here", "there", "home", "back", "late", "ready",
                  "glad", "sorry", "lost", "bored", "drunk", "high", "asleep",
                  "awake", "alone", "lonely", "scared", "sick", "broke",
                  "done", "free", "stuck", "up", "down", "in", "out"}
_LIKE_BLOCK = {"you", "u", "it", "this", "that", "them", "here", "there",
               "everything", "nothing"}


def _clean(v: str, maxlen: int = 60) -> str:
    return re.sub(r"\s+", " ", (v or "").strip().strip(".,!?\"'")).strip()[:maxlen]


def extract_facts(text: str) -> list[tuple[str, str]]:
    """Pull durable (key, value) facts from one user message. Pure."""
    t = (text or "").strip()
    if not t or len(t) > 600:
        return []
    low = t.lower()
    facts: list[tuple[str, str]] = []

    # explicit memory: "remember that my dog is Rex" (highest trust,
    # original case kept)
    m = re.search(r"\bremember that (.+)", t, re.I)
    if m and not low.startswith("remember when"):
        v = _clean(m.group(1), 200)
        if len(v) > 3:
            facts.append(("note", v))
    else:
        m = re.search(r"\bremember:?\s+(.+)", t, re.I)
        if m and not low.startswith("remember when") and \
                not m.group(1).lower().startswith(("when ", "how ")):
            v = _clean(m.group(1), 200)
            if len(v) > 3:
                facts.append(("note", v))

    # name: "my name is X" / "call me X"
    m = re.search(r"\bmy name is ([a-z][\w'\-]{1,20})", low)
    if m:
        facts.append(("name", m.group(1).capitalize()))
    m = re.search(r"\bcall me ([a-z][\w'\-]{1,20})\b", low)
    if m and m.group(1) not in _CALLME_BLOCK:
        facts.append(("name", m.group(1).capitalize()))

    # BOUNDARIES: "don't call me X" / "stop calling me X" / "not your X"
    m = re.search(r"\b(?:don't|dont|do not) call me (?:your |my )?([\w' ]{1,20})",
                  low)
    if m:
        v = _clean(m.group(1), 20)
        facts.append(("no_petname", "*" if v in ("that", "that stuff", "names")
                      else v))
    m = re.search(r"\bstop calling me ([\w' ]{1,20})", low)
    if m:
        v = _clean(m.group(1), 20)
        facts.append(("no_petname", "*" if v == "that" else v))
    m = re.search(r"\bnot your ([\w']{1,20})", low)
    if m:
        facts.append(("no_petname", m.group(1)))

    # likes / loves / hates
    for m in re.finditer(r"\bi (?:really |so )?(like|love|hate) "
                         r"([a-z][a-z' ]{2,40}?)(?:[.!?]|$)", low):
        v = _clean(m.group(2), 40)
        if v and v not in _LIKE_BLOCK and "you" not in v.split():
            facts.append((m.group(1), v))

    # home: "i live in X" / "i'm from X"
    m = re.search(r"\bi live in ([a-z][\w'. \-]{2,40})", low)
    if m:
        facts.append(("home", _clean(m.group(1), 40).title()))
    m = re.search(r"\bi'?m from ([a-z][\w'. \-]{2,40})", low)
    if m:
        facts.append(("home", _clean(m.group(1), 40).title()))

    # job: "i work as X" / "i'm a nurse" (feeling-words blocked)
    m = re.search(r"\bi work as ([\w ]{3,30})", low)
    if m:
        facts.append(("job", _clean(m.group(1), 30)))
    m = re.search(r"\bi'?m an? ([a-z][\w ]{2,29})", low)
    if m:
        v = _clean(m.group(1), 30)
        if v and v.split()[0] not in _FEELING_BLOCK and len(v.split()) <= 4:
            facts.append(("job", v))

    # age / birthday
    m = re.search(r"\bi'?m (\d{1,2}) (?:years old|y\.?o\.?|yrs old)", low)
    if m:
        facts.append(("age", m.group(1)))
    m = re.search(r"\bmy birthday is ([a-z0-9, ]{3,30})", low)
    if m:
        facts.append(("age", "bday:" + _clean(m.group(1), 30)))

    # dedupe, keep order
    seen, out = set(), []
    for kv in facts:
        if kv not in seen:
            seen.add(kv)
            out.append(kv)
    return out


def dossier_text(facts: list[tuple[str, str]], display: str) -> str:
    """Render facts as a HARD prompt block. Empty string when no facts."""
    if not facts:
        return ""
    who = (display or "them").strip() or "them"
    lines = []
    for k, v in facts:
        if k == "name":
            lines.append(f"- their name is {v} — use it")
        elif k == "no_petname":
            what = "ANY pet names" if v == "*" else f'"{v}"'
            lines.append(f"- BOUNDARY: NEVER call them {what} — they explicitly "
                         f"rejected it. Apologize once if you slipped, then obey.")
        elif k in ("like", "love", "hate"):
            lines.append(f"- they {k}: {v}")
        elif k == "home":
            lines.append(f"- they live / are from: {v}")
        elif k == "job":
            lines.append(f"- they work as: {v}")
        elif k == "age":
            lines.append(f"- age/birthday: {v}")
        else:
            lines.append(f"- remember: {v}")
    joined = "\n".join(lines)
    return (f"[DOSSIER — {who}: HARD FACTS. These are TRUE and override any "
            f"guess. Never contradict them, never mix them up with anyone "
            f"else.]\n{joined}")


def clean_reply(reply: str, user_message: str) -> str:
    """Strip echo/quotation artifacts: the bot must never parrot the user
    back (the 'I'm talking to you' → 'I'm talking to you ...' bug)."""
    r = (reply or "").strip()
    u = re.sub(r"\s+", " ", (user_message or "").strip().lower())
    if not r or not u:
        return r
    # strip quoted user text anywhere ("...user text...")
    if len(u) >= 8:
        r = re.sub(r'["“”]' + re.escape((user_message or "").strip()) + r'["“”]',
                   "", r, flags=re.I).strip()
    # strip leading echo: reply starts with the user's message (±punct)
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    first = norm(r.split("\n")[0][: len(u) + 20])
    if first.startswith(norm(u)[: max(8, len(norm(u)) - 2)]) and len(norm(u)) >= 4:
        cut = r[len((user_message or "").strip()):]
        r = re.sub(r"^[\s,.:;!?\-–—]+", "", cut).strip()
        r = r[:1].upper() + r[1:] if r else r
    # strip meta stage-directions some models leak
    r = re.sub(r"^\*(?:sighs?|laughs?|smiles?|smirks?|pauses?)\*\s*", "", r,
               flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", r).strip()


_FALLBACKS = {
    "devon": ["mb, zoned out staring at my code 😅 say that again?",
              "wait what? mud-season brain over here 😂 run it back",
              "glitched for a sec lol — what was that? 👀"],
    "_": ["mb, zoned out 😅 say that again?",
          "wait, what? say that again 👀",
          "brain lag lol — run it back?"],
}


def fallback_line(persona: str, message: str) -> str:
    """Last-resort reply when the LLM returns empty twice. Deterministic
    variety by message hash — NEVER a '(no reply)' placeholder."""
    bank = _FALLBACKS.get((persona or "").lower(), _FALLBACKS["_"])
    h = int(hashlib.md5((message or "").encode()).hexdigest(), 16)
    return bank[h % len(bank)]
