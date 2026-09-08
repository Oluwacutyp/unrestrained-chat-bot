"""Relationship engine: closeness is EARNED, pet names are gated.

Levels: 0 stranger → 1 friend → 2 close → 3 intimate. Auto-advance by
message count + romantic signals from THEM; owner can pin any level.
"No babe for strangers" is enforced by injecting addressing rules into the
persona prompt every message. Group chats cap effective level at 1.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bonds (
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  level INTEGER DEFAULT 0,
  msg_count INTEGER DEFAULT 0,
  manual INTEGER DEFAULT 0,   -- 1 = owner pinned, auto-advance off
  updated REAL NOT NULL,
  PRIMARY KEY (channel, chat_id)
);
"""

# user said something romantic → closeness can jump-start
ROMANTIC_SIGNALS = ("love you", "miss you", "babe", "baby", "my love",
                    "ily ", "ily", "xoxo", "😘", "❤", "💕")

# count thresholds → auto level (when not manual)
THRESHOLDS = (5, 20, 60)  # L1 at 5 msgs, L2 at 20, L3 at 60


def parse_bond_id(bond_id: str) -> tuple[str, str]:
    if ":" in bond_id:
        ch, _, rest = bond_id.partition(":")
        return ch or "telegram", rest
    return "telegram", bond_id


def auto_level(count: int) -> int:
    if count >= THRESHOLDS[2]:
        return 3
    if count >= THRESHOLDS[1]:
        return 2
    if count >= THRESHOLDS[0]:
        return 1
    return 0


def addressing(level: int, name: str, is_group: bool) -> str:
    """Prompt block telling the persona HOW to address this person."""
    who = (name or "them").strip() or "them"
    eff = min(level, 1) if is_group else level
    if eff <= 0:
        s = (f"You barely know {who} — a stranger. Be friendly, casual, a "
             f"little reserved. NEVER use pet names (no babe/baby/love/honey) "
             f"and do NOT flirt. Use their name ({who}) if you use one at all.")
    elif eff == 1:
        s = (f"{who} is a new friend. Warm and playful, use their name "
             f"({who}) naturally. NO pet names yet — no babe/baby until "
             f"you're genuinely closer.")
    elif eff == 2:
        s = (f"You and {who} are close. Pet names like babe are ok SPARINGLY, "
             f"light flirting is fine in private DMs.")
    else:
        s = (f"{who} is your intimate partner. Full romance mode per your "
             f"persona — pet names, affection, everything.")
    if is_group:
        s += (f" THIS IS A GROUP CHAT with an audience: stay appropriate — "
              f"no flirting, no pet names, address {who} by name.")
    return "[RELATIONSHIP] " + s


class BondStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     timeout=30)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def get(self, channel: str, chat_id: str) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "SELECT level,msg_count,manual FROM bonds WHERE channel=? AND chat_id=?",
                (channel, chat_id))
            row = cur.fetchone()
        if row:
            return {"channel": channel, "chat_id": chat_id, "level": row[0],
                    "count": row[1], "manual": bool(row[2])}
        return {"channel": channel, "chat_id": chat_id, "level": 0,
                "count": 0, "manual": False}

    def note_message(self, channel: str, chat_id: str, user_text: str) -> dict:
        """Record one inbound user message; advance closeness if earned."""
        bond = self.get(channel, chat_id)
        count = bond["count"] + 1
        level, manual = bond["level"], bond["manual"]
        if not manual:
            level = auto_level(count)
            low = (user_text or "").lower()
            if any(sig in low for sig in ROMANTIC_SIGNALS):
                level = max(level, 1)  # reciprocate warmth, don't leap to babe
        with self._lock:
            self._conn.execute(
                "INSERT INTO bonds(channel,chat_id,level,msg_count,manual,updated)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(channel,chat_id) DO UPDATE SET "
                "level=excluded.level, msg_count=excluded.msg_count, "
                "manual=excluded.manual, updated=excluded.updated",
                (channel, chat_id, level, count, 1 if manual else 0, time.time()))
            self._conn.commit()
        return {"channel": channel, "chat_id": chat_id, "level": level,
                "count": count, "manual": manual}

    def set_level(self, channel: str, chat_id: str, level: int | None) -> dict:
        """Owner override: pin level 0-3, or None to resume auto."""
        bond = self.get(channel, chat_id)
        if level is None:
            manual, level = False, auto_level(bond["count"])
        else:
            manual, level = True, max(0, min(3, int(level)))
        with self._lock:
            self._conn.execute(
                "INSERT INTO bonds(channel,chat_id,level,msg_count,manual,updated)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(channel,chat_id) DO UPDATE SET "
                "level=excluded.level, manual=excluded.manual, updated=excluded.updated",
                (channel, chat_id, level, bond["count"],
                 1 if manual else 0, time.time()))
            self._conn.commit()
        return {"channel": channel, "chat_id": chat_id, "level": level,
                "count": bond["count"], "manual": manual}
