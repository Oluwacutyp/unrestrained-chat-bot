"""Termux-friendly CLI. `python gq.py --help` for the full command map."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from godquant import __version__
from godquant.agents.base import AgentTask
from godquant.agents.orchestrator import Orchestrator
from godquant.agents.specialists import AGENTS
from godquant.config import load_config, save_config
from godquant.dev.sandbox import run_pytest
from godquant.llm.providers import detect_provider
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.quant.strategies import list_strategies
from godquant.self_improve.evolver import SelfImprover

try:
    from rich.console import Console  # type: ignore
    from rich.markdown import Markdown  # type: ignore
    _RICH = True
    _console = Console()
except Exception:
    _RICH = False
    _console = None


def say(text: str, style: str = ""):
    if _RICH and _console:
        _console.print(text)
    else:
        print(text)


def say_md(text: str):
    if _RICH and _console:
        _console.print(Markdown(text))
    else:
        print(text)


def build_ctx(cfg, memory) -> tuple:
    router = LLMRouter(cfg, memory)
    orch = Orchestrator(cfg, router, memory)
    return router, orch


# ---------------- commands ----------------
def cmd_chat(args, cfg, memory, router, orch):
    system = ("You are God Quant AI, a universe-class quant developer. "
              "Be precise, cite numbers, prefer stdlib Python.")
    if args.message:
        say_md(router.complete(system, args.message, agent="chat").text)
        return
    say("God Quant chat — type /exit to quit, /lesson <text> to teach me.\n")
    while True:
        try:
            msg = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg in ("/exit", "/quit"):
            break
        if msg.startswith("/lesson "):
            memory.add_lesson(msg[8:], tags="human", score=90)
            say("✓ lesson stored")
            continue
        lessons = memory.lesson_context(msg)
        say_md(router.complete(system + lessons, msg, agent="chat").text)


def cmd_mission(args, cfg, memory, router, orch):
    say(f"◈ mission: {args.goal}\n")
    results = orch.run(args.goal, improve=not args.no_learn)
    for r in results:
        say(f"\n━━━ [{r.agent}] {'OK' if r.ok else 'FAIL ' + r.error} ({r.elapsed:.1f}s) ━━━")
        say_md(r.output[:6000])


def cmd_build(args, cfg, memory, router, orch):
    res = orch.run_task("coder", AgentTask(args.spec))
    say_md(res.output[:8000])
    if res.artifacts.get("file"):
        say(f"\n✓ wrote {res.artifacts['file']}")


def cmd_backtest(args, cfg, memory, router, orch):
    params = json.loads(args.params) if args.params else {}
    res = orch.run_task("backtest", AgentTask(
        f"backtest {args.strategy} on {args.symbol}",
        {"symbol": args.symbol, "strategy": args.strategy,
         "params": params, "optimize": False}))
    say_md(res.output[:8000])


def cmd_optimize(args, cfg, memory, router, orch):
    res = orch.run_task("backtest", AgentTask(
        f"optimize {args.strategy} on {args.symbol} by {args.metric}",
        {"symbol": args.symbol, "strategy": args.strategy,
         "optimize": True, "metric": args.metric}))
    say_md(res.output[:8000])


def cmd_risk(args, cfg, memory, router, orch):
    res = orch.run_task("risk", AgentTask(
        f"size a trade: equity={args.equity} risk={args.risk} entry={args.entry} stop={args.stop}",
        {"equity": args.equity, "risk_pct": args.risk, "entry": args.entry,
         "stop": args.stop, "win_rate": args.win_rate,
         "avg_win": args.avg_win, "avg_loss": args.avg_loss}))
    say_md(res.output)


def cmd_review(args, cfg, memory, router, orch):
    p = Path(args.path)
    if p.is_file():
        content = p.read_text()[:6000]
    elif p.is_dir():
        chunks = []
        for f in sorted(p.rglob("*.py"))[:15]:
            chunks.append(f"### {f}\n{f.read_text()[:2500]}")
        content = "\n\n".join(chunks)[:9000]
    else:
        say(f"path not found: {p}")
        return
    res = orch.run_task("reviewer", AgentTask(f"Audit this code:\n\n{content}"))
    say_md(res.output)
    say(f"\nverdict: {res.artifacts.get('verdict')}  score: {res.score:.0f}")


def cmd_memory(args, cfg, memory, router, orch):
    if args.add:
        memory.add_lesson(args.add, tags="human", score=90)
        say("✓ lesson stored")
    elif args.search:
        for m in memory.search(args.search, limit=args.limit):
            say(f"[{m.id} {m.kind} {m.score:.0f}] {m.content[:300]}")
    elif args.stats:
        say(json.dumps(memory.stats(), indent=2))
    else:
        for m in memory.lessons(args.limit):
            say(f"[{m.score:.0f}] {m.content[:300]}")


def cmd_report(args, cfg, memory, router, orch):
    say(SelfImprover(cfg, router, memory).improvement_report())


def cmd_strategies(args, cfg, memory, router, orch):
    say(list_strategies())


def cmd_test(args, cfg, memory, router, orch):
    res = run_pytest(args.path)
    say(res["output"])
    say(f"\nexit={res['exit_code']} in {res['elapsed']:.1f}s")


def cmd_serve(args, cfg, memory, router, orch):
    from godquant.companion.companion import CompanionAgent
    from godquant.companion.server import serve_forever
    if args.port:
        cfg.server_port = args.port
    if args.host:
        cfg.server_host = args.host
    serve_forever(cfg, router, memory, orch, CompanionAgent(cfg, router, memory))


def cmd_partner(args, cfg, memory, router, orch):
    from godquant.companion.companion import CompanionAgent
    from godquant.companion.personas import list_personas
    companion = CompanionAgent(cfg, router, memory)
    persona = args.persona or cfg.persona
    cid = args.cid or "cli"
    if args.message:
        out = companion.chat(args.message, cid, persona=persona,
                             use_search=args.search)
        say(out["response"])
        say(f"\n[{out['persona']} · {out['mood']} {out['mood_level']}/10]")
        return
    say(f"Partner chat ({persona}) — /exit /reset /mood /persona <name> /personas\n")
    while True:
        try:
            msg = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not msg:
            continue
        if msg in ("/exit", "/quit"):
            break
        if msg == "/reset":
            companion.reset(cid)
            say("(fresh start ✨)")
            continue
        if msg == "/mood":
            say(str(companion.moods.snapshot(cid)))
            continue
        if msg == "/personas":
            say(list_personas())
            continue
        if msg.startswith("/persona "):
            persona = msg.split(None, 1)[1]
            say(f"(persona → {persona})")
            continue
        out = companion.chat(msg, cid, persona=persona)
        say(f"\n{out['persona']} [{out['mood']} {out['mood_level']}/10] › {out['response']}\n")


def cmd_export_training(args, cfg, memory, router, orch):
    from godquant.train.collector import TrajectoryLogger
    from godquant.train.export import build_packs
    ws = cfg.resolved_workspace()
    if args.status:
        st = TrajectoryLogger(ws).stats()
        say(f"trajectories={st['trajectories']} prefs={st['prefs']} "
            f"pairs={st['pairs']} bytes={st['bytes']}")
        return
    r = build_packs(ws)
    say(f"✓ sft={r['sft']} dpo={r['dpo']} skipped={r['skipped']}\n{r['dir']}")
    if args.push:
        from godquant.train.hf_pipe import push_files
        import os as _os
        files = {str(ws / "train" / f): f for f in r["files"]
                 if (ws / "train" / f).exists()}
        out = push_files(args.push, files, _os.environ.get("HF_TOKEN", ""))
        say(f"push → {out}")


def cmd_models(args, cfg, memory, router, orch):
    from godquant.companion import local_llm as LL
    if args.download:
        path = LL.download_model(args.download)
        say(f"✓ {path}\nSet it: python gq.py config --set model_path={path}")
        say("Use it: GQ_PROVIDER=llamacpp python gq.py partner \"hey\"")
    else:
        say(LL.list_models())


def cmd_send(args, cfg, memory, router, orch):
    from godquant.companion.outbox import Outbox
    ob = Outbox(cfg.resolved_memory_db())
    try:
        mid = ob.enqueue(args.channel, args.to, args.message)
        say(f"✓ queued #{mid} → {args.channel}:{args.to} "
            f"(delivered on next bridge poll)")
    finally:
        ob.close()


def cmd_tick(args, cfg, memory, router, orch):
    from godquant.companion.companion import CompanionAgent
    from godquant.companion.outbox import Outbox, ProactiveEngine
    ob = Outbox(cfg.resolved_memory_db())
    try:
        queued = ProactiveEngine(cfg, CompanionAgent(cfg, router, memory), ob).tick()
        if not queued:
            say("nothing due 😴 (no silent contacts / quiet hours / limits)")
        for m in queued:
            say(f"✉ #{m['id']} → {m['channel']}:{m['to']}: {m['message'][:100]}")
    finally:
        ob.close()


def cmd_contacts(args, cfg, memory, router, orch):
    from godquant.companion.outbox import Outbox
    ob = Outbox(cfg.resolved_memory_db())
    try:
        if args.add:
            ch, _, rest = args.add.partition(":")
            chat_id, _, display = rest.partition(":")
            ob.upsert_contact(ch or "whatsapp", chat_id, display)
            say(f"✓ registered {ch}:{chat_id}")
        elif args.remove:
            ch, _, chat_id = args.remove.partition(":")
            ob.remove(ch, chat_id)
            say("✓ removed")
        elif args.enable:
            ch, _, chat_id = args.enable.partition(":")
            ob.set_enabled(ch, chat_id, True)
            say("✓ proactive ON")
        elif args.disable:
            ch, _, chat_id = args.disable.partition(":")
            ob.set_enabled(ch, chat_id, False)
            say("✓ proactive OFF")
        else:
            for c in ob.list_contacts():
                bell = "🔔" if c["enabled"] else "🔕"
                say(f"{bell} {c['channel']}:{c['chat_id']} ({c['display']}) "
                    f"nudges={c['nudges_today']} silent={c['last_inbound_ago']}s")
            say(str(ob.stats()))
    finally:
        ob.close()


def cmd_setup(args, cfg, memory, router, orch):
    """Guided first-run wizard. Secrets → ~/.godquant/env.sh (chmod 600),
    settings → ~/.godquant/config.json. Flags make it non-interactive."""
    import os
    from godquant.companion.personas import list_personas
    from godquant.config import base_dir, save_config
    from godquant.llm.router import LLMRouter

    def ask(prompt, default=""):
        if args.yes:
            return default
        try:
            v = input(f"{prompt} [{default or 'skip'}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        return v or default

    say("◈ Unrestrained Bot Setup — Enter skips any step.\n")
    # ---- 1. brains (multi-provider) ----
    say("── 1/4 brains (free tier keys; Pollinations needs NO key) ──")
    groq = args.groq or ask("Groq key (console.groq.com, gsk_...)",
                            os.environ.get("GROQ_API_KEY", ""))
    hf = args.hf or ask("HuggingFace token (hf.co/settings/tokens)",
                        os.environ.get("HF_TOKEN", ""))
    provider = args.provider_name or ask("primary provider", cfg.llm_provider)
    fallbacks = args.fallbacks or ask("fallbacks, comma-sep",
                                      ",".join(cfg.llm_fallbacks) or "pollinations")
    cfg.llm_provider = provider or "auto"
    cfg.llm_fallbacks = [x.strip().lower() for x in fallbacks.split(",") if x.strip()]
    if args.model:
        cfg.llm_model = args.model
    # test the chain live
    say("\ntesting chain...", )
    import copy as _copy
    test_cfg = _copy.copy(cfg)
    if groq:
        os.environ["GROQ_API_KEY"] = groq
    if hf:
        os.environ["HF_TOKEN"] = hf
    test_cfg.llm_timeout = 25
    try:
        resp = LLMRouter(test_cfg, None).complete("Reply with exactly: OK", "ping",
                                                  agent="setup")
        say(f"  ✓ answered by {resp.provider} ({resp.model})")
        if resp.provider == "heuristic":
            say("  ⚠ no cloud key worked — bot runs offline until you add one.")
    except Exception as e:
        say(f"  ⚠ test failed: {e}")
    # ---- 2. persona ----
    say("\n── 2/4 persona ──\n" + list_personas())
    cfg.persona = args.persona or ask("persona", cfg.persona)
    # ---- 3. proactive texting ----
    say("\n── 3/4 proactive texting ──")
    try:
        cfg.nudge_after = int(args.nudge_after or ask("text-first after N sec silence",
                                                      str(cfg.nudge_after)))
    except ValueError:
        pass
    cfg.quiet_hours = args.quiet if args.quiet is not None else ask(
        "quiet hours (e.g. 1-6, empty = none)", cfg.quiet_hours)
    # ---- 4. telegram userbot ----
    say("\n── 4/4 telegram userbot (your own account) ──")
    tg_id = args.tg_id or ask("TG_API_ID (my.telegram.org/apps)",
                              os.environ.get("TG_API_ID", ""))
    tg_hash = args.tg_hash or ask("TG_API_HASH", os.environ.get("TG_API_HASH", ""))
    tg_allow = args.tg_allow or ask("TG_ALLOW (csv ids, empty = all DMs)",
                                    os.environ.get("TG_ALLOW", ""))
    # ---- persist ----
    env_path = base_dir() / "env.sh"
    lines = ["# generated by `bot.py setup` — source this before running bridges"]
    if groq:
        lines.append(f"export GROQ_API_KEY='{groq}'")
    if hf:
        lines.append(f"export HF_TOKEN='{hf}'")
    if tg_id:
        lines.append(f"export TG_API_ID='{tg_id}'")
    if tg_hash:
        lines.append(f"export TG_API_HASH='{tg_hash}'")
    if tg_allow:
        lines.append(f"export TG_ALLOW='{tg_allow}'")
    lines.append("export GQ_SERVER='http://127.0.0.1:5000'")
    env_path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(env_path, 0o600)
    except Exception:
        pass
    save_config(cfg)
    say(f"\n✓ settings → {base_dir() / 'config.json'}")
    say(f"✓ secrets  → {env_path} (chmod 600, never share)")
    say("\nnext commands (run in this order):")
    say(f"  source {env_path}")
    say("  python bot.py serve &")
    say("  python bridges/telegram_userbot.py   # first run: phone + login code")
    say("  (new session)  python bot.py partner \"hey babe\"   # talk from terminal")


def cmd_doctor(args, cfg, memory, router, orch):
    import sqlite3
    checks = []
    checks.append(("python", sys.version.split()[0]))
    checks.append(("provider", f"{detect_provider(cfg)} (model: {router.primary and cfg.llm_model or 'default'})"))
    try:
        import requests  # type: ignore
        checks.append(("requests", requests.__version__))
    except Exception:
        checks.append(("requests", "missing (ok — urllib fallback)"))
    try:
        import numpy  # type: ignore
        checks.append(("numpy", numpy.__version__))
    except Exception:
        checks.append(("numpy", "missing (ok — pure-python mode)"))
    try:
        import yfinance  # type: ignore
        checks.append(("yfinance", "installed"))
    except Exception:
        checks.append(("yfinance", "missing (optional)"))
    checks.append(("rich", "installed" if _RICH else "missing (optional)"))
    checks.append(("sqlite", sqlite3.sqlite_version))
    checks.append(("memory_db", str(cfg.resolved_memory_db())))
    checks.append(("workspace", str(cfg.resolved_workspace())))
    checks.append(("agents", ", ".join(sorted(AGENTS))))
    checks.append(("persona", cfg.persona))
    import shutil as _shutil
    if _shutil.which("llama-server"):
        checks.append(("llama-cpp", "binary ready (`llama-server`, Termux pkg)"))
    else:
        try:
            import llama_cpp  # type: ignore
            checks.append(("llama-cpp", "python pkg ready (local GGUF)"))
        except Exception:
            checks.append(("llama-cpp", "missing (optional; cloud LLMs ok)"))
    from godquant.companion.local_llm import MODELS_DIR
    ggufs = list(MODELS_DIR.glob("*.gguf")) if MODELS_DIR.exists() else []
    checks.append(("gguf_models", str(len(ggufs))))
    try:
        import telethon  # type: ignore
        checks.append(("telethon", telethon.__version__ + " (TG userbot ready)"))
    except Exception:
        checks.append(("telethon", "missing (pip install telethon for TG bridge)"))
    import shutil
    checks.append(("node", shutil.which("node") and "installed (WA bridge ready)"
                  or "missing (WA bridge needs Node)"))
    checks.append(("proactive", f"every {cfg.proactive_interval}s, nudge after "
                                f"{cfg.nudge_after}s, quiet '{cfg.quiet_hours or 'none'}'"))
    import os as _os
    keys = []
    for label, var in (("groq", "GROQ_API_KEY"), ("hf", "HF_TOKEN"),
                       ("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
                       ("gemini", "GEMINI_API_KEY"), ("tg", "TG_API_ID")):
        keys.append(f"{label}:{'✓' if _os.environ.get(var) else '✗'}")
    checks.append(("keys", " ".join(keys)))
    checks.append(("llm_chain", " → ".join(router.chain())))
    say("God Quant Doctor\n")
    for k, v in checks:
        say(f"  {k:<12} {v}")


def cmd_config(args, cfg, memory, router, orch):
    if args.set:
        from godquant.config import _coerce
        k, _, v = args.set.partition("=")
        if hasattr(cfg, k):
            setattr(cfg, k, _coerce(k, v))  # coerce so lists/ints save typed
            save_config(cfg)
            say(f"✓ {k} = {getattr(cfg, k)}")
        else:
            say(f"unknown key: {k}")
    else:
        import dataclasses
        say(json.dumps(dataclasses.asdict(cfg), indent=2))


# ---------------- parser ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gq", description=f"GodQuant v{__version__} by oluwacutyp / peacethefirst1 — personal AI, memory-first")
    p.add_argument("--provider", help="llm provider (auto|openai|groq|huggingface|pollinations|anthropic|gemini|deepseek|openrouter|ollama|llamacpp|heuristic)")
    p.add_argument("--model", help="model override")
    p.add_argument("--offline", action="store_true", help="force offline mode (no network/keys)")
    p.add_argument("--verbose", "-v", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chat", help="chat / REPL with memory")
    c.add_argument("message", nargs="?", help="single message (omit for REPL)")

    m = sub.add_parser("mission", help="run a multi-agent mission (plan→execute→verify→learn)")
    m.add_argument("goal")
    m.add_argument("--no-learn", action="store_true")

    b = sub.add_parser("build", help="generate code with sandbox smoke-test")
    b.add_argument("spec")

    t = sub.add_parser("backtest", help="backtest a strategy")
    t.add_argument("--symbol", default=None)
    t.add_argument("--strategy", default="sma_cross")
    t.add_argument("--params", default=None, help='JSON e.g. \'{"fast":10,"slow":50}\'')

    o = sub.add_parser("optimize", help="grid-search strategy params")
    o.add_argument("--symbol", default=None)
    o.add_argument("--strategy", default="sma_cross")
    o.add_argument("--metric", default="sharpe")

    r = sub.add_parser("risk", help="position sizing + risk verdict")
    r.add_argument("--equity", type=float, default=10000)
    r.add_argument("--risk", type=float, default=0.01)
    r.add_argument("--entry", type=float, default=100)
    r.add_argument("--stop", type=float, default=95)
    r.add_argument("--win-rate", type=float, default=0.5)
    r.add_argument("--avg-win", type=float, default=1.5)
    r.add_argument("--avg-loss", type=float, default=1.0)

    v = sub.add_parser("review", help="audit code (file or directory)")
    v.add_argument("--path", default=".")

    mm = sub.add_parser("memory", help="inspect/teach long-term memory")
    mm.add_argument("--add", default=None)
    mm.add_argument("--search", default=None)
    mm.add_argument("--stats", action="store_true")
    mm.add_argument("--limit", type=int, default=10)

    sub.add_parser("report", help="self-improvement report")
    sub.add_parser("strategies", help="list built-in strategies")
    tt = sub.add_parser("test", help="run test suite")
    tt.add_argument("--path", default="tests")
    sub.add_parser("doctor", help="environment diagnostics")
    cc = sub.add_parser("config", help="view/set config")
    cc.add_argument("--set", default=None, help="key=value")

    s = sub.add_parser("serve", help="start unified chat+quant server (WhatsApp-ready)")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)

    pc = sub.add_parser("partner", help="chat with the AI partner (persona+mood+tools)")
    pc.add_argument("message", nargs="?", help="single message (omit for REPL)")
    pc.add_argument("--persona", default=None, help="devon|alex|companion|realistic|quant")
    pc.add_argument("--cid", default="cli")
    pc.add_argument("--search", action="store_true")

    ex = sub.add_parser("export-training", help="training packs from collected data")
    ex.add_argument("--status", action="store_true", help="show collection stats")
    ex.add_argument("--push", default=None, help="push packs to user/repo (needs HF_TOKEN)")
    md = sub.add_parser("models", help="list/download local GGUF models")
    md.add_argument("--download", default=None)

    sd = sub.add_parser("send", help="queue an outbound message (bot texts first)")
    sd.add_argument("--channel", default="whatsapp")
    sd.add_argument("--to", required=True)
    sd.add_argument("--message", required=True)

    sub.add_parser("tick", help="run one proactive-texting pass now")

    ct = sub.add_parser("contacts", help="proactive-texting registry")
    ct.add_argument("--add", default=None, help="channel:chat_id[:display]")
    ct.add_argument("--remove", default=None, help="channel:chat_id")
    ct.add_argument("--enable", default=None, help="channel:chat_id")
    ct.add_argument("--disable", default=None, help="channel:chat_id")

    su = sub.add_parser("setup", help="guided first-run wizard (keys, persona, TG)")
    su.add_argument("--yes", action="store_true", help="non-interactive: flags only")
    su.add_argument("--groq", default=None)
    su.add_argument("--hf", default=None)
    su.add_argument("--provider-name", default=None)
    su.add_argument("--fallbacks", default=None)
    su.add_argument("--persona", default=None)
    su.add_argument("--tg-id", default=None)
    su.add_argument("--tg-hash", default=None)
    su.add_argument("--tg-allow", default=None)
    su.add_argument("--nudge-after", default=None)
    su.add_argument("--quiet", default=None)
    return p


_HANDLERS = {"chat": cmd_chat, "mission": cmd_mission, "build": cmd_build,
             "backtest": cmd_backtest, "optimize": cmd_optimize, "risk": cmd_risk,
             "review": cmd_review, "memory": cmd_memory, "report": cmd_report,
             "strategies": cmd_strategies, "test": cmd_test,
             "doctor": cmd_doctor, "config": cmd_config,
             "serve": cmd_serve, "partner": cmd_partner, "models": cmd_models,
             "send": cmd_send, "tick": cmd_tick, "contacts": cmd_contacts,
             "setup": cmd_setup, "export-training": cmd_export_training}


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    overrides = {}
    if getattr(args, "provider", None):
        overrides["llm_provider"] = args.provider
    if getattr(args, "model", None):
        overrides["llm_model"] = args.model
    if getattr(args, "offline", False):
        overrides["offline"] = True
    cfg = load_config(overrides)
    if getattr(args, "symbol", None) is None and hasattr(args, "symbol"):
        args.symbol = cfg.default_symbol
    memory = MemoryStore(cfg.resolved_memory_db())
    try:
        router, orch = build_ctx(cfg, memory)
        _HANDLERS[args.cmd](args, cfg, memory, router, orch)
    finally:
        memory.close()
