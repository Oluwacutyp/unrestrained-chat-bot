# ◈ God Quant AI Developer

**Production-grade, self-improving, multi-agent quant + AI Partner system — built Termux-first.**

Zero mandatory dependencies (pure Python stdlib). Runs fully **offline** out of the box;
add one free API key to unlock full LLM reasoning.

```
python gq.py mission "design and backtest an RSI mean-reversion strategy on BTCUSDT"
python gq.py partner "hey babe, backtest rsi_meanrev on BTCUSDT" --persona alex
python gq.py serve   # unified chat+quant server → phone browser / WhatsApp
```

## ⚡ 60-second start (Termux)

```bash
pkg install -y python git
git clone https://github.com/Oluwacutyp/unrestrained-chat-bot
cd unrestrained-chat-bot
bash install-termux.sh
# works with NO api key:
python gq.py --offline backtest --symbol BTCUSDT --strategy sma_cross
python gq.py --offline mission "optimize an RSI strategy and risk-gate it"
```

Optional (recommended): `pip install requests rich pytest numpy`

## 🔑 Full AI power (one step, free tier)

```bash
export GQ_API_KEY=gsk_...        # Groq — free, fast (recommended for phones)
# alternatives: OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY / DEEPSEEK_API_KEY
python gq.py mission "build me a Donchian breakout bot with risk controls"
```

Or persist config: `cp config.example.json ~/.godquant/config.json` and edit it.

Provider auto-detection: `GQ_PROVIDER=openai|groq|anthropic|gemini|ollama|deepseek|openrouter|heuristic`
Ollama (PC/server): `GQ_PROVIDER=ollama GQ_BASE_URL=http://<host>:11434`

## 🧠 Architecture

```
                ┌──────────────┐
  goal ────────▶│ ORCHESTRATOR │  plan → fan-out → verify → learn
                └──────┬───────┘
     ┌────────┬────────┼────────┬────────┬────────┐
     ▼        ▼        ▼        ▼        ▼        ▼
 researcher coder    quant  backtest   risk   reviewer
     │        │        │        │        │        │
     └────────┴────────┴───┬────┴────────┴────────┘
                          ▼
            ┌─────────────────────────┐
            │ MEMORY (SQLite)         │  lessons • facts • runs • costs
            │ SELF-IMPROVER (judge)   │  scores outputs, evolves prompts/params
            │ LLM ROUTER + fallbacks  │  8 providers + offline heuristic
            │ QUANT ENGINE            │  indicators • backtester • risk • data
            └─────────────────────────┘
```

**Agents** (`godquant/agents/`): planner/orchestrator + 6 specialists, parallel via threads.
**Self-improvement** (`godquant/self_improve/`): every output is judged; durable lessons
persist and are auto-injected into future prompts. Strategy params hill-climb across runs.
**Quant engine** (`godquant/quant/`): causal indicators, event-driven backtester (no lookahead),
5 strategies + grid optimizer, Kelly/VaR/heat risk gates, free data (Binance/Stooq) + cache.
**Memory** (`godquant/memory/`): dependency-free TF-search over SQLite — swap in embeddings later.

## 📟 Command map

| Command | What it does |
|---|---|
| `chat [msg]` | REPL with memory (`/lesson …` teaches it permanently) |
| `mission "goal"` | full multi-agent run: research→build→verify→risk→learn |
| `build "spec"` | generate code + automatic sandbox smoke-test |
| `backtest --symbol BTCUSDT --strategy sma_cross` | run a backtest |
| `optimize --symbol BTCUSDT --strategy rsi_meanrev --metric sharpe` | grid-search params |
| `risk --equity 10000 --risk 0.01 --entry 100 --stop 95` | sizing + APPROVE/VETO/HALT |
| `review --path .` | audit file/dir (score + verdict) |
| `strategies` | list built-in strategies |
| `memory --search "…" / --add "…" / --stats` | inspect & teach memory |
| `report` | self-improvement report (spend, lessons) |
| `test` | run test suite |
| `doctor` | environment diagnostics |
| `config --set key=value` | view/persist config |

Global flags: `--provider`, `--model`, `--offline`, `-v`.

## 📈 Examples

```bash
# backtest + optimize (offline OK)
python gq.py backtest --symbol ETHUSDT --strategy ema_macd
python gq.py optimize --symbol BTCUSDT --strategy donchian_trend --metric sortino
python gq.py backtest --symbol BTCUSDT --strategy sma_cross --params '{"fast":10,"slow":50}'

# stocks (auto-switches to Stooq, no key)
python gq.py backtest --symbol AAPL --strategy rsi_meanrev

# risk gate a live idea
python gq.py risk --equity 5000 --risk 0.01 --entry 67200 --stop 65800

# multi-agent build with review + learning
python gq.py mission "research momentum vs mean-reversion for BTC, backtest both, recommend one"

# audit your own code
python gq.py review --path workspace/
```

## 🔁 Self-improvement flywheel

1. Every mission output is **judged** (0–100) and distilled to **one durable lesson**.
2. Lessons persist in `~/.godquant/memory.db`, ranked by score.
3. Future prompts auto-include the most relevant lessons.
4. `memory --search` / `report` show what the system has learned; `/lesson` and
   `--add` let you inject your own doctrine.

## 💕 Partner fusion (your bots, evolved)

Your 6 original Partner sources were merged from `main`, reconstructed as runnable
code in `partner_original/` (Flask + llama.cpp + whatsapp-web.js, PC-oriented),
**and** re-engineered stdlib-only into `godquant/companion/`:

| Original | Fusion |
|---|---|
| Companion Backend persona | `personas: companion` |
| Alex identity + 12-mood engine | `personas: alex` + unified `MoodEngine` (now **persistent** across restarts) |
| Realistic bot mood decay + summaries | merged into `MoodEngine` + history summaries |
| Research features (news/wiki/fact-check) | `web_search.py` — **zero deps** (no `duckduckgo-search` needed), also grounds the ResearcherAgent |
| 3× Flask apps | one stdlib `server.py` — `/chat /research /mood /mission /backtest /risk` + mobile chat UI |
| `whatsapp.js` | `bridges/whatsapp.js` — same `/chat` contract + `!mission` mode, mood footers, env config |
| Dolphin GGUF via llama.cpp | `GQ_PROVIDER=llamacpp` router provider + `models` downloader (phone-size Qwen GGUFs too) |
| HF Spaces Gradio app | preserved in `partner_original/hf_app.py` |

```bash
# chat with Alex (persona + mood + quant tools) — offline OK
python gq.py partner "hey babe, backtest sma_cross on BTCUSDT" --persona alex
python gq.py partner --persona quant        # REPL as Quant Buddy

# serve to your phone browser + WhatsApp (replaces all 3 Flask apps)
python gq.py serve                           # → http://localhost:5000
AI_SERVER_URL=http://localhost:5000 WA_PERSONA=alex node bridges/whatsapp.js

# local GGUF brain instead of cloud (PC, or big-storage phones)
python gq.py models --download qwen2.5-0.5b-q4
GQ_PROVIDER=llamacpp python gq.py partner "hey"
```

Personas: `alex` (default) · `companion` · `realistic` · `quant`.
Set default: `python gq.py config --set persona=quant`.

## ⚠️ Honest limits

- Backtests are **not** profit promises. In-sample Sharpe lies; always demand
  out-of-sample + paper trading before capital.
- Heuristic (offline) mode gives real math but template narratives — add a key for reasoning.
- The sandbox guards against accidents, not adversaries. Don't run untrusted code.

## 🗂 Layout

```
gq.py  install-termux.sh  requirements.txt  config.example.json
godquant/
  config.py  llm/{providers,router,prompts}.py  memory/store.py
  agents/{base,orchestrator,specialists}.py     # 7 agents incl. companion
  quant/{indicators,data,backtest,strategies,risk}.py
  companion/{personas,mood,web_search,local_llm,companion,server}.py
  dev/{sandbox,patcher}.py  self_improve/evolver.py  ui/cli.py
partner_original/   # your 6 reconstructed sources (PC stack)
bridges/whatsapp.js # WhatsApp → God Quant server
tests/  workspace/
```

MIT — trade safe.
