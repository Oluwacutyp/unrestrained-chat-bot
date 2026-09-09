"""Relationship engine v2 — closeness is EARNED, not counted.

Score 0-100 per sender. Gains come from SUBSTANCE × WARMTH, discounted for
rapid-fire bursts; insults and spam build FRICTION (the bot goes cold and
snappy); silence decays the score; day-streaks deepen it. Levels 0-3 derive
from score with hysteresis so bonds don't flicker. Twenty meaningless
"lol"s earn ~nothing; one vulnerable 2am paragraph earns a lot.

DB migrates itself (new columns + score backfill from legacy msg counts).
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bonds (
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  level INTEGER DEFAULT 0,
  msg_count INTEGER DEFAULT 0,
  manual INTEGER DEFAULT 0,
  updated REAL NOT NULL,
  score REAL DEFAULT 0,
  friction REAL DEFAULT 0,
  streak INTEGER DEFAULT 0,
  last_day TEXT DEFAULT '',
  last_ts REAL DEFAULT 0,
  burst INTEGER DEFAULT 0,
  deep INTEGER DEFAULT 0,
  PRIMARY KEY (channel, chat_id)
);
"""

_FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS bond_facts (
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  fkey TEXT NOT NULL,
  fvalue TEXT NOT NULL,
  updated REAL NOT NULL,
  PRIMARY KEY (channel, chat_id, fkey, fvalue)
);
"""
_NEW_COLS = {"score": "REAL DEFAULT 0", "friction": "REAL DEFAULT 0",
             "streak": "INTEGER DEFAULT 0", "last_day": "TEXT DEFAULT ''",
             "last_ts": "REAL DEFAULT 0", "burst": "INTEGER DEFAULT 0",
             "deep": "INTEGER DEFAULT 0"}

# score bands → level (up-thresholds; down-thresholds 5-8 pts lower = hysteresis)
BAND_UP = (15.0, 40.0, 70.0)
BAND_DOWN = (10.0, 32.0, 60.0)
BAND_FLOOR = (0.0, 15.0, 40.0, 70.0)

MEANINGLESS = {"lol", "lmao", "lmfao", "haha", "hahaha", "hehe", "heh",
               "k", "kk", "ok", "okay", "yea", "yeah", "yep", "yup",
               "nope", "hmm", "hm", "idk", "lolol", "loll", "xd"}
WARM_ROMANTIC = ("love you", "miss you", "need you", "want you", "my love",
                 "babe", "baby", "ily", "xoxo", "😘", "❤", "💕", "💋", "🥰")
WARM_CARE = ("how are you", "how r u", "how're you", "proud of you",
             "thank you", "thanks", "beautiful", "gorgeous", "handsome",
             "cute", "sweet", "amazing", "good morning", "good night",
             "gn ", "gm ", "take care", "feel better", "you matter")
COLD_INSULT = ("shut up", "fuck off", "fuck you", "hate you", "stupid",
               "dumb", "idiot", "ugly", "annoying", "boring", "loser",
               "pathetic", "worthless", "kill yourself", "kys")
COLD_DISMISS = ("whatever", "don't care", "dont care", "leave me",
                "not now", "busy", "go away", "stop texting", "k.")
VULNERABLE = ("scared", "afraid", "cried", "crying", "lonely", "alone",
              "depressed", "anxious", "anxiety", "miss", "love", "hate",
              "family", "mom", "dad", "mother", "father", "ex ", "my ex",
              "dream", "secret", "sorry", "forgive", "hurt", "pain",
              "died", "dead", "sick", "hospital", "fired", "failed",
              "fail", "nobody", "everybody hates", "can't sleep",
              "cant sleep", "insomnia", "pray")
EXCITED_MARK = ("!!", "?!")
UPSET_WORDS = ("sad", "upset", "crying", "cried", "depressed", "hurt",
               "heartbroken", "lonely", "miss him", "miss her", "broke up",
               "breakup", "dumped", "ghosted", "ignored")
APOLOGY = ("sorry", "my bad", "apologize", "didn't mean", "didnt mean",
           "forgive me")

BURST_WINDOW_S = 45.0     # msgs closer than this = rapid-fire (cheap)
BURST_DECAY = 0.90        # gain ×= 0.9 per consecutive burst msg, floor 0.15
FRICTION_HALFLIFE_H = 1.0  # friction halves every hour of quiet
SCORE_DECAY_DAY = 0.90    # score ×0.9 per silent day


def parse_bond_id(bond_id: str) -> tuple[str, str]:
    if ":" in bond_id:
        ch, _, rest = bond_id.partition(":")
        return ch or "telegram", rest
    return "telegram", bond_id


def level_for_score(score: float, current: int = 0) -> int:
    """Band mapping with hysteresis (harder to fall than to climb)."""
    up = sum(score >= t for t in BAND_UP)
    if up > current:
        return up
    lvl = current
    while lvl > 0 and score < BAND_DOWN[lvl - 1]:
        lvl -= 1
    return lvl


def substance(text: str) -> float:
    """0..1 — how much of THEM is in this message. 'lol' ≈ 0."""
    t = (text or "").strip()
    if not t:
        return 0.0
    low = t.lower()
    # pure emoji / reaction?
    chars = [c for c in t if not c.isspace()]
    if chars and all(ord(c) > 0x2500 or c in "!?.,<3" for c in chars):
        return 0.03
    words = re.findall(r"[a-z']+", low)
    if words and all(w in MEANINGLESS for w in words):
        return 0.03
    s = min(1.0, len(t) / 220.0)
    if "?" in t:
        s += 0.15  # they ask about you = reciprocity
    if any(v in low for v in VULNERABLE):
        s += 0.25  # vulnerability = real closeness material
    if len(words) >= 25:
        s += 0.10
    return min(1.0, round(s, 3))


def warmth(text: str) -> float:
    """-1..+1 — affection (+) vs hostility (−) toward the bot."""
    low = (text or "").lower()
    if any(w in low for w in COLD_INSULT):
        return -1.0
    if any(w in low for w in COLD_DISMISS):
        return -0.5
    if any(w in low for w in WARM_ROMANTIC):
        return 1.0
    if any(w in low for w in WARM_CARE):
        return 0.6
    return 0.0


def addressing(level: int, name: str, is_group: bool,
               bond: dict | None = None) -> str:
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
    if bond:
        fr = bond.get("friction", 0) or 0
        if fr >= 55:
            s += (f" BUT right now you're FED UP with {who} — they've been "
                  f"rude or spamming meaningless texts. Be SHORT, cold, call "
                  f"it out. No pet names, no warmth until they act right.")
        elif fr >= 30:
            s += (f" They're slightly getting on your nerves — a little "
                  f"edge and attitude is fair game.")
        streak = bond.get("streak", 0) or 0
        if streak >= 3 and eff >= 1 and fr < 30:
            s += (f" You've talked {streak} days straight — comfortable, "
                  f"familiar, inside-joke energy.")
    return "[RELATIONSHIP] " + s


def vibe_context(text: str, gap_s: float, now_ts: float | None = None) -> str:
    """[VIBE] block: their energy/emotion + time-of-day awareness."""
    import time as _t
    now_ts = _t.time() if now_ts is None else now_ts
    hour = _dt.datetime.fromtimestamp(now_ts).hour
    bits = []
    if gap_s is not None:
        if gap_s < 90:
            bits.append("rapid-fire exchange — match the energy, short texts")
        elif gap_s > 6 * 3600:
            h = gap_s / 3600
            span = f"{h:.0f} hours" if h < 24 else f"{h / 24:.0f} days"
            bits.append(f"they reappeared after {span} of silence — notice it")
    low = (text or "").lower()
    caps = sum(1 for c in (text or "") if c.isupper())
    letters = sum(1 for c in (text or "") if c.isalpha())
    if any(m in (text or "") for m in EXCITED_MARK) or (
            letters > 6 and caps / letters > 0.7):
        bits.append("THEY seem EXCITED/hyped — mirror the hype")
    elif any(w in low for w in COLD_INSULT):
        bits.append("THEY are being HOSTILE — defend yourself, real attitude")
    elif any(w in low for w in UPSET_WORDS):
        bits.append("THEY seem UPSET/sad — be soft, care first, comfort them")
    elif any(w in low for w in WARM_ROMANTIC):
        bits.append("THEY are being affectionate — reciprocate genuinely")
    if hour < 5:
        bits.append(f"it's {hour}am — late-night, intimate, unguarded hours")
    elif hour < 12:
        bits.append("morning energy — fresh, light")
    elif hour >= 23:
        bits.append("almost midnight — winding-down, soft hours")
    if not bits:
        return ""
    return "[VIBE] " + ". ".join(bits) + "."


class BondStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     timeout=30)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA + _FACTS_SCHEMA)
            have = {r[1] for r in self._conn.execute(
                "PRAGMA table_info(bonds)").fetchall()}
            for col, ddl in _NEW_COLS.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE bonds ADD COLUMN {col} {ddl}")
            # one-time backfill: veterans keep earned closeness
            self._conn.execute(
                "UPDATE bonds SET score=CASE WHEN score IS NULL OR score=0 "
                "THEN MIN(60.0, msg_count*1.5) ELSE score END")
            self._conn.commit()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def _row(self, channel: str, chat_id: str) -> dict:
        cur = self._conn.execute(
            "SELECT level,msg_count,manual,score,friction,streak,last_day,"
            "last_ts,burst,deep FROM bonds WHERE channel=? AND chat_id=?",
            (channel, chat_id))
        r = cur.fetchone()
        if r:
            return {"channel": channel, "chat_id": chat_id, "level": r[0],
                    "count": r[1], "manual": bool(r[2]), "score": r[3] or 0.0,
                    "friction": r[4] or 0.0, "streak": r[5] or 0,
                    "last_day": r[6] or "", "last_ts": r[7] or 0.0,
                    "burst": r[8] or 0, "deep": r[9] or 0}
        return {"channel": channel, "chat_id": chat_id, "level": 0,
                "count": 0, "manual": False, "score": 0.0, "friction": 0.0,
                "streak": 0, "last_day": "", "last_ts": 0.0, "burst": 0,
                "deep": 0}

    @staticmethod
    def _decayed(bond: dict, now: float) -> dict:
        """Apply silence decay (score) + quiet cooling (friction), no write."""
        last = bond["last_ts"] or now
        gap_days = max(0.0, (now - last) / 86400.0)
        gap_h = gap_days * 24.0
        bond = dict(bond)
        bond["score"] = round(bond["score"] * (SCORE_DECAY_DAY ** gap_days), 2)
        bond["friction"] = round(
            bond["friction"] * (0.5 ** (gap_h / FRICTION_HALFLIFE_H)), 2)
        if not bond["manual"]:
            bond["level"] = level_for_score(bond["score"], bond["level"])
        return bond

    def get(self, channel: str, chat_id: str,
            now: float | None = None) -> dict:
        import time as _t
        now = _t.time() if now is None else now
        with self._lock:
            bond = self._row(channel, chat_id)
        bond = self._decayed(bond, now)
        bond.update({"substance": 0.0, "warmth": 0.0, "spam": False})
        return bond

    def note_message(self, channel: str, chat_id: str, user_text: str,
                     now: float | None = None) -> dict:
        """Score one inbound message. Returns the updated bond."""
        import time as _t
        now = _t.time() if now is None else now
        with self._lock:
            bond = self._decayed(self._row(channel, chat_id), now)

        sub, warm = substance(user_text), warmth(user_text)
        low = (user_text or "").lower()
        gap = now - bond["last_ts"] if bond["last_ts"] else 1e9
        bursting = gap < BURST_WINDOW_S
        burst = bond["burst"] + 1 if bursting else 0
        mult = max(0.15, BURST_DECAY ** burst)
        spam = sub < 0.08 and (bursting or bond["friction"] >= 30)

        gain = sub * (6.0 + 12.0 * max(0.0, warm)) * mult
        score = bond["score"] + gain
        friction = bond["friction"]
        if warm <= -1.0:
            friction += 25
            score = max(0.0, score - 6)  # insults cost closeness
        elif warm <= -0.5:
            friction += 10
            score = max(0.0, score - 2)
        elif spam:
            friction += 6
        if any(w in low for w in APOLOGY):
            friction = max(0.0, friction - 30)
        if warm >= 0.9:
            score = max(score, BAND_UP[0])  # romance fast-tracks L1
        friction = min(100.0, friction)
        score = round(min(100.0, score), 2)

        # streaks: consecutive-day talking
        today = _dt.datetime.fromtimestamp(now).date().isoformat()
        streak = bond["streak"]
        if today != bond["last_day"]:
            try:
                delta = (_dt.date.fromisoformat(today) -
                         _dt.date.fromisoformat(bond["last_day"])).days \
                    if bond["last_day"] else 99
            except ValueError:
                delta = 99
            streak = streak + 1 if delta == 1 else 1
            if sub > 0.3:  # showing up with substance = bonus depth
                score = round(min(100.0, score + 1.5), 2)

        count = bond["count"] + 1
        deep = bond["deep"] + (1 if sub > 0.4 else 0)
        manual = bond["manual"]
        level = bond["level"] if manual else level_for_score(score, bond["level"])

        with self._lock:
            self._conn.execute(
                "INSERT INTO bonds(channel,chat_id,level,msg_count,manual,"
                "updated,score,friction,streak,last_day,last_ts,burst,deep)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(channel,chat_id) DO UPDATE SET level=excluded.level,"
                " msg_count=excluded.msg_count, manual=excluded.manual,"
                " updated=excluded.updated, score=excluded.score,"
                " friction=excluded.friction, streak=excluded.streak,"
                " last_day=excluded.last_day, last_ts=excluded.last_ts,"
                " burst=excluded.burst, deep=excluded.deep",
                (channel, chat_id, level, count, 1 if manual else 0, now,
                 score, friction, streak, today, now, burst, deep))
            self._conn.commit()
        return {"channel": channel, "chat_id": chat_id, "level": level,
                "count": count, "manual": manual, "score": score,
                "friction": round(friction, 2), "streak": streak,
                "substance": sub, "warmth": warm, "spam": spam, "deep": deep}

    def set_level(self, channel: str, chat_id: str, level: int | None) -> dict:
        """Owner override: pin 0-3 (score snaps to band floor), None = auto."""
        import time as _t
        with self._lock:
            bond = self._row(channel, chat_id)
        if level is None:
            manual = False
            level = level_for_score(bond["score"], bond["level"])
            score, friction = bond["score"], bond["friction"]
        else:
            manual, level = True, max(0, min(3, int(level)))
            score, friction = BAND_FLOOR[level], 0.0
        with self._lock:
            self._conn.execute(
                "INSERT INTO bonds(channel,chat_id,level,msg_count,manual,"
                "updated,score,friction,streak,last_day,last_ts,burst,deep)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(channel,chat_id) DO UPDATE SET level=excluded.level,"
                " manual=excluded.manual, updated=excluded.updated,"
                " score=excluded.score, friction=excluded.friction",
                (channel, chat_id, level, bond["count"], 1 if manual else 0,
                 _t.time(), score, friction, bond["streak"], bond["last_day"],
                 bond["last_ts"], bond["burst"], bond["deep"]))
            self._conn.commit()
        bond.update({"level": level, "manual": manual, "score": score,
                     "friction": friction, "substance": 0.0, "warmth": 0.0,
                     "spam": False})
        return bond

    # ---- per-sender facts / dossier ----
    def add_fact(self, channel: str, chat_id: str, key: str, value: str,
                 now: float | None = None) -> list[tuple[str, str]]:
        """Store one durable fact. Single-value keys replace; rest accumulate."""
        import time as _t
        from godquant.companion.memory_engine import SINGLE_KEYS
        now = _t.time() if now is None else now
        value = (value or "").strip()
        if not key or not value:
            return self.get_facts(channel, chat_id)
        with self._lock:
            if key in SINGLE_KEYS:
                self._conn.execute(
                    "DELETE FROM bond_facts WHERE channel=? AND chat_id=? AND fkey=?",
                    (channel, chat_id, key))
            self._conn.execute(
                "INSERT OR IGNORE INTO bond_facts(channel,chat_id,fkey,fvalue,"
                "updated) VALUES(?,?,?,?,?)",
                (channel, chat_id, key, value, now))
            self._conn.commit()
        return self.get_facts(channel, chat_id)

    def get_facts(self, channel: str, chat_id: str) -> list[tuple[str, str]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT fkey,fvalue FROM bond_facts WHERE channel=? AND chat_id=? "
                "ORDER BY updated", (channel, chat_id))
            return [(r[0], r[1]) for r in cur.fetchall()]

    def clear_facts(self, channel: str, chat_id: str):
        with self._lock:
            self._conn.execute(
                "DELETE FROM bond_facts WHERE channel=? AND chat_id=?",
                (channel, chat_id))
            self._conn.commit()
