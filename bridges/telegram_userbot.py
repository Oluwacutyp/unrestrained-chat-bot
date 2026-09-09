#!/usr/bin/env python3
"""Telegram USERBOT — your own account, powered by the bot brain (MTProto).

NOT a Bot-API bot: this logs in as YOU via Telethon. It replies to your DMs,
lets you run owner commands by messaging yourself, and polls the outbox so the
bot can text first — including texting itself (Saved Messages).

Setup (one time):
  pip install telethon
  # get api id/hash at https://my.telegram.org/apps (free, 2 min)
  export TG_API_ID=123456 TG_API_HASH=abcdef...
  python bridges/telegram_userbot.py     # first run asks phone + login code
  # (session persists at ~/.godquant/tg_userbot.session — back it up, never share)

Env knobs:
  GQ_SERVER=http://127.0.0.1:5000  TG_ALLOW=12345,@friend (empty = all DMs)
  TG_GROUPS=0  TG_POLL=15  TG_PERSONA=alex  TG_PREFIX=.  TG_SEARCH=0
  TG_CATCHUP=0 (1 = reply to unread DMs missed while offline, see below)
  TG_CATCHUP_MINS=60  TG_CATCHUP_MAX=5
  TG_HUMANIZE=1 (0 = instant sends, no typing performance)
  HUMAN_WPM=45 HUMAN_MAXPM=20 HUMAN_GAP=4 (humanizer tuning)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-userbot")

try:
    from telethon import TelegramClient, events  # type: ignore
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False

from godquant.companion.humanize import (ExchangeTracker, RateLimiter,
                                        asleep, bubble_gap, plan_typos,
                                        read_delay, split_bubbles,
                                        typing_delay)

API_ID = int(os.environ.get("TG_API_ID", "0") or 0)
API_HASH = os.environ.get("TG_API_HASH", "")
SERVER = os.environ.get("GQ_SERVER", "http://127.0.0.1:5000").rstrip("/")
SESSION = os.environ.get("TG_SESSION",
                         str(Path.home() / ".godquant" / "tg_userbot"))
ALLOW = {a.strip().lower().lstrip("@") for a in
         os.environ.get("TG_ALLOW", "").split(",") if a.strip()}
GROUPS = os.environ.get("TG_GROUPS", "0") == "1"
POLL = int(os.environ.get("TG_POLL", "15"))
PERSONA = os.environ.get("TG_PERSONA", "")
PREFIX = os.environ.get("TG_PREFIX", ".")
SEARCH = os.environ.get("TG_SEARCH", "0") == "1"
HUMANIZE = os.environ.get("TG_HUMANIZE", "1") == "1"
HUMAN_WPM = int(os.environ.get("HUMAN_WPM", "45"))

limiter = RateLimiter(max_per_min=int(os.environ.get("HUMAN_MAXPM", "20")),
                      min_chat_gap=float(os.environ.get("HUMAN_GAP", "4")))
exchanges = ExchangeTracker()
_warn_last: dict = {}   # kind -> last ts (self-alert throttle)
BRIDGE_CLIENT = None     # live telethon client (for .import)


def _require_creds():
    if not API_ID or not API_HASH:
        print("Set TG_API_ID and TG_API_HASH from https://my.telegram.org/apps")
        sys.exit(1)


# ---------- server API (sync urllib, called via to_thread) ----------
def _api(path: str, payload: dict | None = None, timeout: int = 120):
    url = SERVER + path
    if payload is None:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def api(path, payload=None, timeout=120):
    try:
        return await asyncio.to_thread(_api, path, payload, timeout)
    except Exception as e:
        if path != "/warn":
            await warn("brain_offline", f"brain unreachable ({SERVER}): {e}")
        raise


async def warn(kind: str, message: str):
    """Self-alert to Saved Messages (throttled 5 min per kind). Never raises."""
    try:
        now = time.time()
        if now - _warn_last.get(kind, 0) < 300:
            return
        _warn_last[kind] = now
        await asyncio.to_thread(_api, "/warn", {"channel": "telegram",
                                                 "kind": kind,
                                                 "message": message[:400]}, 15)
    except Exception:
        pass


# ---------- helpers ----------
def allowed(sender_id: int, username: str | None) -> bool:
    if not ALLOW:
        return True
    return str(sender_id) in ALLOW or (username or "").lower() in ALLOW


async def chat_full(text: str, sender_id: int, display: str,
                   use_search: bool = False, chat_id=None,
                   is_group: bool = False) -> dict:
    """Ask the brain. Group chats share one context; bonds stay per-sender.

    Returns the full /chat payload (response + mood + bond intelligence)
    so the humanizer can perform it properly.
    """
    chat = str(chat_id) if chat_id is not None else str(sender_id)
    return await api("/chat", {
        "message": text, "conversation_id": f"tg:{chat}",
        "channel": "telegram", "chat_id": chat, "display": display,
        "sender_name": display, "is_group": is_group,
        "bond_id": f"telegram:{sender_id}",
        "persona": PERSONA or None, "use_search": use_search}, timeout=180)


async def chat_reply(text: str, sender_id: int, display: str,
                     use_search: bool = False, chat_id=None,
                     is_group: bool = False) -> str:
    """Ask the brain, text only (compat wrapper around chat_full)."""
    data = await chat_full(text, sender_id, display, use_search, chat_id,
                           is_group)
    # NOTE: mood footer deliberately NOT appended — replies stay clean.
    return data.get("response") or "(no reply 😅)"


async def human_send_reply(event, client, text: str, mood: str = "neutral",
                           depth: float = 0.0, energy: str = "calm"):
    """Reply like a human: read pause → mood-paced typing → bubbles.

    mood/depth/energy come from the brain's reply payload: heavy emotional
    texts get longer absorbsion, angry types fast, sad types slow, and long
    bubbles occasionally ship a human typo + *correction.
    """
    bubbles = plan_typos(split_bubbles(text))
    chat = "tg:%s" % getattr(event, "chat_id", "?")
    if not HUMANIZE:
        for b in bubbles:
            await limiter.wait(chat)
            await event.reply(b[:4000])
        return
    await asleep(read_delay(len(text), depth))
    for i, b in enumerate(bubbles):
        if i:
            await asleep(bubble_gap())
        await limiter.wait(chat)
        try:
            async with client.action(event.chat_id, "typing"):
                await asleep(typing_delay(len(b), wpm=HUMAN_WPM, mood=mood,
                                          energy=energy))
        except Exception:
            await asleep(typing_delay(b))
        await event.reply(b[:4000])


async def human_send_message(client, entity, text: str, mood: str = "neutral"):
    """Outbound variant of human_send_reply (no event to reply to)."""
    bubbles = plan_typos(split_bubbles(text))
    key = "tg:%s" % entity
    if not HUMANIZE:
        for b in bubbles:
            await limiter.wait(key)
            await client.send_message(entity, b[:4000])
        return
    await asleep(read_delay(len(text)))
    for i, b in enumerate(bubbles):
        if i:
            await asleep(bubble_gap())
        await limiter.wait(key)
        try:
            async with client.action(entity, "typing"):
                await asleep(typing_delay(len(b), wpm=HUMAN_WPM, mood=mood))
        except Exception:
            await asleep(typing_delay(b))
        await client.send_message(entity, b[:4000])


# user-facing DM commands (anyone allowed)
async def run_user_cmd(cmd: str, arg: str, sender_id: int) -> str | None:
    if cmd in ("reset",):
        await api("/reset", {"conversation_id": f"tg:{sender_id}"})
        return "fresh start ✨"
    if cmd in ("mood",):
        m = await api(f"/mood?conversation_id=tg:{sender_id}")
        return f"mood: {m.get('current')} ({m.get('level')}/10)"
    if cmd in ("persona",):
        return "personas live server-side — ask your owner to set TG_PERSONA 💕"
    if cmd in ("search", "news", "wiki", "fact", "fact_check"):
        kind = {"search": "text", "fact": "fact_check"}.get(cmd, cmd)
        r = await api("/research", {"query": arg, "type": kind}, timeout=60)
        return r.get("results", "no results")
    if cmd in ("translate", "tr", "pidgin"):
        if not arg:
            return "usage: !translate <text> (English ↔ Naija pidgin)"
        r = await api("/translate", {"text": arg}, timeout=60)
        return r.get("translation") or "couldn't translate that rn 😅"
    return None


# owner commands (YOU, via outgoing messages starting with PREFIX, anywhere)
HELP = ("userbot cmds: `.mission <goal>` `.tick` `.send <chat> <msg>` "
        "`.contacts` `.mood [chat]` `.reset [chat]` `.persona <n>` "
        "`.bond [chat] [0-3|auto]` `.memory [chat]` `.import <chat>` `.help`")


async def run_owner_cmd(cmd: str, arg: str) -> str:
    if cmd == "help":
        return HELP
    if cmd == "mission":
        r = await api("/mission", {"goal": arg}, timeout=300)
        parts = [f"[{x['agent']}] {x['output']}" for x in r.get("results", [])]
        return "\n\n".join(parts)[:4000] or "done, no output"
    if cmd == "tick":
        r = await api("/tick", {}, timeout=180)
        q = r.get("queued", [])
        return f"queued {len(q)}: " + "; ".join(
            f"{m['channel']}:{m['to']}" for m in q) if q else "nothing due 😴"
    if cmd == "send":
        to, _, msg = arg.partition(" ")
        if not to or not msg:
            return "usage: .send <chat_id|me|username> <message>"
        r = await api("/send", {"channel": "telegram", "to": to, "message": msg})
        return f"queued #{r.get('queued')} → {to} ✉"
    if cmd == "contacts":
        r = await api("/contacts")
        lines = [f"{c['channel']}:{c['chat_id']} ({c['display']}) "
                 f"{'🔔' if c['enabled'] else '🔕'}" for c in r.get("contacts", [])]
        return "\n".join(lines) or "no contacts yet" + f"\n{r.get('outbox')}"
    if cmd == "mood":
        m = await api(f"/mood?conversation_id={arg or 'tg:me'}")
        return f"{m.get('current')} ({m.get('level')}/10)"
    if cmd == "reset":
        await api("/reset", {"conversation_id": arg or "tg:me"})
        return "reset ✨"
    if cmd == "persona":
        global PERSONA
        PERSONA = arg.strip()
        return f"persona → {PERSONA}"
    if cmd == "import":
        if BRIDGE_CLIENT is None:
            return "client not ready — try again in a few seconds"
        parts = arg.split()
        if not parts:
            return "usage: .import <chat_id|username|me> [limit≤300]"
        target, limit = parts[0], 200
        for p in parts[1:]:
            if p.isdigit():
                limit = min(300, int(p))
        try:
            entity = await BRIDGE_CLIENT.get_entity(target)
            cid = str(getattr(entity, "id", target))
            recs = []
            async for m in BRIDGE_CLIENT.iter_messages(entity, limit=limit):
                t = getattr(m, "text", None) or ""
                if not t.strip():
                    continue
                d = getattr(m, "date", None)
                recs.append({"role": "assistant" if getattr(m, "out", False)
                             else "user", "text": t[:1000],
                             "ts": d.timestamp() if d else 0,
                             "sender": str(getattr(m, "sender_id", None) or cid)})
            recs.reverse()
            r = await api("/import", {"channel": "telegram", "chat_id": cid,
                                      "messages": recs}, timeout=120)
        except Exception as e:
            return f"import failed: {e}"
        facts = r.get("facts", [])
        head = f"imported {r.get('imported', 0)} msgs, learned {len(facts)} facts"
        return head if not facts else head + ": " + ", ".join(facts[:12])
    if cmd == "memory":
        target = arg.strip() or "me"
        r = await api(f"/memory?channel=telegram&chat_id={target}")
        facts = r.get("facts", [])
        b = r.get("bond", {})
        head = (f"memory[{target}] L{b.get('level')} score {b.get('score')} "
                f"fric {b.get('friction')} streak {b.get('streak')}d")
        if not facts:
            return head + " — no facts yet 🧠"
        rest = " | ".join(f"{f['key']}={f['value']}" for f in facts)
        return (head + " | " + rest)[:3500]
    if cmd == "bond":
        parts = arg.split()
        target = parts[0] if parts else "me"
        if len(parts) > 1:
            if parts[1].lower() == "auto":
                level = "auto"
            else:
                try:
                    level = int(parts[1])
                except ValueError:
                    return "usage: .bond [chat] [0-3|auto]"
            r = await api("/bond", {"channel": "telegram",
                                    "chat_id": target, "level": level})
            b = r.get("bond", {})
            return (f"bond[{target}] pinned → L{b.get('level')} "
                    f"({b.get('count', 0)} msgs) 💾")
        r = await api(f"/bond?channel=telegram&chat_id={target}")
        b = r.get("bond", {})
        pin = " 📌" if b.get("manual") else ""
        return f"bond[{target}]: L{b.get('level')} · {b.get('count', 0)} msgs{pin}"
    return HELP


# ---------- message handling (pure logic — unit-tested with fake events) ----------
async def handle_owner_message(event) -> str | None:
    """Your outgoing `PREFIXcmd` messages → owner command result (also replied)."""
    text = (event.raw_text or "").strip()
    if not text.startswith(PREFIX) or len(text) <= len(PREFIX):
        return None
    cmd, _, arg = text[len(PREFIX):].partition(" ")
    try:
        out = await run_owner_cmd(cmd.lower(), arg.strip())
    except Exception as e:
        out = f"owner cmd failed: {e}"
    await event.reply(out[:4000])
    return out


async def handle_incoming(event, client, me) -> str | None:
    """Someone texted your account → reply via the brain. Returns reply/None."""
    is_group = bool(getattr(event, "is_group", False) or
                    getattr(event, "is_channel", False))
    if is_group:
        if not GROUPS:
            return None
        text0 = event.raw_text or ""
        mentioned = bool(me.username) and f"@{me.username}".lower() in text0.lower()
        replied = False
        if getattr(event, "is_reply", False):
            try:
                replied = (await event.get_reply_message()).sender_id == me.id
            except Exception:
                replied = False
        if not (mentioned or replied):
            return None
    sender = await event.get_sender()
    if getattr(sender, "bot", False):
        return None
    sid = event.sender_id
    if not allowed(sid, getattr(sender, "username", None)):
        print(f"⏭ ignored {sid}")
        return None
    text = (event.raw_text or "").strip()
    if not text:
        return None
    name = getattr(sender, "first_name", None) or getattr(sender, "username", str(sid))
    print(f"📨 {name} ({sid}): {text[:80]}")
    try:
        await api("/contacts", {"channel": "telegram", "chat_id": str(sid),
                                "display": name}, timeout=15)
        if text[:1] in ("!", "/") and len(text) > 1:  # contact commands
            parts = text[1:].split(None, 1)
            out = await run_user_cmd(parts[0].lower(),
                                     parts[1] if len(parts) > 1 else "", sid)
            if out:
                await event.reply(out[:4000])
                return out
        full = await chat_full(text, sid, name, use_search=SEARCH,
                               chat_id=event.chat_id, is_group=is_group)
        reply = full.get("response") or "(no reply 😅)"
        chat_key = "tg:%s" % event.chat_id
        energy = "rapid" if (exchanges.gap(chat_key) or 1e9) < 90 else "calm"
        spaced = exchanges.note(chat_key)
        if spaced:
            print(f"💤 distracted {spaced:.0f}s ({chat_key})")
            await asleep(spaced)
        await human_send_reply(event, client, reply,
                               mood=full.get("mood", "neutral"),
                               depth=float(full.get("substance", 0) or 0),
                               energy=energy)
        print("✓ replied")
        return reply
    except Exception as e:
        log.warning("reply failed: %s", e)
        await warn("reply_failed", f"reply to {sid} failed: {e}")
        try:
            await event.reply("hey, something went wrong on my end 😔 try again?")
        except Exception:
            pass
        return None


async def deliver_outbox(client) -> list:
    """One outbox pass: send everything pending for telegram. Returns sent-to list."""
    sent = []
    items = (await api("/outbox?channel=telegram", timeout=20)).get("pending", [])
    for it in items:
        try:
            entity = int(it["to"]) if it["to"].lstrip("-").isdigit() else it["to"]
            await human_send_message(client, entity, it["message"])
            await api("/ack", {"id": it["id"], "ok": True}, timeout=15)
            print(f"✉ sent → {it['to']}")
            sent.append(it["to"])
        except Exception as e:
            log.warning("send → %s failed: %s", it["to"], e)
            await api("/ack", {"id": it["id"], "ok": False}, timeout=15)
    return sent


async def catchup_unread(client, me) -> list:
    """Opt-in (TG_CATCHUP=1): reply to unread DMs missed while offline.

    Guards: DMs only, allowlist applies, bots skipped, only messages newer
    than TG_CATCHUP_MINS, max TG_CATCHUP_MAX dialogs, replied ones marked read.
    """
    if os.environ.get("TG_CATCHUP", "0") != "1":
        return []
    from datetime import datetime, timedelta, timezone
    max_age = timedelta(minutes=int(os.environ.get("TG_CATCHUP_MINS", "60")))
    max_n = int(os.environ.get("TG_CATCHUP_MAX", "5"))
    done, now = [], datetime.now(timezone.utc)
    async for dialog in client.iter_dialogs(limit=50):
        if len(done) >= max_n:
            break
        if getattr(dialog, "is_group", False) or getattr(dialog, "is_channel", False):
            continue
        if not (getattr(dialog, "unread_count", 0) or 0):
            continue
        try:
            entity = await client.get_entity(dialog.id)
        except Exception:
            continue
        if getattr(entity, "bot", False):
            continue
        if not allowed(entity.id, getattr(entity, "username", None)):
            continue
        try:
            msgs = await client.get_messages(entity, limit=1)
        except Exception:
            continue
        msg = msgs[0] if msgs else None
        if msg is None or getattr(msg, "out", False):
            continue
        date = getattr(msg, "date", None)
        if date is not None:
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            if now - date > max_age:
                continue
        try:
            out = await handle_incoming(msg, client, me)
            await client.send_read_acknowledge(entity)
            if out:
                done.append(dialog.id)
        except Exception as e:
            log.warning("catchup %s: %s", dialog.id, e)
    return done


async def main():
    _require_creds()
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()  # interactive phone+code login on first run
    global BRIDGE_CLIENT
    BRIDGE_CLIENT = client
    me = await client.get_me()
    print(f"✓ logged in as @{getattr(me, 'username', me.id)} ({me.id}) → {SERVER}")

    @client.on(events.NewMessage(outgoing=True))
    async def _(event):
        await handle_owner_message(event)

    @client.on(events.NewMessage(incoming=True))
    async def _(event):
        await handle_incoming(event, client, me)

    caught = await catchup_unread(client, me)
    if caught:
        print(f"✓ catchup replied in {len(caught)} dialog(s)")

    async def outbox_loop():
        while True:
            try:
                await deliver_outbox(client)
            except Exception as e:
                log.debug("outbox poll: %s", e)
                await warn("outbox", f"outbox poll failed: {e}")
            await asyncio.sleep(POLL)

    asyncio.create_task(outbox_loop())
    print(f"✓ userbot live (dm allowlist: {sorted(ALLOW) or 'everyone'}, "
          f"groups: {GROUPS}, poll: {POL}s, humanize: {HUMANIZE})")
    await client.run_until_disconnected()


if __name__ == "__main__":
    if not TELETHON_OK:
        print("telethon not installed.\n  pip install telethon\nthen re-run.")
        sys.exit(1)
    asyncio.run(main())
