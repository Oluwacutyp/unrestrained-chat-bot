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
  · `.bond [chat] [0-3]` (view/pin relationship level) · `.help`
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
