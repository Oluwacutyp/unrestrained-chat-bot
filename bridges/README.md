# Bridges — WhatsApp + Telegram (own-account userbot)

Both bridges talk to one brain: `python gq.py serve` (default `:5000`).
Both **reply AND deliver** — the outbox lets the bot text first / text itself.

```
                    ┌──────────────┐
  WhatsApp ────────▶│              │──reply──▶ WhatsApp chat
  Telegram (you) ──▶│  gq.py serve │──reply──▶ Telegram DM
                    │              │──tick──▶ outbox ──poll──▶ bridges send
                    └──────────────┘   ▲
                        /send          │ proactive ticker (silence watcher)
```

## Telegram userbot — your OWN account (Termux ✅)

This is **not** a Bot-API bot. It logs in as you via MTProto (Telethon):
replies to your DMs as you, and you command it by messaging yourself.

```bash
pip install telethon
export TG_API_ID=123456 TG_API_HASH=abcdef...   # https://my.telegram.org/apps (free)
python gq.py serve &                             # the brain
python bridges/telegram_userbot.py               # first run: phone number + login code
```

- Session persists at `~/.godquant/tg_userbot.session` — back it up, **never share it**
  (whoever holds it is logged in as you).
- DMs: anyone can chat by default; lock it down: `TG_ALLOW=12345,@friend`
- Groups: off by default (`TG_GROUPS=1` to enable, mention/reply only).
  Group chats share one conversation context; relationships stay per-sender
  and are capped at level 1 in groups (no pet names with an audience).
- **Owner commands** — send from YOUR account anywhere, starting with `.`:
  `.mission <goal>` · `.tick` (force proactive pass) · `.send <chat|me> <msg>`
  · `.contacts` · `.mood` · `.reset` · `.persona alex`
  · `.bond [chat] [0-3|auto]` (view/pin relationship level)
  · `.memory [chat]` (view stored facts + bond) · `.help`
- **Humanizer** (v3.3): read pauses (+depth for heavy texts), mood-paced
  typing (angry = fast, sad = slow), multi-bubble splits, rare human typos
  with `*corrections`, "got distracted" pauses in long rapid sessions, and
  anti-ban pacing. Tune: `HUMAN_WPM=45` `HUMAN_MAXPM=20` `HUMAN_GAP=4`;
  `TG_HUMANIZE=0` for instant sends.
- **Bonds** (v3.3): closeness is a 0-100 score from substance × warmth —
  spam earns ~nothing, insults build friction (bot goes cold), silence
  decays, day-streaks deepen. `.bond <chat>` shows score/friction/streak;
  `.bond <chat> auto` resumes auto-pilot after a pin.
- **DM commands** (contacts): `!reset` `!mood` `!search <q>` `!news <q>` `!wiki <q>` `!fact <q>`
  `!translate <text>` (English ↔ Naija pidgin, Devon is fluent)
- **Text itself**: `.send me good morning ❤` lands in your Saved Messages.
  The proactive ticker texts silent contacts automatically (see below).
- **Missed messages**: the userbot answers texts that arrive while it's running.
  If it was offline, those aren't replayed — opt into catch-up replies with
  `TG_CATCHUP=1` (only unread DMs < `TG_CATCHUP_MINS`=60 old, max
  `TG_CATCHUP_MAX`=5 dialogs, allowlist + bot-filter still apply).
- Termux: run inside `termux-wake-lock` / a Termux:Boot session to survive doze.

## WhatsApp — PC recommended (⚠️ Termux can't run Chrome)

`whatsapp-web.js` needs Puppeteer/Chrome, which doesn't exist on Android.
Run this bridge on a PC against a local or remote brain:

```bash
npm install whatsapp-web.js qrcode-terminal axios
python gq.py serve &                                # brain (same machine or reachable host)
AI_SERVER_URL=http://localhost:5000 WA_PERSONA=alex node bridges/whatsapp.js
# scan the QR with your WhatsApp → Linked Devices
```

- Lock responders: `ALLOWED_NUMBERS=2348012345678,234...`
- `!`-prefixed messages run full agent missions; replies are clean (no footers).
- Groups: mention/reply only by default; `WA_GROUP_OPEN=1` answers everything.
- Humanizer mirrors Telegram: `WA_HUMANIZE=0` disables, `HUMAN_WPM` tunes.
- **Text itself**: find your chat id in the bridge logs, then
  `curl -X POST localhost:5000/send -d '{"channel":"whatsapp","to":"234...@c.us","message":"hey me"}'`.
  The proactive ticker + `WA_POLL` loop deliver it like any outbound text.

## Discord (v3.5)

```bash
pip install -U discord.py   # enable MESSAGE CONTENT INTENT in the dev portal!
export DISCORD_TOKEN=... DISCORD_OWNER_ID=...   # your discord user id
python gq.py serve &
python bridges/discord_bot.py
```

DMs always answered; guilds only on mention (`DISCORD_GUILDS` allowlist).
Humanizer included (typing indicator, bubbles, typos). Owner cmds via DM:
`.tick` `.send` `.contacts` `.mood` `.bond` `.memory` + `.import [limit]`
(ingests the channel's history). Outbound queue supported (`discord` channel).

## WhatsApp without Chrome — Baileys (Termux ✅, v3.5 beta)

`whatsapp.js` needs Chrome (PC only). Selenium can't fix that — it needs a
browser too. **Baileys is pure JS** (websocket, no browser), so it runs on
Termux:

```bash
npm install @whiskeysockets/baileys qrcode-terminal axios pino
python gq.py serve &
AI_SERVER_URL=http://localhost:5000 WA_PAIR_NUMBER=2348012345678 node bridges/whatsapp_baileys.js
# enter the pairing code on your phone (Linked Devices → Link with number)
```

Same brain contract as the classic bridge (humanizer, groups, outbox,
self-alerts). Beta: keep `ALLOWED_NUMBERS` locked while testing.

## Importing past chats (pre-deployment memory)

Devon can know you before day one — import history, backfill bonds with real
timestamps, extract facts:

```bash
python bridges/tg_import.py 123456789 --limit 300   # Telegram DM/group
# or live: .import <chat> [limit]   (Telegram + Discord owner cmd)
```

## Self alerts (bot warnings → your own chat)

Bridge/brain failures are queued to YOURSELF: Telegram Saved Messages
(`GQ_OWNER_TG`, default `me`), WhatsApp self-chat (`GQ_OWNER_WA` — printed in
the bridge log on connect), Discord owner DM (`GQ_OWNER_DISCORD`). Throttled
to 1 per kind per 5 min, both bridge-side and server-side (`POST /warn`).

## Command deck (v3.6 — same on every platform)

One shared core (`bridges/owner.py` for Python, `bridges/wa_owner.js` for
WhatsApp). Telegram: your outgoing `.cmd` anywhere. Discord: owner DM or
server. WhatsApp: `WA_OWNER_NUMBER` + `.cmd` in any chat incl. self-chat.

| Command | What it does |
|---|---|
| `.persona` / `.persona devon` / `.persona 42 quant` / `.persona 42 clear` | show / set global / per-chat override / clear |
| `.bond [chat] [0-3\|auto]` · `.memory [chat]` · `.forget <chat> [deep]` | relationships + dossier control |
| `.models` · `.model <provider>` | chain info + runtime LLM switch (resets on restart) |
| `.mission <goal>` · `.code <task>` | multi-agent tasks · audited code → `workspace/code/` |
| `.exec <shell>` | ⚠️ raw shell (needs `GQ_ALLOW_EXEC=1` + restart) |
| `.tick` `.send` `.contacts` `.mood` `.reset` `.stats` `.import` `.help` | ops classics |
| `.remind <when> <text>` · `.reminders` · `.cancel <id>` · `.snooze <id> <when>` | alarms (`in 30m`, `tomorrow 7:00`, `every day 8:00`, `19:30`); bypass quiet hours; smart-weave into live relevant chats (v3.8, `GQ_REMIND_WEAVE=0` to disable, `GQ_REMIND_WINDOW` secs) |
| `.want <goal>` · `.mind [done\|drop <id>]` | intentions — hers and yours, in one view |
| `.journal [chat]` · `.dream` · `.brief` | relationship diary · consolidate now · morning briefing |
| `.note save <n> <text>` · `.note <n>` · `.note list` | long-term notes (case-insensitive names) |
| `.fetch <url>` | agent reads a page (scripts stripped, 2000 chars) |
| `.recall <query>` · `.mem list [scope]` · `.mem pin/unpin/del/edit` | unified memory: search everything, inspect + curate any memory (v4.0) |

Contacts get: `!reset` `!mood` `!search` `!news` `!wiki` `!fact` `!translate`.

## Discord autonomous agent (v3.6)

Beyond replies: `DISCORD_WELCOME_DM=1` DMs every new member a brain-written
greeting (+ `DISCORD_WELCOME_CHAN` for public waves); `DISCORD_AMBIENT=1`
joins guild threads it finds interesting (questions + her topics + friends,
tunable `DISCORD_AMBIENT_P`, hourly cap, per-channel cooldown);
`DISCORD_CATCHUP=1` imports recent guild history on boot. Bots can't join
servers by themselves — invite link still required, then she's a local.

## Your own model (no cloud needed)

- **llama-server** (GGUF on Termux/PC): point any OpenAI-compatible kind at it:
  `GQ_PROVIDER=ollama GQ_BASE_URL=http://127.0.0.1:8080/v1` (the `/v1` suffix
  selects OpenAI-compat mode). Then `.model ollama` anytime.
- **Ollama** (PC): `GQ_PROVIDER=ollama` (+ `OLLAMA_HOST` if remote).
- **llamacpp provider** (in-process): `GQ_PROVIDER=llamacpp` with model path
  configured — no server process at all. Heaviest option, cheapest runtime.
- Check the live chain anytime: `.models` (also `python bot.py doctor`).

## Proactive texting (both channels)

The server's ticker (every `GQ_PROACTIVE` seconds, default 300) watches every
registered contact. After `GQ_NUDGE_AFTER` seconds of silence (default 3600,
max `max_nudges`/day, respects `GQ_QUIET="1-6"`), it generates an in-character
opener from persona + live mood and queues it to the outbox. Bridges deliver
within one poll interval. Disable per-contact:

```bash
python gq.py contacts --disable whatsapp 234...@c.us
python gq.py tick        # force one proactive pass now
python gq.py contacts    # list registry + outbox stats
```
