"""Autonomous messaging core: outbox queue + contacts + proactive engine.

This is what lets the bot TEXT FIRST — not just reply:
- Bridges (WhatsApp/Telegram) POST inbound sightings → contacts registry.
- POST /chat with channel+chat_id touches last_inbound automatically.
- Proactive ticker notices silence, generates an in-character opener
  ("you alive??", good-morning texts, jealous checks...) and queues it.
- Bridges poll GET /outbox?channel=... and deliver, then POST /ack.

"Text itself": queue with to=<own chat id> (WA: 234...@c.us, TG: me/id).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("godquant.outbox")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,      -- whatsapp|telegram
  to_addr TEXT NOT NULL,      -- chat id / number / 'me'
  message TEXT NOT NULL,
  status TEXT DEFAULT 'pending',  -- pending|sent|failed
  attempts INTEGER DEFAULT 0,
  created REAL NOT NULL,
  sent_at REAL
);
CREATE TABLE IF NOT EXISTS contacts (
  channel TEXT NOT NULL,
  chat_id TEXT NOT NULL,
  display TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,  -- proactive texting on/off
  quiet TEXT DEFAULT '',      -- quiet hours "23-7", empty = none
  max_nudges INTEGER DEFAULT 3,
  nudges_today INTEGER DEFAULT 0,
  day TEXT DEFAULT '',
  last_inbound REAL DEFAULT 0,
  last_outbound REAL DEFAULT 0,
  created REAL NOT NULL,
  PRIMARY KEY (channel, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, channel);
"""


class Outbox:
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

    # ---------- queue ----------
    def enqueue(self, channel: str, to_addr: str, message: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO outbox(channel,to_addr,message,created) VALUES(?,?,?,?)",
                (channel, to_addr, message[:4000], time.time()))
            self._conn.commit()
            return int(cur.lastrowid)

    def pending(self, channel: str, limit: int = 20) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id,to_addr,message,attempts,created FROM outbox "
                "WHERE status='pending' AND channel=? ORDER BY id LIMIT ?",
                (channel, limit))
            return [{"id": r[0], "to": r[1], "message": r[2],
                     "attempts": r[3], "created": r[4]} for r in cur.fetchall()]

    def ack(self, msg_id: int, ok: bool = True):
        with self._lock:
            if ok:
                self._conn.execute(
                    "UPDATE outbox SET status='sent', sent_at=? WHERE id=?",
                    (time.time(), msg_id))
            else:
                self._conn.execute(
                    "UPDATE outbox SET attempts=attempts+1, "
                    "status=CASE WHEN attempts+1>=5 THEN 'failed' ELSE 'pending' END "
                    "WHERE id=?", (msg_id,))
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "SELECT status, COUNT(*) FROM outbox GROUP BY status")
            q = dict(cur.fetchall())
            cur = self._conn.execute("SELECT COUNT(*) FROM contacts WHERE enabled=1")
            return {"outbox": q, "proactive_contacts": cur.fetchone()[0]}

    # ---------- contacts ----------
    def upsert_contact(self, channel: str, chat_id: str, display: str = "",
                       touch_inbound: bool = False):
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO contacts(channel,chat_id,display,last_inbound,created)"
                " VALUES(?,?,?,?,?) ON CONFLICT(channel,chat_id) DO UPDATE SET "
                "display=CASE WHEN excluded.display='' THEN contacts.display "
                "ELSE excluded.display END",
                (channel, chat_id, display, now, now))
            if touch_inbound:
                self._conn.execute(
                    "UPDATE contacts SET last_inbound=? WHERE channel=? AND chat_id=?",
                    (now, channel, chat_id))
            self._conn.commit()

    def touch_inbound(self, channel: str, chat_id: str):
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET last_inbound=? WHERE channel=? AND chat_id=?",
                (time.time(), channel, chat_id))
            self._conn.commit()

    def set_enabled(self, channel: str, chat_id: str, enabled: bool):
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET enabled=? WHERE channel=? AND chat_id=?",
                (1 if enabled else 0, channel, chat_id))
            self._conn.commit()

    def remove(self, channel: str, chat_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM contacts WHERE channel=? AND chat_id=?",
                               (channel, chat_id))
            self._conn.commit()

    def list_contacts(self) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT channel,chat_id,display,enabled,nudges_today,last_inbound,"
                "last_outbound FROM contacts ORDER BY channel,chat_id")
            return [{"channel": r[0], "chat_id": r[1], "display": r[2],
                     "enabled": bool(r[3]), "nudges_today": r[4],
                     "last_inbound_ago": int(time.time() - r[5]) if r[5] else -1,
                     "last_outbound_ago": int(time.time() - r[6]) if r[6] else -1}
                    for r in cur.fetchall()]


def _in_quiet(quiet: str, now: datetime | None = None) -> bool:
    """quiet='23-7' → True if current hour is 23,0..6."""
    if not quiet or "-" not in quiet:
        return False
    try:
        start, end = (int(x) % 24 for x in quiet.split("-", 1))
    except ValueError:
        return False
    h = (now or datetime.now()).hour
    return h >= start or h < end if start > end else start <= h < end


class ProactiveEngine:
    """Silence watcher: text contacts first, in character."""

    def __init__(self, cfg, companion, outbox: Outbox):
        self.cfg = cfg
        self.companion = companion
        self.outbox = outbox

    @staticmethod
    def _is_group(channel: str, chat_id: str) -> bool:
        if channel == "telegram":
            return chat_id.startswith("-")  # supergroups/channels
        if channel == "whatsapp":
            return chat_id.endswith("@g.us")
        return False

    def _due(self, row: tuple, now: float) -> bool:
        channel, chat_id, display, enabled, quiet, max_n, nudges, day, \
            last_in, last_out = row
        if not enabled:
            return False
        today = datetime.now().strftime("%Y%m%d")
        if day != today:  # new day → reset counters
            with self.outbox._lock:
                self.outbox._conn.execute(
                    "UPDATE contacts SET nudges_today=0, day=? "
                    "WHERE channel=? AND chat_id=?", (today, channel, chat_id))
                self.outbox._conn.commit()
            nudges = 0
        if nudges >= (max_n or self.cfg.max_nudges):
            return False
        if _in_quiet(quiet or self.cfg.quiet_hours):
            return False
        if now - (last_in or 0) < self.cfg.nudge_after:
            return False
        if last_out and now - last_out < self.cfg.nudge_gap:
            return False
        return True

    def _tick_reminders(self, now: float) -> list[dict]:
        """Fire due reminders into the outbox (alarms bypass quiet hours)."""
        try:
            due = self.companion.bonds.due_reminders(now)
        except Exception as e:
            log.warning("reminders: %s", e)
            return []
        queued = []
        for r in due:
            try:
                mid = self.outbox.enqueue(r["channel"], r["chat_id"],
                                          f"⏰ {r['text']}")
                self.companion.bonds.fire_reminder(r["id"], now)
                queued.append({"id": mid, "channel": r["channel"],
                               "to": r["chat_id"],
                               "message": f"⏰ {r['text'][:120]}"})
            except Exception as e:
                log.warning("reminder #%d failed: %s", r["id"], e)
        return queued

    def _tick_dream(self, now: float):
        from godquant.companion.dream import run_dream
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if self.companion.bonds.kv_get("last_dream_day") == today:
                return
            cmap = {f"{c['channel']}:{c['chat_id']}": c["display"]
                    for c in self.outbox.list_contacts() if c["display"]}
            rep = run_dream(self.companion.bonds, cmap, now)
            log.info("dream: %s", rep)
        except Exception as e:
            log.warning("dream failed: %s", e)

    def _tick_brief(self, now: float, queued: list):
        try:
            dest = (self.cfg.brief_to or "").strip()
            if not dest or ":" not in dest:
                return
            bh = self.cfg.brief_hour
            if datetime.now().hour < (7 if bh is None else bh):
                return
            today = datetime.now().strftime("%Y-%m-%d")
            if self.companion.bonds.kv_get("brief_day") == today:
                return
            from godquant.companion.dream import build_brief
            ch, _, cid = dest.partition(":")
            pend = self.outbox.stats()["outbox"].get("pending", 0)
            mid = self.outbox.enqueue(
                ch, cid, build_brief(self.companion.bonds, pend, now))
            self.companion.bonds.kv_set("brief_day", today)
            queued.append({"id": mid, "channel": ch, "to": cid,
                           "message": "☀️ brief"})
        except Exception as e:
            log.warning("brief failed: %s", e)

    def _tick_intentions(self, now: float) -> list[dict]:
        """Act on one targeted intention per tick (custom ones: owner pulls)."""
        if _in_quiet(self.cfg.quiet_hours):
            return []
        try:
            items = self.companion.bonds.active_intentions()
        except Exception as e:
            log.warning("intentions: %s", e)
            return []
        for it in items:
            if not it["channel"] or not it["chat_id"]:
                continue
            try:
                cid = f"{it['channel']}:{it['chat_id']}"
                if it["kind"] == "checkin":
                    text = self.companion.proactive_opener(
                        cid, self.cfg.persona, 2 * 86400,
                        display=it["display"] or None)
                else:
                    r = self.companion.chat(
                        "[EVENT: pursue this intention in character, "
                        f"one short message: {it['text']}]",
                        cid, persona=self.cfg.persona, bond_id=cid)
                    text = (r.get("response") or it["text"]).strip()
                mid = self.outbox.enqueue(it["channel"], it["chat_id"], text)
                self.companion.bonds.resolve_intention(it["id"])
                log.info("intention #%d acted → %s", it["id"], cid)
                return [{"id": mid, "channel": it["channel"], "to": it["chat_id"],
                         "message": text[:120]}]
            except Exception as e:
                log.warning("intention #%d failed: %s", it["id"], e)
        return []

    def tick(self) -> list[dict]:
        """One proactive pass. Returns queued messages. DMs only, never groups."""
        now = time.time()
        queued = self._tick_reminders(now)
        self._tick_dream(now)
        self._tick_brief(now, queued)
        queued += self._tick_intentions(now)
        with self.outbox._lock:
            rows = self.outbox._conn.execute(
                "SELECT channel,chat_id,display,enabled,quiet,max_nudges,"
                "nudges_today,day,last_inbound,last_outbound FROM contacts "
                "WHERE enabled=1").fetchall()
        for row in rows:
            channel, chat_id = row[0], row[1]
            if self._is_group(channel, chat_id):
                continue  # never slide into groups uninvited
            try:
                if not self._due(row, now):
                    continue
                silence = int(now - (row[8] or now))
                cid = f"{channel}:{chat_id}"
                opener = self.companion.proactive_opener(
                    cid, self.cfg.persona, silence, display=row[2])
                mid = self.outbox.enqueue(channel, chat_id, opener)
                with self.outbox._lock:
                    self.outbox._conn.execute(
                        "UPDATE contacts SET nudges_today=nudges_today+1, "
                        "last_outbound=? WHERE channel=? AND chat_id=?",
                        (time.time(), channel, chat_id))
                    self.outbox._conn.commit()
                queued.append({"id": mid, "channel": channel, "to": chat_id,
                               "message": opener[:120]})
                log.info("proactive → %s:%s (%ds silent)", channel, chat_id, silence)
            except Exception as e:
                log.warning("proactive failed for %s:%s: %s", channel, chat_id, e)
        return queued
