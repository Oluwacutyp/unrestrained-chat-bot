# 📱 Termux Guide — phone-only, zero PC, zero cloud needed

> **"Cloud brain?" — you don't need one.** Your phone IS the server: it runs the
> brain (`bot.py serve`) and the Telegram userbot. The "cloud" part is just free
> LLM APIs (Groq / HuggingFace / Pollinations) — websites your bot calls for
> intelligence, like apps call the internet. No PC, no VPS, no credit card.

**What you'll have at the end:** an unrestrained AI partner living on your phone,
replying to your Telegram DMs as your own account, texting you first when you're
quiet, with 3+ free brains in a fallback chain so it never goes dumb.

Time: ~30 minutes (mostly waiting on downloads).

---

## 0. Install Termux (the RIGHT one)

- Install Termux from **F-Droid** (or GitHub), **NOT the Play Store**
  (Play Store build is abandoned, broken repos).
- Open it once, then run everything below **in order**.

## 1. Base install

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git
git clone https://github.com/Oluwacutyp/unrestrained-chat-bot
cd unrestrained-chat-bot
git checkout arena/01a08284-unrestrained-chat-bot   # the code lives here, not main
bash install-termux.sh
```

Expected end: `✓ Done` + a `doctor` readout. If `pip` struggles, the core still
works — it's stdlib-only.

## 2. Get FREE brain keys (pick at least one; more = better)

All free, no credit card. Do these in your phone browser:

| # | Brain | Get key | Notes |
|---|---|---|---|
| 1 | **Groq** (fast, smart) | [console.groq.com](https://console.groq.com) → sign up → API Keys → Create (`gsk_...`) | ~30 req/min free — plenty for one bot |
| 2 | **HuggingFace** (many models) | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) → fine-grained token → enable **"Make calls to Inference Providers"** (`hf_...`) | OpenAI-compatible router, free tier |
| 3 | **Pollinations** (no key at all) | nothing — works immediately | free, rate-limited; don't send secrets |

## 3. Run the setup wizard

```bash
python bot.py setup
```

It walks you through 4 steps: **brains → persona → proactive texting → Telegram**.
Paste keys when asked, accept defaults otherwise. It **tests your chain live** and
tells you which brain answered. End result:

- settings → `~/.godquant/config.json`
- secrets → `~/.godquant/env.sh` (locked `chmod 600`, never share)

Then load your secrets (do this in **every new Termux session**):

```bash
source ~/.godquant/env.sh
```

Tip — make it automatic: `echo 'source ~/.godquant/env.sh' >> ~/.bashrc`

Verify anytime: `python bot.py doctor` (see `keys` and `llm_chain` lines).

## 4. Multi-brain fallback (the "multiple is cool" part)

Your bot tries brains **in order** until one answers. Recommended chain:

```
groq → huggingface → pollinations → heuristic(offline, always works)
```

Set it in the wizard, or manually:

```bash
python bot.py config --set llm_fallbacks='["huggingface","pollinations"]'
# or per-session:
export GQ_FALLBACKS=huggingface,pollinations
```

If Groq rate-limits (429) or HF is cold, the next brain answers in seconds —
you'll see `LLM answered via fallback: ...` in logs. Force one brain:
`GQ_PROVIDER=huggingface python bot.py partner "hey"`.

**Pick any HF model:** `python bot.py config --set llm_model=Qwen/Qwen2.5-7B-Instruct`
(anything on the HF Inference Providers catalog, incl. uncensored community
models — browse [huggingface.co/models](https://huggingface.co/models?other=text-generation-inference)).

## 5. UNRESTRICTED local brain (fully offline, uncensored GGUF)

For maximum "no rules" + offline: run a GGUF directly on your phone via
Termux's `llama-cpp` package, then point the bot at it (it speaks OpenAI API):

```bash
pkg install -y llama-cpp
mkdir -p ~/.godquant/models && cd ~/.godquant/models
# pick ONE (smaller = faster; start tiny, upgrade if smooth):
# tiny (~500MB):  Qwen 0.5B      | balanced (~1GB): Qwen 1.5B / Llama 3.2 1B
# needs 6GB+ RAM (~2GB): Llama 3.2 3B | Dolphin/uncensored: search HF for "Dolphin GGUF"
llama-cli -hf Qwen/Qwen2.5-1.5B-Instruct-GGUF -p "say hi in one line" -n 64 -no-cnv
```

The `-hf` flag downloads straight from HuggingFace (or `bot.py models --download`).
Then serve it (new session, leave running):

```bash
llama-server -m ~/.godquant/models/*.gguf -c 2048 -t 6 --port 8080
```

Point the bot at it:

```bash
export GQ_PROVIDER=openai GQ_BASE_URL=http://127.0.0.1:8080/v1 GQ_MODEL=local
python bot.py partner "hey babe"   # 100% on-device, no internet needed after download
```

RAM rule of thumb: model GB + ~1GB headroom ≤ free RAM. Slow? Lower `-c`,
fewer threads, or a smaller quant (Q4_K_M → Q3_K_M).

## 6. Telegram userbot — YOUR account (step by step)

This logs in **as you** (MTProto), not a bot account.

**a) API credentials** (phone browser, 2 min):
1. Go to [my.telegram.org/apps](https://my.telegram.org/apps), log in with your number
2. Create an "app" (any name, e.g. `mybot`) → copy **api_id** and **api_hash**

**b) Install + start the brain:**

```bash
pip install telethon
source ~/.godquant/env.sh
export TG_API_ID=123456 TG_API_HASH=abcdef...   # yours from step (a)
export TG_ALLOW=                  # empty = reply to all DMs; or YOUR id to lock it down
python bot.py serve &             # brain in background (proactive ticker included)
```

Find your Telegram id: message [@userinfobot](https://t.me/userinfobot) once.

**c) Start the userbot (foreground first time):**

```bash
python bridges/telegram_userbot.py
# → enter phone (+234...) → enter the login code Telegram sends you
# → (if 2FA) enter your cloud password
# → "✓ logged in as @you" + "✓ userbot live"
```

Session saves to `~/.godquant/tg_userbot.session` — you log in **once**; keep
that file backed up and secret (it IS your login).

**d) Test it:**
- From another account (or any DM): send `hey babe` → it replies as you
- From YOUR account to yourself (Saved Messages): `.help` → owner commands
- Self-text: `.send me good morning ❤` → lands in Saved Messages
- Force a proactive pass: `.tick`
- Go silent 1h+ → it texts first (mood-driven opener)

**e) Keep it alive on Android:**
- `pkg install termux-wake-lock; termux-wake-lock` (or run `termux-wake-lock` once)
- Android Settings → Apps → Termux → Battery → **Unrestricted**
- Disable battery optimization / adaptive battery for Termux
- Optional auto-start on reboot: install **Termux:Boot** (F-Droid) with a
  `~/.termux/boot/start-bot.sh` that sources env and launches `serve` + userbot

## 7. Daily commands cheat-sheet

```bash
source ~/.godquant/env.sh
python bot.py serve &                          # brain
python bridges/telegram_userbot.py             # userbot (foreground, watch the logs)
# ── elsewhere ──
python bot.py partner "hey"                    # terminal chat
python bot.py send --channel telegram --to me --message "reminder ❤"
python bot.py tick                             # proactive pass now
python bot.py contacts                         # registry + outbox stats
python bot.py memory --search "..."            # what it remembers
python bot.py doctor                           # health: keys, chain, bridges
```

Open `http://127.0.0.1:5000` in your phone browser for the chat UI.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `429 / rate limit` | normal on free tiers — fallback chain covers it; check `llm_chain` in `doctor` |
| `HF model not found / 404` | that model isn't on Inference Providers; set another `llm_model` or leave default |
| `telethon ... FloodWaitError` | you sent too fast; wait it out, raise `TG_POLL` |
| `SESSION expired / AuthKey` | delete `~/.godquant/tg_userbot.session*`, re-run, log in again |
| `llama-server` OOM-killed | smaller model/quant, lower `-c 1024`, close other apps |
| Termux killed overnight | battery Unrestricted + `termux-wake-lock`; check `logcat` |
| Bot replies as you in groups | `TG_GROUPS=1` enables mention-only group replies (v3.2); default ignores groups |
| `PLAY STORE Termux` errors | reinstall from F-Droid — Play build is dead |

## 8.5 Updating while the bot is running

New version out? One command stops everything, pulls, and restarts:

```bash
cd ~/unrestrained-chat-bot
bash update.sh
```

Your Telegram login (`.session`), memory DB, and bond scores all survive —
they live outside git. If a process was started in another Termux session,
`update.sh` still finds and restarts it (logs: `brain.log`, `userbot.log`).
Manual way: `pkill -f "bot.py serve"; pkill -f telegram_userbot`, then
`git pull` and re-run both commands.

## 8.6 Cloning to other devices / accounts

Yes — clone the repo anywhere, each install is independent:

- **New device, same Telegram account:** fresh clone, own `TG_SESSION`
  (never copy `.session` files between devices — one login each). Memory and
  bonds are per-device (separate sqlite DBs); copy `~/.godquant/*.db` over
  manually if you want shared memory (stop the bot first).
- **⚠️ Don't run two live userbots on the SAME account** — both would reply
  to every message (double texts). One brain per account; other devices can
  run different accounts (different `TG_API_ID`/login) or just the brain.
- **Ports:** second brain on one machine → change `GQ_PORT`/server port and
  point that install's bridges at it (`GQ_SERVER=...`).
- **WhatsApp on Termux:** use the Baileys bridge (no browser needed):
  `node bridges/whatsapp_baileys.js` with `WA_PAIR_NUMBER` set.

## 8.7 Owner commands + task mode (self-chat)

Your Saved Messages (Telegram) or self-chat (WhatsApp, needs
`WA_OWNER_NUMBER` set to your digits) is a control room: `.persona 42 devon`
to change who's talking where, `.models`/`.model groq` to switch brains,
`.mission research X` for multi-agent tasks, `.code build a scraper` for
audited code, `.exec` for raw shell (**only with `GQ_ALLOW_EXEC=1`** — anyone
holding your unlocked phone could run commands, so think before enabling).

## 8.8 Alarms that find you (v3.8)

1. Open your self-chat: Telegram **Saved Messages**, WhatsApp chat with
   yourself, or the bot's DM on Discord.
2. Type `.remind in 2h call mom` (or `tomorrow 7:00`, `every day 8:00`,
   plain `19:30`). She confirms with `⏰ #1 ...`.
3. When it fires you get `⏰ call mom` right there — **unless** you're
   mid-conversation in a chat it relates to (you're chatting with Mom):
   then she weaves it into that chat naturally in her own voice, and your
   self-chat gets `✅ handled in Mom's chat` instead. Groups are never
   touched. `.snooze 1 in 30m` buys time; `.reminders` lists all.

## 9. Security notes (read once)

- `~/.godquant/env.sh` + `tg_userbot.session` = your keys + your Telegram login.
  Never screenshot, upload, or `git add` them (repo ignores them by default).
- `TG_ALLOW` locked to your own id = only you can chat it. Start locked.
- Free public APIs (Pollinations) are fine for roleplay, not for passwords/secrets.

Enjoy. 💕 `python bot.py setup` whenever you want to re-tune.
