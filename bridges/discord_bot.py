#!/usr/bin/env python3
"""Discord bridge — Devon on Discord. DMs + guild mentions, one brain.

Setup (one time):
  pip install -U discord.py
  # Discord Developer Portal → Applications → New → Bot → token
  # ⚠️ enable PRIVILEGED 'MESSAGE CONTENT INTENT' or the bot sees no text
  # OAuth2 → URL Generator → scopes bot + applications.commands,
  #   permission Send Messages/Read Messages → open URL to invite
  export DISCORD_TOKEN=... DISCORD_OWNER_ID=123   # your discord user id
  python gq.py serve &                             # the brain
  python bridges/discord_bot.py

Env knobs:
  GQ_SERVER DISCORD_OWNER_ID DISCORD_PREFIX=. DISCORD_GUILDS= (csv, empty=all)
  DISCORD_HUMANIZE=1 HUMAN_WPM=45 HUMAN_MAXPM=20 HUMAN_GAP=4
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("discord-bridge")

try:
    import discord  # type: ignore
    DISCORD_OK = True
except ImportError:
    DISCORD_OK = False

from godquant.companion.humanize import (RateLimiter, asleep, bubble_gap,
                                        plan_typos, read_delay,
                                        split_bubbles, typing_delay)

SERVER = os.environ.get("GQ_SERVER", "http://127.0.0.1:5000").rstrip("/")
TOKEN = os.environ.get("DISCORD_TOKEN", "")
OWNER_ID = int(os.environ.get("DISCORD_OWNER_ID", "0") or 0)
PREFIX = os.environ.get("DISCORD_PREFIX", ".")
GUILDS = {g.strip() for g in os.environ.get("DISCORD_GUILDS", "").split(",")
          if g.strip()}
HUMANIZE = os.environ.get("DISCORD_HUMANIZE", "1") == "1"
HUMAN_WPM = int(os.environ.get("HUMAN_WPM", "45"))
PERSONA = os.environ.get("DISCORD_PERSONA", "")
AMBIENT = os.environ.get("DISCORD_AMBIENT", "1") == "1"
AMBIENT_P = float(os.environ.get("DISCORD_AMBIENT_P", "0.15"))
AMBIENT_MAXH = int(os.environ.get("DISCORD_AMBIENT_MAXH", "6"))
AMBIENT_CD = int(os.environ.get("DISCORD_AMBIENT_CD", "600"))
WELCOME_DM = os.environ.get("DISCORD_WELCOME_DM", "1") == "1"
WELCOME_CHAN = os.environ.get("DISCORD_WELCOME_CHAN", "")
CATCHUP = os.environ.get("DISCORD_CATCHUP", "0") == "1"
CATCHUP_N = int(os.environ.get("DISCORD_CATCHUP_N", "50"))
_ambient_last: dict = {}   # channel -> last ambient ts
_ambient_hits: dict = {}   # channel -> [ts this hour]
_bond_cache: dict = {}     # author -> (ts, level)
TOPIC_WORDS = frozenset({
    "code", "coding", "python", "bug", "debug", "program", "server",
    "trade", "trading", "stocks", "crypto", "bitcoin",
    "hiking", "hike", "trail", "bike", "biking", "camp", "snow",
    "food", "recipe", "coffee", "cook", "eat",
    "nigeria", "pidgin", "lagos", "afrobeats",
    "music", "game", "gaming", "anime", "movie", "gym", "love",
})

limiter = RateLimiter(max_per_min=int(os.environ.get("HUMAN_MAXPM", "20")),
                      min_chat_gap=float(os.environ.get("HUMAN_GAP", "4")))
_warn_last: dict = {}


# ---------- server API ----------
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
    """Self-alert to the owner's DM (throttled 5 min per kind). Never raises."""
    try:
        now = time.time()
        if now - _warn_last.get(kind, 0) < 300:
            return
        _warn_last[kind] = now
        await asyncio.to_thread(_api, "/warn", {"channel": "discord",
                                                "kind": kind,
                                                "message": message[:400]}, 15)
    except Exception:
        pass


# ---------- pure logic (unit-tested with fakes) ----------
def should_reply(message, bot_id: int) -> bool:
    """DMs always; guilds only on mention; never bots/empties."""
    if getattr(message.author, "bot", False):
        return False
    if not (message.content or "").strip():
        return False
    if message.guild is None:
        return True
    return any(getattr(m, "id", None) == bot_id
               for m in (message.mentions or []))


def guild_allowed(guild_id) -> bool:
    return not GUILDS or str(guild_id) in GUILDS


async def chat_full(text: str, author_id, display: str, channel_id,
                    is_group: bool = False) -> dict:
    return await api("/chat", {
        "message": text, "conversation_id": f"discord:{channel_id}",
        "channel": "discord", "chat_id": str(channel_id), "display": display,
        "sender_name": display, "is_group": is_group,
        "bond_id": f"discord:{author_id}",
        "persona": PERSONA or None}, timeout=180)


async def human_send(channel, text: str, mood: str = "neutral",
                     depth: float = 0.0, energy: str = "calm"):
    bubbles = plan_typos(split_bubbles(text))
    key = "discord:%s" % getattr(channel, "id", "?")
    if not HUMANIZE:
        for b in bubbles:
            await limiter.wait(key)
            await channel.send(b[:2000])
        return
    await asleep(read_delay(len(text), depth))
    for i, b in enumerate(bubbles):
        if i:
            await asleep(bubble_gap())
        await limiter.wait(key)
        try:
            async with channel.typing():
                await asleep(typing_delay(len(b), wpm=HUMAN_WPM, mood=mood,
                                          energy=energy))
        except Exception:
            await asleep(typing_delay(len(b), wpm=HUMAN_WPM, mood=mood,
                                      energy=energy))
        await channel.send(b[:2000])


HELP = ("discord cmds: `.tick` `.send <channel_or_user_id> <msg>` `.contacts` "
        "`.mood` `.bond [user] [0-3|auto]` `.memory [user]` "
        "`.import [limit]` (this channel's history) `.help`")


async def run_owner_cmd(cmd: str, arg: str) -> str:
    """Owner commands via the shared core (import handled in handler)."""
    from bridges.owner import run_owner_command
    return await run_owner_command(api, "discord", cmd, arg)


async def handle_message(message, client) -> str | None:
    """Route one Discord message. Returns reply text or None."""
    bot_id = getattr(getattr(client, "user", None), "id", 0)
    text = (message.content or "").strip()
    if getattr(message.author, "bot", False):
        return None
    # owner commands (anywhere, PREFIX-prefixed)
    if OWNER_ID and message.author.id == OWNER_ID and text.startswith(PREFIX) \
            and len(text) > len(PREFIX):
        cmd, _, arg = text[len(PREFIX):].partition(" ")
        if cmd.lower() == "import":
            return await import_channel(message)
        try:
            out = await run_owner_cmd(cmd.lower(), arg.strip())
        except Exception as e:
            out = f"owner cmd failed: {e}"
        await message.channel.send(out[:2000])
        return out
    if message.guild is not None and not guild_allowed(message.guild.id):
        return None
    if not should_reply(message, bot_id):
        if message.guild is not None:
            return await maybe_ambient(message, client)
        return None
    is_group = message.guild is not None
    display = getattr(message.author, "display_name", None) or \
        getattr(message.author, "name", str(message.author.id))
    try:
        full = await chat_full(text, message.author.id, display,
                               message.channel.id, is_group)
        reply = full.get("response") or "(no reply 😅)"
        await human_send(message.channel, reply,
                         mood=full.get("mood", "neutral"),
                         depth=float(full.get("substance", 0) or 0))
        return reply
    except Exception as e:
        log.warning("reply failed: %s", e)
        await warn("reply_failed", f"discord reply failed: {e}")
        try:
            await message.channel.send("hey, something went wrong 😔 try again?")
        except Exception:
            pass
        return None


async def import_channel(message) -> str:
    """Owner .import: ingest this channel's history as pre-deployment memory."""
    limit = 200
    for p in (message.content or "").split()[1:]:
        if p.isdigit():
            limit = min(300, int(p))
    items = []
    try:
        async for m in message.channel.history(limit=limit):
            t = (m.content or "").strip()
            if not t:
                continue
            ts = m.created_at.timestamp() if getattr(m, "created_at", None) else 0
            items.append({"role": "assistant" if getattr(m.author, "bot", False)
                          else "user",
                          "text": t[:1000], "ts": ts,
                          "sender": str(m.author.id),
                          "sender_name": getattr(m.author, "display_name", "")})
    except Exception as e:
        return f"import failed: {e}"
    items.reverse()  # chronological for streaks/decay
    try:
        r = await api("/import", {"channel": "discord",
                                  "chat_id": str(message.channel.id),
                                  "display": getattr(message.channel, "name",
                                                     "discord"),
                                  "messages": items}, timeout=120)
    except Exception as e:
        return f"import failed: {e}"
    facts = r.get("facts", [])
    head = f"imported {r.get('imported', 0)} msgs, learned {len(facts)} facts"
    return head if not facts else head + ": " + ", ".join(facts[:12])


async def deliver_outbox(client) -> list:
    sent = []
    try:
        items = (await api("/outbox?channel=discord", timeout=20)).get("pending", [])
    except Exception as e:
        log.debug("outbox poll: %s", e)
        return sent
    for it in items:
        try:
            entity_id = int(it["to"])
        except (TypeError, ValueError):
            await api("/ack", {"id": it["id"], "ok": False}, timeout=15)
            continue
        dest = client.get_user(entity_id) or client.get_channel(entity_id)
        if dest is None:
            for fetch in ("fetch_user", "fetch_channel"):
                try:
                    dest = await getattr(client, fetch)(entity_id)
                    break
                except Exception:
                    continue
        if dest is None:
            await api("/ack", {"id": it["id"], "ok": False}, timeout=15)
            continue
        try:
            await human_send(dest, it["message"])
            await api("/ack", {"id": it["id"], "ok": True}, timeout=15)
            sent.append(it["to"])
        except Exception as e:
            log.warning("send → %s failed: %s", it["to"], e)
            await api("/ack", {"id": it["id"], "ok": False}, timeout=15)
    return sent


def ambient_score(text: str, level: int = 0) -> float:
    """0..0.9 — how much Devon wants to join this guild thread. Pure."""
    s = AMBIENT_P
    low = (text or "").lower()
    if "?" in low:
        s += 0.25  # questions pull her in
    if any(w in low for w in TOPIC_WORDS):
        s += 0.20  # her topics: code, outdoors, food, music...
    s += 0.05 * min(max(level, 0), 3)  # friends get attention
    if len(low) > 200:
        s += 0.10  # effort deserves engagement
    return min(0.9, s)


async def maybe_ambient(message, client) -> str | None:
    """Guild chatter (no mention): join the topic if she feels like it."""
    if not AMBIENT:
        return None
    ch = str(message.channel.id)
    now = time.time()
    if now - _ambient_last.get(ch, 0) < AMBIENT_CD:
        return None
    hits = [t for t in _ambient_hits.get(ch, []) if now - t < 3600]
    if len(hits) >= AMBIENT_MAXH:
        return None
    lvl = 0
    try:
        ck = f"discord:{message.author.id}"
        c = _bond_cache.get(ck)
        if not c or now - c[0] > 600:
            b = await api(f"/bond?channel=discord&chat_id={message.author.id}",
                          timeout=15)
            lvl = (b.get("bond") or {}).get("level", 0)
            _bond_cache[ck] = (now, lvl)
        else:
            lvl = c[1]
    except Exception:
        pass
    if random.random() > ambient_score(message.content, lvl):
        return None
    _ambient_last[ch] = now
    hits.append(now)
    _ambient_hits[ch] = hits
    display = getattr(message.author, "display_name", None) or str(message.author.id)
    try:
        full = await chat_full(message.content, message.author.id, display,
                               message.channel.id, True)
        reply = full.get("response") or "(no reply \U0001F605)"
        await human_send(message.channel, reply,
                         mood=full.get("mood", "neutral"),
                         depth=float(full.get("substance", 0) or 0))
        print(f"\U0001F4AC ambient → #{getattr(message.channel, 'name', ch)}")
        return reply
    except Exception as e:
        await warn("reply_failed", f"discord ambient failed: {e}")
        return None


async def welcome_member(member, client):
    """New human joined a server: DM them like a curious local + maybe wave publicly."""
    if not WELCOME_DM and not WELCOME_CHAN:
        return
    name = getattr(member, "display_name", "newcomer")
    guild = getattr(getattr(member, "guild", None), "name", "the server")
    try:
        full = await chat_full(
            f"[EVENT: {name} just joined {guild}. Greet them warmly in ONE short "
            f"flirty message and ask their name. Never mention you are a bot.]",
            member.id, name, f"welcome:{getattr(member.guild, 'id', '?')}", True)
        text = full.get("response") or f"well hey {name} \U0001F440 welcome in!"
    except Exception:
        text = f"well hey {name} \U0001F440 welcome in!"
    if WELCOME_DM:
        try:
            await human_send(await member.create_dm(), text)
        except Exception:
            pass
    if WELCOME_CHAN:
        try:
            for c in (member.guild.text_channels or []):
                if c.name == WELCOME_CHAN or str(c.id) == WELCOME_CHAN:
                    await human_send(c, text)
                    break
        except Exception:
            pass


async def discord_catchup(client):
    """Opt-in (DISCORD_CATCHUP=1): read recent guild history as memory."""
    if not CATCHUP:
        return
    for guild in getattr(client, "guilds", []):
        if not guild_allowed(guild.id):
            continue
        for chan in (getattr(guild, "text_channels", None) or [])[:5]:
            try:
                items = []
                async for m in chan.history(limit=CATCHUP_N):
                    t = (m.content or "").strip()
                    if not t:
                        continue
                    ts = m.created_at.timestamp() if getattr(m, "created_at", None) else 0
                    items.append({"role": "assistant" if getattr(m.author, "bot", False)
                                  else "user", "text": t[:1000], "ts": ts,
                                  "sender": str(m.author.id)})
                if not items:
                    continue
                items.reverse()
                await api("/import", {"channel": "discord",
                                      "chat_id": str(chan.id),
                                      "display": getattr(chan, "name", "?"),
                                      "messages": items[:300]}, timeout=120)
                log.info("catchup #%s: %d", getattr(chan, "name", "?"), len(items))
            except Exception as e:
                log.debug("catchup #%s: %s", getattr(chan, "name", "?"), e)


async def main():
    if not DISCORD_OK:
        print("discord.py not installed.\n  pip install -U discord.py\nthen re-run.")
        sys.exit(1)
    if not TOKEN:
        print("Set DISCORD_TOKEN from the Discord Developer Portal.")
        sys.exit(1)
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"✓ discord live as {client.user} → {SERVER}")
        if OWNER_ID:
            print(f"  owner: {OWNER_ID}")
        client.loop.create_task(poll_loop())
        client.loop.create_task(discord_catchup(client))

    async def poll_loop():
        await client.wait_until_ready()
        while not client.is_closed():
            try:
                await deliver_outbox(client)
            except Exception as e:
                log.debug("outbox poll: %s", e)
            await asyncio.sleep(15)

    @client.event
    async def on_member_join(member):
        try:
            if getattr(member, 'bot', False):
                return
            if guild_allowed(member.guild.id):
                print(f"\U0001F44B welcome {getattr(member, 'display_name', '?')}")
                await welcome_member(member, client)
        except Exception:
            log.exception("welcome failed")

    @client.event
    async def on_message(message):
        try:
            await handle_message(message, client)
        except Exception:
            log.exception("message failed")

    await client.start(TOKEN)


if __name__ == "__main__":
    if not DISCORD_OK:
        print("discord.py not installed.\n  pip install -U discord.py\nthen re-run.")
        sys.exit(1)
    asyncio.run(main())
