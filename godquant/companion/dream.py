"""Dream cycle: nightly consolidation for the autonomous mind.

While nobody is talking, the bot:
1. Dedupes + conflict-resolves dossier facts (newest truth wins — explicit
   corrections like "no, I live in Abuja now" overwrite stale ones).
2. Journals every relationship (bond level, what changed, friction flags).
3. Sets its own intentions — e.g. notices a close bond gone quiet > 48h and
   plans a check-in, which the tick loop then acts on.

Pure logic over the Bonds store: no network, deterministic, testable.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

QUIET_AFTER = 48 * 3600   # silence before a close bond earns a check-in
CHECKIN_CAP = 3            # max new check-in intentions per dream


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _name(facts: list, chat_id: str) -> str:
    for k, v in facts:
        if k.strip().lower() in ("name", "callsign", "called"):
            return v
    return chat_id


def run_dream(bonds, display_map: dict | None = None,
              now: float | None = None) -> dict:
    """Consolidate one full pass. Returns a report dict."""
    now = now or time.time()
    display_map = display_map or {}
    report: dict = {"chats": 0, "merged": 0, "conflicts": [],
                    "intentions": [], "journal": 0}
    for b in bonds.all_bonds():
        ch, cid = b["channel"], b["chat_id"]
        full = bonds.facts_full(ch, cid)
        if full:
            # only SINGLE_KEYS collapse (newest truth wins); multi-value keys
            # like=... are legitimately plural — keep them all (cap 30).
            from godquant.companion.memory_engine import SINGLE_KEYS
            by_key: dict = {}
            for k, v, _u in full:
                by_key.setdefault(k, []).append(v)
            for k, vals in by_key.items():
                uniq = list(dict.fromkeys(vals))
                if k in SINGLE_KEYS and len(uniq) > 1:
                    report["merged"] += len(vals) - 1
                    report["conflicts"].append(f"{ch}:{cid} {k} → {vals[-1]}")
                    bonds.clear_key(ch, cid, k)
                    bonds.add_fact(ch, cid, k, vals[-1])
                elif len(vals) > 30:
                    report["merged"] += len(vals) - 30
                    bonds.clear_key(ch, cid, k)
                    for v in vals[-30:]:
                        bonds.add_fact(ch, cid, k, v)
        facts = bonds.get_facts(ch, cid)
        nm = display_map.get(f"{ch}:{cid}") or _name(facts, cid)
        bits = [f"{nm} — bond L{b['level']} ({b['score']:.0f}), "
                f"{b['msg_count']} msgs, {len(facts)} known"]
        if facts:
            bits.append("latest: " + "; ".join(f"{k}={v}"
                                               for k, v in facts[-3:]))
        if b.get("friction", 0) >= 2:
            bits.append("⚠️ friction high — tread gently")
        if b.get("streak", 0) >= 5:
            bits.append(f"🔥 {b['streak']}-day streak")
        bonds.save_journal(ch, cid, _day(now), ". ".join(bits))
        report["journal"] += 1
        report["chats"] += 1
        last = b.get("updated") or now
        if (b["level"] >= 2 and now - last > QUIET_AFTER
                and len(report["intentions"]) < CHECKIN_CAP):
            days = int((now - last) / 86400)
            iid = bonds.add_intention(
                f"check on {nm} — quiet {days}d", kind="checkin",
                channel=ch, chat_id=cid, display=nm)
            report["intentions"].append(iid)
    bonds.kv_set("last_dream_day", _day(now))
    bonds.kv_set("dream_report", json.dumps(report))
    return report


def build_brief(bonds, pending_outbox: int = 0,
                now: float | None = None) -> str:
    """Morning briefing text: reminders, intentions, bonds, last dream."""
    now = now or time.time()
    lines = [f"☀️ brief — {_day(now)}"]
    soon = [r for r in bonds.list_reminders()
            if r["due_ts"] - now < 86400]
    if soon:
        lines.append("⏰ due in 24h: " + "; ".join(
            f"#{r['id']} {r['text'][:60]}" for r in soon[:5]))
    else:
        lines.append("⏰ nothing due in 24h")
    ints = bonds.active_intentions()
    if ints:
        lines.append("🎯 intentions: " + "; ".join(
            f"#{i['id']} {i['text'][:60]}" for i in ints[:5]))
    else:
        lines.append("🎯 no open intentions")
    close = [b for b in bonds.all_bonds() if b["level"] >= 2]
    lines.append(f"❤️ {len(close)} close bond(s)")
    try:
        rep = json.loads(bonds.kv_get("dream_report", "") or "{}")
    except ValueError:
        rep = {}
    if rep:
        lines.append(f"🌙 dream: {rep.get('chats', 0)} chats, "
                     f"{rep.get('merged', 0)} facts merged, "
                     f"{len(rep.get('intentions', []))} check-ins")
    if pending_outbox:
        lines.append(f"📤 {pending_outbox} queued outbound")
    return "\n".join(lines)
