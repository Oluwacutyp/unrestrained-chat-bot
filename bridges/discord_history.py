#!/usr/bin/env python3
"""Discord HISTORY HARVEST — guild channels → training data.

Mirrors tg_history.py: walks text channels the bot can see, pairs
consecutive different-author messages, appends to train/trajectories.jsonl.
Run the discord bot once first so it has joined your servers, then:

  python bridges/discord_history.py --list
  python bridges/discord_history.py --all --limit 500

Flags: --list --all(default) --limit N --include/--exclude (name substrings)
       --min-chars N --dry-run --workspace PATH
Needs DISCORD_TOKEN (same bot token). DMs need prior contact; guilds work.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import discord  # type: ignore
    DISCORD_OK = True
except ImportError:
    DISCORD_OK = False

from godquant.train.collector import TrajectoryLogger
from godquant.train.history import (dedupe, load_state, pair_messages,
                                    save_state)

TOKEN = os.environ.get("DISCORD_TOKEN", "")


def _workspace(override: str = "") -> str:
    if override:
        return override
    try:
        from godquant.config import load_config
        return load_config().resolved_workspace()
    except Exception:
        return str(Path.home() / ".godquant" / "workspace")


def _match(text: str, patterns: list[str]) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)


async def _harvest(args) -> int:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    box: dict = {}

    @client.event
    async def on_ready():
        try:
            ws = _workspace(args.workspace)
            tlog = TrajectoryLogger(ws)
            state = load_state(ws)
            inc = [x.strip().lower() for x in args.include.split(",")
                   if x.strip()]
            exc = [x.strip().lower() for x in args.exclude.split(",")
                   if x.strip()]
            total_msgs = total_rows = 0
            for g in client.guilds:
                for ch in g.text_channels:
                    hay = f"{g.name} #{ch.name}"
                    if inc and not _match(hay, inc):
                        continue
                    if exc and _match(hay, exc):
                        continue
                    if args.list:
                        print(f"{g.name} #{ch.name} [{ch.id}]")
                        continue
                    key = f"discord:{ch.id}"
                    msgs: list[dict] = []
                    max_id = state.get(key, 0)
                    try:
                        async for m in ch.history(limit=args.limit,
                                                  oldest_first=True,
                                                  after=discord.Object(
                                                      id=max_id) if max_id
                                                  else None):
                            if m.author.bot and not m.content.strip():
                                continue
                            if not (m.content or "").strip():
                                continue
                            max_id = max(max_id, m.id)
                            msgs.append({"sender": str(m.author.id),
                                         "text": m.content,
                                         "ts": m.created_at.timestamp()})
                    except Exception as e:
                        print(f"{hay}: unreadable ({e})")
                        continue
                    rows = dedupe(pair_messages(msgs,
                                                min_chars=args.min_chars))
                    total_msgs += len(msgs)
                    total_rows += len(rows)
                    print(f"{hay}: {len(msgs)} msgs -> {len(rows)} rows")
                    if not args.dry_run:
                        for u, a, _ in rows:
                            tlog.log_history(u, a, chat=hay[:120],
                                             chat_type="discord")
                        state[key] = max_id
                        save_state(ws, state)
            print(f"done: {total_msgs} msgs -> {total_rows} rows"
                  f"{' (dry run)' if args.dry_run else ''}")
            box["rc"] = 0
        except Exception as e:
            print(f"harvest failed: {e}")
            box["rc"] = 1
        finally:
            await client.close()

    await client.start(TOKEN)
    return box.get("rc", 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="harvest discord into training")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--include", default="")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--min-chars", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workspace", default="")
    args = ap.parse_args(argv)
    if not DISCORD_OK:
        print("pip install -U discord.py  (discord harvest needs it)")
        return 1
    if not TOKEN:
        print("export DISCORD_TOKEN=... (same bot token)")
        return 1
    return asyncio.run(_harvest(args))


if __name__ == "__main__":
    sys.exit(main())
