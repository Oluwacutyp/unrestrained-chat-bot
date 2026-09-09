#!/usr/bin/env python3
"""X (Twitter) bridge — Devon on X. Mentions + replies, one brain.

HONEST LIMITS (set by X, not us): the free API tier can POST but barely
read — mentions timeline needs **Basic ($100/mo)** or higher, DMs need
much more. This bridge does mentions+replies+outbox posts and degrades
gracefully when an endpoint 403s.

Setup (one time):
  pip install tweepy
  # https://developer.x.com → project + app → keys & tokens
  export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... \\
         X_ACCESS_SECRET=... X_BEARER=... X_OWNER_ID=123
  python bot.py serve &
  python bridges/x_bridge.py
  python bridges/x_bridge.py --post "hello world"   # one-shot test

Env knobs: GQ_SERVER X_OWNER_ID X_PREFIX=. X_POLL=120
Live chats auto-log to training data via /chat.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("x-bridge")

try:
    import tweepy  # type: ignore
    TWEEPY_OK = True
except ImportError:
    TWEEPY_OK = False

SERVER = os.environ.get("GQ_SERVER", "http://127.0.0.1:5000").rstrip("/")
PREFIX = os.environ.get("X_PREFIX", ".")
OWNER_ID = os.environ.get("X_OWNER_ID", "")
POLL = int(os.environ.get("X_POLL", "120"))
STATE = Path.home() / ".godquant" / "x_state.json"


def _api(path: str, payload: dict | None = None, timeout: int = 120):
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(SERVER + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st: dict):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st))
    except Exception as e:
        log.debug("state: %s", e)


def _chat(text: str, cid: str, sender: str, sender_id: str) -> str:
    if sender_id == OWNER_ID and text.startswith(PREFIX):
        from bridges.owner import run_owner_command
        import asyncio
        cmd, _, arg = text[len(PREFIX):].partition(" ")

        async def _api2(path, payload=None):
            return _api(path, payload)
        return asyncio.run(run_owner_command(_api2, "x", cmd, arg, cid))
    r = _api("/chat", {"message": text, "conversation_id": f"x:{cid}",
                       "sender_name": sender})
    return r.get("response", "")


def _poll(client) -> int:
    st = _state()
    n = 0
    try:
        resp = client.get_users_mentions(
            client.get_me().data.id,
            since_id=st.get("since_id"),
            max_results=20,
            expansions=["author_id"],
            tweet_fields=["conversation_id"])
    except Exception as e:
        log.warning("mentions unavailable (tier/permissions?): %s", e)
        return 0
    users = {u.id: u.username for u in (resp.includes or {}).get("users", [])}
    for t in (resp.data or [])[::-1]:
        try:
            st["since_id"] = str(t.id)
            author = users.get(t.author_id, "?")
            reply = _chat(t.text, str(t.id), author, str(t.author_id))
            if reply:
                client.create_tweet(text=f"@{author} {reply}"[:280],
                                    in_reply_to_tweet_id=t.id)
                n += 1
        except Exception as e:
            log.warning("mention %s: %s", t.id, e)
        finally:
            _save_state(st)
    try:
        pending = _api("/outbox?channel=x", None, 20).get("pending", [])
    except Exception as e:
        log.debug("outbox: %s", e)
        return n
    for it in pending:
        try:
            to = str(it["to"])
            if to.startswith("@"):
                client.create_tweet(text=f"{to} {it['message']}"[:280])
            elif to.isdigit():
                client.create_tweet(text=str(it["message"])[:280],
                                    in_reply_to_tweet_id=int(to))
            else:
                raise ValueError(f"bad target {to} (@handle or tweet id)")
            _api("/ack", {"id": it["id"], "ok": True}, 15)
            n += 1
        except Exception as e:
            log.warning("outbox #%s: %s", it.get("id"), e)
            try:
                _api("/ack", {"id": it["id"], "ok": False}, 15)
            except Exception:
                pass
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="devon on X")
    ap.add_argument("--post", default="", help="one-shot tweet, then exit")
    args = ap.parse_args(argv)
    if not TWEEPY_OK:
        print("pip install tweepy  (x bridge needs it)")
        return 1
    for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
              "X_ACCESS_SECRET", "X_BEARER"):
        if not os.environ.get(k):
            print(f"export {k}=... (developer.x.com keys & tokens)")
            return 1
    client = tweepy.Client(bearer_token=os.environ["X_BEARER"],
                           consumer_key=os.environ["X_API_KEY"],
                           consumer_secret=os.environ["X_API_SECRET"],
                           access_token=os.environ["X_ACCESS_TOKEN"],
                           access_token_secret=os.environ["X_ACCESS_SECRET"],
                           wait_on_rate_limit=True)
    if args.post:
        client.create_tweet(text=args.post[:280])
        print("posted ✓")
        return 0
    print(f"✓ x live (poll {POLL}s, mentions need Basic tier+)")
    while True:
        try:
            n = _poll(client)
            if n:
                log.info("handled %d", n)
        except Exception as e:
            log.warning("poll: %s", e)
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
