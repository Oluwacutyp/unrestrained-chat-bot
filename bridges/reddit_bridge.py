#!/usr/bin/env python3
"""Reddit bridge — Devon on Reddit. Mentions + replies + DMs, one brain.

Setup (one time):
  pip install praw
  # https://www.reddit.com/prefs/apps → create SCRIPT app (free)
  export REDDIT_CLIENT_ID=... REDDIT_SECRET=... \\
         REDDIT_USER=botname REDDIT_PASS=... REDDIT_OWNER=yourname
  python bot.py serve &                            # the brain
  python bridges/reddit_bridge.py                  # poll loop
  python bridges/reddit_bridge.py --backfill 200   # own history → training

Env knobs:
  GQ_SERVER REDDIT_OWNER REDDIT_PREFIX=. REDDIT_POLL=60
  REDDIT_SUBS= (csv allowlist for mentions, empty = all)
Live chats auto-log to training data via /chat; backfill harvests your
own past comments/submissions as style rows.
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
log = logging.getLogger("reddit-bridge")

try:
    import praw  # type: ignore
    PRAW_OK = True
except ImportError:
    PRAW_OK = False

SERVER = os.environ.get("GQ_SERVER", "http://127.0.0.1:5000").rstrip("/")
PREFIX = os.environ.get("REDDIT_PREFIX", ".")
OWNER = os.environ.get("REDDIT_OWNER", "").lower()
POLL = int(os.environ.get("REDDIT_POLL", "60"))
SUBS = {s.strip().lower() for s in os.environ.get("REDDIT_SUBS", "").split(",")
        if s.strip()}
ME = os.environ.get("REDDIT_USER", "").lower()


def _api(path: str, payload: dict | None = None, timeout: int = 120):
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(SERVER + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _client():
    return praw.Reddit(client_id=os.environ["REDDIT_CLIENT_ID"],
                       client_secret=os.environ["REDDIT_SECRET"],
                       username=os.environ["REDDIT_USER"],
                       password=os.environ["REDDIT_PASS"],
                       user_agent=os.environ.get("REDDIT_UA", "godquant/1.0"))


def _chat(text: str, cid: str, sender: str) -> str:
    from bridges.owner import run_owner_command
    if sender == OWNER and text.startswith(PREFIX):
        cmd, _, arg = text[len(PREFIX):].partition(" ")
        async def _api2(path, payload=None):
            return _api(path, payload)
        import asyncio
        return asyncio.run(run_owner_command(_api2, "reddit", cmd, arg, cid))
    r = _api("/chat", {"message": text, "conversation_id": f"reddit:{cid}",
                       "sender_name": sender})
    return r.get("response", "")


def _poll(reddit) -> int:
    n = 0
    for item in reddit.inbox.unread(limit=50):
        try:
            author = (item.author.name if item.author else "").lower()
            if author in ("", ME):
                item.mark_read()
                continue
            if isinstance(item, praw.models.Comment):  # type: ignore
                sub = str(item.subreddit).lower()
                if SUBS and sub not in SUBS:
                    item.mark_read()
                    continue
                mentioned = ME and f"u/{ME}" in item.body.lower()
                parent_author = ""
                try:
                    parent = item.parent()
                    parent_author = (parent.author.name
                                     if parent.author else "").lower()
                except Exception:
                    pass
                if not mentioned and parent_author != ME and author != OWNER:
                    item.mark_read()
                    continue
                reply = _chat(item.body, item.fullname,
                              item.author.name if item.author else "?")
                if reply:
                    item.reply(reply[:9000])
                    n += 1
            elif isinstance(item, praw.models.Message):  # type: ignore
                reply = _chat(f"{item.subject}\n{item.body}".strip(),
                              f"u/{author}", author)
                if reply:
                    item.reply(reply[:9000])
                    n += 1
            item.mark_read()
        except Exception as e:
            log.warning("inbox item failed: %s", e)
    # outbox: to=u/user (DM) | to=t1_/t3_ fullname (reply)
    try:
        pending = _api("/outbox?channel=reddit", None, 20).get("pending", [])
    except Exception as e:
        log.debug("outbox: %s", e)
        return n
    for it in pending:
        try:
            to = str(it["to"])
            if to.startswith("u/"):
                reddit.redditor(to[2:]).message("devon", it["message"][:9000])
            elif to.startswith(("t1_", "t3_")):
                obj = reddit.comment(to) if to.startswith("t1_") \
                    else reddit.submission(to)
                obj.reply(it["message"][:9000])
            else:
                raise ValueError(f"bad target {to} (u/user or t1_/t3_)")
            _api("/ack", {"id": it["id"], "ok": True}, 15)
            n += 1
        except Exception as e:
            log.warning("outbox #%s: %s", it.get("id"), e)
            try:
                _api("/ack", {"id": it["id"], "ok": False}, 15)
            except Exception:
                pass
    return n


def _backfill(reddit, limit: int, workspace: str = "") -> int:
    from godquant.train.collector import TrajectoryLogger
    from godquant.train.history import channel_rows
    if not workspace:
        from godquant.config import load_config
        workspace = load_config().resolved_workspace()
    log = TrajectoryLogger(workspace)
    me = reddit.redditor(os.environ["REDDIT_USER"])
    texts = []
    try:
        for c in me.comments.new(limit=limit):
            texts.append(c.body or "")
        for s in me.submissions.new(limit=limit // 2):
            texts.append(f"{s.title or ''}\n{s.selftext or ''}".strip())
    except Exception as e:
        log.error("backfill fetch: %s", e)
        return 1
    rows = channel_rows(texts, f"reddit u/{os.environ['REDDIT_USER']}")
    for u, a, _ in rows:
        log.log_history(u, a, chat=f"u/{os.environ['REDDIT_USER']}",
                        chat_type="reddit")
    print(f"backfilled {len(texts)} items -> {len(rows)} rows")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="devon on reddit")
    ap.add_argument("--backfill", type=int, default=0,
                    help="harvest own history (N items) then exit")
    ap.add_argument("--workspace", default="")
    args = ap.parse_args(argv)
    if not PRAW_OK:
        print("pip install praw  (reddit bridge needs it)")
        return 1
    for k in ("REDDIT_CLIENT_ID", "REDDIT_SECRET", "REDDIT_USER",
              "REDDIT_PASS"):
        if not os.environ.get(k):
            print(f"export {k}=... (reddit script app, prefs/apps)")
            return 1
    reddit = _client()
    if args.backfill:
        return _backfill(reddit, args.backfill, args.workspace)
    print(f"✓ reddit live (u/{os.environ['REDDIT_USER']}, poll {POLL}s)")
    while True:
        try:
            n = _poll(reddit)
            if n:
                log.info("handled %d", n)
        except Exception as e:
            log.warning("poll: %s", e)
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
