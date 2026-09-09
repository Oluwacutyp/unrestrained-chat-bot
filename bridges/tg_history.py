#!/usr/bin/env python3
"""Telegram HISTORY HARVEST — backfill training data from every chat.

Uses YOUR account (same login as the userbot). Walks all dialogs — DMs,
groups, channels — pairs consecutive replies into (user, response) rows
and appends them to the same train/trajectories.jsonl the server exports
(`.train export` picks them up automatically). Re-runs resume per chat.

IMPORTANT: stop the userbot first — one session file, one process:
  pkill -f telegram_userbot
  python bridges/tg_history.py --list             # see what's there
  python bridges/tg_history.py --all --limit 500  # harvest
  python bridges/telegram_userbot.py              # restart her

Flags:
  --list               show dialogs only (no downloading)
  --all                harvest every dialog (default)
  --limit N            messages per chat, newest-first (default 500)
  --include a,b        only chats whose id/title matches (substring ok)
  --exclude c,d        skip chats whose id/title matches
  --no-channels        skip broadcast channels
  --min-chars N        drop messages shorter than N chars (default 4)
  --with-forwards      keep forwarded messages (skipped by default)
  --dry-run            count only, write nothing
  --workspace PATH     override config workspace
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from telethon import TelegramClient  # type: ignore
    from telethon.tl.types import Channel, Chat, User  # type: ignore
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False

from godquant.train.collector import TrajectoryLogger
from godquant.train.history import (channel_rows, dedupe, load_state,
                                    pair_messages, save_state)

API_ID = int(os.environ.get("TG_API_ID", "0") or 0)
API_HASH = os.environ.get("TG_API_HASH", "")
SESSION = os.environ.get("TG_SESSION",
                         str(Path.home() / ".godquant" / "tg_userbot"))


def _workspace(override: str = "") -> str:
    if override:
        return override
    try:
        from godquant.config import load_config
        return load_config().resolved_workspace()
    except Exception:
        return str(Path.home() / ".godquant" / "workspace")


def _kind(entity) -> str:
    if isinstance(entity, User):
        return "dm"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "group"
    if isinstance(entity, Chat):
        return "group"
    return "other"


def _match(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)


async def _harvest(args) -> int:
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    ws = _workspace(args.workspace)
    log = TrajectoryLogger(ws)
    state = load_state(ws)
    inc = [x.strip().lower() for x in (args.include or "").split(",") if x.strip()]
    exc = [x.strip().lower() for x in (args.exclude or "").split(",") if x.strip()]
    total_msgs = total_rows = 0
    async for d in client.iter_dialogs():
        kind = _kind(d.entity)
        title = d.title or d.name or str(d.id)
        hay = f"{d.id} {title}"
        if inc and not _match(hay, inc):
            continue
        if exc and _match(hay, exc):
            continue
        if kind == "channel" and args.no_channels:
            continue
        if args.list:
            print(f"{d.id} [{kind}] {title[:70]}")
            continue
        last = state.get(str(d.id), 0)
        msgs: list[dict] = []
        max_id = last
        async for m in client.iter_messages(d.entity, limit=args.limit,
                                           min_id=last):
            max_id = max(max_id, m.id)
            if m.forward and not args.with_forwards:
                continue
            txt = m.message or ""
            if not txt.strip():
                continue
            msgs.append({"sender": str(m.sender_id or m.chat_id or "?"),
                         "text": txt,
                         "ts": m.date.timestamp() if m.date else 0})
        msgs.reverse()
        if kind == "channel":
            rows = channel_rows([m["text"] for m in msgs], title)
        else:
            rows = pair_messages(msgs, min_chars=args.min_chars)
        rows = dedupe(rows)
        total_msgs += len(msgs)
        total_rows += len(rows)
        print(f"{title[:50]} [{kind}] {len(msgs)} msgs -> {len(rows)} rows")
        if not args.dry_run:
            for u, a, _ in rows:
                log.log_history(u, a, chat=title[:120], chat_type=kind)
            state[str(d.id)] = max_id
            save_state(ws, state)
    await client.disconnect()
    print(f"done: {total_msgs} msgs -> {total_rows} rows"
          f"{' (dry run)' if args.dry_run else ''}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Harvest TG history into training data")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--include", default="")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--no-channels", action="store_true")
    ap.add_argument("--min-chars", type=int, default=4)
    ap.add_argument("--with-forwards", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workspace", default="")
    args = ap.parse_args(argv)
    if not TELETHON_OK:
        print("pip install telethon  (history harvest needs it)")
        return 1
    if not API_ID or not API_HASH:
        print("Set TG_API_ID and TG_API_HASH from https://my.telegram.org/apps")
        return 1
    print("NOTE: stop the userbot first (pkill -f telegram_userbot) — "
          "shared session file.")
    return asyncio.run(_harvest(args))


if __name__ == "__main__":
    sys.exit(main())
