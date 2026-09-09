# ◈ Unrestrained Chat Bot

**Your AI partner that texts first — on WhatsApp and from your own Telegram account.**

No filters, no cloud lock-in, Termux-first. Personas with real moods, persistent
memory, live web research — and an autonomous messaging engine so the bot can
**text you first, follow up when you're silent, and even text itself**.
A quant/backtesting module ships along for fun, but this project is a chat bot.

```
python bot.py partner "hey babe, what did you find on the news today?" --persona alex
python bot.py serve            # brain → phone browser + WhatsApp + Telegram
python bot.py tick             # force one proactive-texting pass
```

## ⚡ 60-second start (Termux)

```bash
pkg install -y python git
git clone https://github.com/Oluwacutyp/unrestrained-chat-bot
cd unrestrained-chat-bot
bash install-termux.sh
python bot.py --offline partner "hey babe i miss you" --persona alex
```

Zero mandatory pip packages — works with no API key (offline voice + real tools).
**Phone-only? Start here:** full step-by-step → [`docs/TERMUX_GUIDE.md`](docs/TERMUX_GUIDE.md)
— or just run the wizard: `python bot.py setup` (keys, persona, Telegram, live test).

Brains (free tiers, auto-chained with fallback — if one dies, the next answers):

| Provider | Key | Uncensored path |
|---|---|---|
| `groq` (default pick) | `GROQ_API_KEY` (`gsk_...`, free, no card) | great roleplay via system prompt |
| `huggingface` | `HF_TOKEN` (free, needs "Inference Providers" perm) | any HF model via `GQ_MODEL`, incl. community uncensored |
| `pollinations` | **none** — works out of the box | free `/models` list, try several |
| local GGUF | none (after download) | `pkg install llama-cpp` + Dolphin/etc → `GQ_BASE_URL=http://127.0.0.1:8080/v1` |
| `openai/anthropic/gemini/deepseek/openrouter/ollama/llamacpp` | their keys | as usual |

```bash
export GQ_FALLBACKS=huggingface,pollinations   # groq → hf → pollinations → offline
python bot.py doctor                            # see keys + live llm_chain
```

## ✉️ Messaging: WhatsApp + Telegram (own account)

One brain, two bridges — both **reply and deliver**, so the bot texts first:

| Bridge | Replies | Texts first | Runs on |
|---|---|---|---|
| `bridges/whatsapp.js` (Node, QR login) | ✅ DMs + groups | ✅ via outbox poll | **PC** (needs Chrome) |
| `bridges/telegram_userbot.py` (MTProto, **your own account**) | ✅ DMs + groups | ✅ via outbox poll | **Termux ✅** |

v3.3 social brain: replies are **clean** (no mood footer), with read pauses,
mood-paced typing, multi-bubble splits, rare typos + `*corrections`, and
distracted pauses. Relationships are a **0-100 score** from substance ×
warmth — spam earns ~nothing, insults build friction, silence decays,
streaks deepen (`.bond <chat>` to view/pin). Mention-gated group replies
(`TG_GROUPS=1` / `WA_GROUP_OPEN=1`) and anti-ban pacing
(`HUMAN_MAXPM`/`HUMAN_GAP`, `TG_HUMANIZE=0` for instant sends).
Update a live bot with `bash update.sh`.

```bash
# Terminal 1 — the brain (with proactive ticker every 5 min)
python bot.py serve

# Terminal 2a — Telegram as YOU (not a bot account)
pip install telethon             # once
export TG_API_ID=... TG_API_HASH=...   # https://my.telegram.org/apps
python bridges/telegram_userbot.py     # first run: phone + login code

# Terminal 2b — WhatsApp (PC only — Termux has no Chrome)
npm install whatsapp-web.js qrcode-terminal axios
AI_SERVER_URL=http://localhost:5000 WA_PERSONA=alex node bridges/whatsapp.js
```

Full guide (allow-lists, owner commands, Saved-Messages self-texting, Termux
keep-alive): [`bridges/README.md`](bridges/README.md).

**Text itself, literally:** `.send me good morning ❤` (Telegram) or
`POST /send {"channel":"whatsapp","to":"234...@c.us"}` lands in your own chat —
great for reminders, journals, and bot-to-self loops.

## 🧠 How it works

```
  you ──WhatsApp/Telegram──▶  serve ──▶ companion (persona + mood + memory)
                                         │  ├─ tools: web search, quant
  you ◀── bridges poll ◀── outbox ◀──────┘  └─ proactive ticker: silence watcher
         (bot texts first)       ▲              texts you when you're quiet
```

- **Personas** (`alex` default · `companion` · `realistic` · `quant`) — faithful
  evolutions of the original Partner bots, rendered per-message with live mood.
- **Mood engine** — 12 moods, triggers, jealousy, 30-min decay, mood-aware
  sampling; persists across restarts (the originals forgot everything).
- **Memory** — SQLite: conversations, lessons, costs. Self-improvement loop
  judges outputs and injects lessons into future replies.
- **7 agents** — researcher (live web-grounded), coder, quant, backtest, risk,
  reviewer, companion — via `mission` (also over chat: `!mission ...`).
- **Uncensored path** — any OpenAI-compatible endpoint, Ollama, or local GGUF;
  persona + model are both your choice.

## 📟 Command map

| Command | What it does |
|---|---|
| `partner [msg] --persona alex` | chat with the partner (REPL or one-shot) |
| `serve [--port 5000]` | brain server: chat UI + API + proactive ticker |
| `send --channel telegram --to me --message "..."` | queue outbound (bot texts first) |
| `tick` | run one proactive pass now |
| `contacts [--add ch:id:name]` | registry: `--enable/--disable/--remove/list` |
| `chat [msg]` | plain assistant chat with memory |
| `mission "goal"` | multi-agent run (research→build→verify→learn) |
| `backtest/optimize/risk/review` | optional quant module + code audit |
| `models [--download key]` | local GGUF manager |
| `memory/report/strategies/test/doctor/config` | introspection & setup |

Global flags: `--provider --model --offline -v`. `bot.py` and `gq.py` are aliases.

## 🧩 Project layout

```
bot.py  gq.py  install-termux.sh  requirements.txt
godquant/
  companion/{personas,mood,web_search,local_llm,companion,outbox,server}.py
  agents/{base,orchestrator,specialists}.py   # 7 agents incl. companion
  quant/{indicators,data,backtest,strategies,risk}.py  # optional module
  llm/{providers,router,prompts}.py  memory/store.py
  dev/{sandbox,patcher}.py  self_improve/evolver.py  ui/cli.py
bridges/{telegram_userbot.py,whatsapp.js}     # messaging bridges
partner_original/    # reconstructed pre-fusion sources (PC stack)
tests/  workspace/
```

## ⚠️ Honest limits

- The WhatsApp bridge needs Chrome → **PC only**. On Termux, Telegram userbot
  is the full experience.
- Your `.session` file **is** your Telegram login — back it up, never share it.
- Start with `TG_ALLOW` / `ALLOWED_NUMBERS` locked to yourself; open up later.
- Proactive texting defaults to ≤3 nudges/day/contact with quiet-hours support.
- Uncensored ≠ consequence-free: you're responsible for what your bot sends.

MIT — text responsibly. 💕
