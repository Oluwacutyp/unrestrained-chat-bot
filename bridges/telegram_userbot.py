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
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-userbot")

try:
    from telethon import TelegramClient, events  # type: ignore
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False

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
    return await asyncio.to_thread(_api, path, payload, timeout)


# ---------- helpers ----------
def allowed(sender_id: int, username: str | None) -> bool:
    if not ALLOW:
        return True
    return str(sender_id) in ALLOW or (username or "").lower() in ALLOW


async def chat_reply(text: str, sender_id: int, display: str,
                     use_search: bool = False) -> str:
    data = await api("/chat", {
        "message": text, "conversation_id": f"tg:{sender_id}",
        "channel": "telegram", "chat_id": str(sender_id), "display": display,
        "persona": PERSONA or None, "use_search": use_search}, timeout=180)
    mood = f"\n_{data.get('mood', '')} {data.get('mood_level', '')}_".strip()
    return (data.get("response") or "(no reply 😅)") + (f"\n{mood}" if data.get("mood") else "")


# user-facing DM commands (anyone allowed)
async def run_user_cmd(cmd: str, arg: str, sender_id: int) -> str | None:
    if cmd in ("reset",):
        await api("/reset", {"conversation_id": f"tg:{sender_id}"})
        return "fresh start ✨"
    if cmd in ("mood",):
        m = await api(f"/mood?conversation_id=tg:{sender_id}")
        return f"mood: {m.get('current')} ({m.get('level')}/10)"
    if cmd in ("persona",):
        return "personas live server-side — ask your owner to set WA/TG_PERSONA 💕"
    if cmd in ("search", "news", "wiki", "fact", "fact_check"):
        kind = {"search": "text", "fact": "fact_check"}.get(cmd, cmd)
        r = await api("/research", {"query": arg, "type": kind}, timeout=60)
        return r.get("results", "no results")
    return None


# owner commands (YOU, via outgoing messages starting with PREFIX, anywhere)
HELP = ("userbot cmds: `.mission <goal>` `.tick` `.send <chat> <msg>` "
        "`.contacts` `.mood [chat]` `.reset [chat]` `.persona <n>` `.help`")


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
    return HELP


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()  # interactive phone+code login on first run
    me = await client.get_me()
    print(f"✓ logged in as @{getattr(me, 'username', me.id)} ({me.id}) → {SERVER}")

    # ---- owner commands: your own outgoing messages starting with PREFIX ----
    @client.on(events.NewMessage(outgoing=True, pattern=rf"^{PREFIX}(\w+)\s*(.*)$"))
    async def _(event):
        cmd, arg = event.pattern_match.group(1).lower(), event.pattern_match.group(2)
        try:
            await event.reply(await run_owner_cmd(cmd, arg))
        except Exception as e:
            await event.reply(f"owner cmd failed: {e}")

    # ---- incoming DMs ----
    @client.on(events.NewMessage(incoming=True))
    async def _(event):
        if event.is_group or event.is_channel:
            if not GROUPS:
                return
            mentioned = me.username and f"@{me.username}".lower() in (event.raw_text or "").lower()
            replied = event.is_reply and (await event.get_reply_message()).sender_id == me.id
            if not (mentioned or replied):
                return
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
        sid = event.sender_id
        if not allowed(sid, getattr(sender, "username", None)):
            print(f"⏭ ignored {sid}")
            return
        text = (event.raw_text or "").strip()
        if not text:
            return
        name = getattr(sender, "first_name", None) or getattr(sender, "username", str(sid))
        print(f"📨 {name} ({sid}): {text[:80]}")
        try:
            await api("/contacts", {"channel": "telegram", "chat_id": str(sid),
                                    "display": name}, timeout=15)
            # !cmd or /cmd from contacts
            if text[:1] in ("!", "/") and len(text) > 1:
                parts = text[1:].split(None, 1)
                out = await run_user_cmd(parts[0].lower(),
                                         parts[1] if len(parts) > 1 else "", sid)
                if out:
                    await event.reply(out[:4000])
                    return
            async with client.action(event.chat_id, "typing"):
                reply = await chat_reply(text, sid, name, use_search=SEARCH)
            await event.reply(reply[:4000])
            print("✓ replied")
        except Exception as e:
            log.warning("reply failed: %s", e)
            try:
                await event.reply("hey, something went wrong on my end 😔 try again?")
            except Exception:
                pass

    # ---- outbox delivery loop (bot texts first / texts itself) ----
    async def outbox_loop():
        while True:
            try:
                items = (await api("/outbox?channel=telegram", timeout=20)).get("pending", [])
                for it in items:
                    try:
                        entity = int(it["to"]) if it["to"].lstrip("-").isdigit() else it["to"]
                        await client.send_message(entity, it["message"])
                        await api("/ack", {"id": it["id"], "ok": True}, timeout=15)
                        print(f"✉ sent → {it['to']}")
                    except Exception as e:
                        log.warning("send → %s failed: %s", it["to"], e)
                        await api("/ack", {"id": it["id"], "ok": False}, timeout=15)
            except Exception as e:
                log.debug("outbox poll: %s", e)
            await asyncio.sleep(POLL)

    asyncio.create_task(outbox_loop())
    print(f"✓ userbot live (dm allowlist: {sorted(ALLOW) or 'everyone'}, "
          f"groups: {GROUPS}, poll: {POLL}s)")
    await client.run_until_disconnected()


if __name__ == "__main__":
    if not TELETHON_OK:
        print("telethon not installed.\n  pip install telethon\nthen re-run.")
        sys.exit(1)
    asyncio.run(main())
