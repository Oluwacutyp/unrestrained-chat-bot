# ◈ God Quant AI Developer

**Production-grade, self-improving, multi-agent quant + code system — built Termux-first.**

Zero mandatory dependencies (pure Python stdlib). Runs fully **offline** out of the box;
add one free API key to unlock full LLM reasoning.

```
python gq.py mission "design and backtest an RSI mean-reversion strategy on BTCUSDT"
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

## 🧩 Bringing your existing AI Partner code

Drop your current codebase anywhere (e.g. `partner/`) and run:

```bash
python gq.py review --path partner/     # audit it
python gq.py mission "integrate partner/ into godquant as a new agent, keep tests green"
```

The coder + reviewer agents will refactor, wire, and regression-test it.

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
  agents/{base,orchestrator,specialists}.py
  quant/{indicators,data,backtest,strategies,risk}.py
  dev/{sandbox,patcher}.py  self_improve/evolver.py  ui/cli.py
tests/  workspace/
```

MIT — trade safe.
