#!/usr/bin/env python3
"""Import past Telegram history into the brain (pre-deployment memory).

Reads a dialog's history with YOUR account and POSTs it to /import: the
brain backfills bond scores (with real timestamps for decay/streaks),
extracts facts, and stores context — so Devon knows you BEFORE day one.

Usage:
  pip install telethon
  export TG_API_ID=... TG_API_HASH=...
  python gq.py serve &                                  # brain must run
  python bridges/tg_import.py 123456789 --limit 300     # a DM (user id)
  python bridges/tg_import.py @somefriend --limit 200
  python bridges/tg_import.py me                        # Saved Messages
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request


def build_items(records: list[dict]) -> list[dict]:
    """Pure: telethon-shaped records → /import payload items."""
    items = []
    for r in records:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        items.append({"role": "assistant" if r.get("out") else "user",
                      "text": text[:1000],
                      "ts": float(r.get("ts") or 0),
                      "sender": str(r.get("sender_id") or r.get("chat_id") or ""),
                      "sender_name": r.get("sender_name") or ""})
    return items


def post_import(server: str, channel: str, chat_id: str, display: str,
                items: list[dict]) -> dict:
    req = urllib.request.Request(
        server.rstrip("/") + "/import",
        data=json.dumps({"channel": channel, "chat_id": chat_id,
                         "display": display, "messages": items}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


async def amain(args) -> dict:
    from telethon import TelegramClient  # lazy: --help works without it
    api_id = int(os.environ.get("TG_API_ID", "0") or 0)
    api_hash = os.environ.get("TG_API_HASH", "")
    if not api_id or not api_hash:
        sys.exit("Set TG_API_ID and TG_API_HASH from https://my.telegram.org/apps")
    session = os.environ.get("TG_SESSION", os.path.expanduser("~/.godquant/tg_userbot"))
    client = TelegramClient(session, api_id, api_hash)
    await client.start()
    entity = await client.get_entity(args.chat)
    chat_id = str(getattr(entity, "id", args.chat))
    display = getattr(entity, "first_name", None) or \
        getattr(entity, "username", None) or getattr(entity, "title", None) or chat_id
    is_group = bool(getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False))
    print(f"◈ reading last {args.limit} msgs from {display} "
          f"({'group' if is_group else 'DM'})…")
    records = []
    async for m in client.iter_messages(entity, limit=args.limit):
        if not getattr(m, "text", None):
            continue
        sender_name = ""
        if is_group:
            try:
                s = await m.get_sender()
                sender_name = getattr(s, "first_name", "") or ""
            except Exception:
                pass
        records.append({"text": m.text, "out": bool(getattr(m, "out", False)),
                        "ts": m.date.timestamp() if getattr(m, "date", None) else 0,
                        "sender_id": getattr(m, "sender_id", None) or chat_id,
                        "chat_id": chat_id, "sender_name": sender_name})
    records.reverse()  # chronological for streaks/decay
    await client.disconnect()
    items = build_items(records)
    print(f"◈ posting {len(items)} msgs → {args.server}/import …")
    res = post_import(args.server, "telegram", chat_id, display, items)
    print(f"◈ imported {res.get('imported', 0)} msgs, "
          f"learned {len(res.get('facts', []))} facts")
    for f in res.get("facts", [])[:15]:
        print(f"   • {f}")
    return res


def main():
    ap = argparse.ArgumentParser(description="Import Telegram history into the brain")
    ap.add_argument("chat", help="user id, @username, or 'me' (Saved Messages)")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--server", default=os.environ.get("GQ_SERVER",
                                                       "http://127.0.0.1:5000"))
    args = ap.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
