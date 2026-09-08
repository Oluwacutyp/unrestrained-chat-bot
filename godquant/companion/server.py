"""Unified God Quant server — stdlib-only (no Flask needed).

One server replaces all three original Flask apps AND adds the quant engine.
API is backward compatible with whatsapp.js:
  POST /chat   {message, conversation_id, use_search, image, persona}
  POST /reset  {conversation_id}
  GET  /mood?conversation_id=
  GET  /status | /health
  POST /search    {query}
  POST /research  {query, type: text|news|wiki|fact_check|images}
  POST /mission   {goal}            → multi-agent run
  POST /backtest  {symbol, strategy, params?, optimize?, metric?}
  POST /risk      {equity, risk, entry, stop, ...}
  POST /send      {channel, to, message}  → queue outbound (bot texts first)
  GET  /outbox?channel=  → pending outbound for bridges (+ POST /ack {id,ok})
  GET|POST /contacts     → proactive-texting registry
  POST /tick             → run proactive pass now
  GET|POST /bond         → relationship level (get / owner-pin 0-3)
GET / serves the single-file chat UI (mobile-first, Termux-friendly).
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from godquant import __version__
from godquant.agents.base import AgentTask
from godquant.companion import web_search as WS

log = logging.getLogger("godquant.server")

UI_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>God Quant Companion</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:#0f1220;color:#e8eaf2}
header{padding:12px 16px;background:#1a1f3a;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0;flex:1}select,input[type=text]{background:#0f1220;color:#e8eaf2;border:1px solid #3a4170;border-radius:8px;padding:7px}
#mood{background:#2a2f55;border-radius:20px;padding:5px 12px;font-size:13px}
#msgs{max-width:720px;margin:0 auto;padding:16px 12px 120px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:85%;padding:10px 14px;border-radius:14px;white-space:pre-wrap;word-break:break-word;font-size:15px}
.me{align-self:flex-end;background:#4a5cff}.them{align-self:flex-start;background:#232842}
.tools{max-width:720px;margin:0 auto;padding:0 12px;display:flex;gap:6px;flex-wrap:wrap}
.tools button{background:#232842;color:#cfd3ff;border:1px solid #3a4170;border-radius:20px;padding:6px 12px;font-size:13px}
#bar{position:fixed;bottom:0;left:0;right:0;background:#1a1f3a;padding:10px;display:flex;gap:8px;max-width:720px;margin:0 auto}
#inp{flex:1;font-size:16px}#send{background:#4a5cff;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:15px}
</style></head><body>
<header><h1>◈ God Quant Companion</h1><span id="mood">neutral · 5</span>
<select id="persona"><option value="alex">Alex</option><option value="companion">Companion</option>
<option value="realistic">Realistic</option><option value="quant">Quant Buddy</option></select></header>
<div id="msgs"></div>
<div class="tools">
<button onclick="research('news')">📰 News</button><button onclick="research('wiki')">📚 Wiki</button>
<button onclick="research('fact_check')">🔍 Fact-check</button>
<button onclick="quick('backtest rsi_meanrev on BTCUSDT')">📈 Backtest</button>
<button onclick="ask('size 10000 equity, entry 100, stop 95')">🛡 Size</button>
<button onclick="reset()">🔄 Reset</button></div>
<div id="bar"><input id="inp" type="text" placeholder="Message…" autocomplete="off">
<button id="send" onclick="send()">Send</button></div>
<script>
const cid='web_'+Math.random().toString(36).slice(2,8);
function add(t,me){const d=document.createElement('div');d.className='msg '+(me?'me':'them');d.textContent=t;
document.getElementById('msgs').appendChild(d);window.scrollTo(0,document.body.scrollHeight);}
async function send(){const i=document.getElementById('inp');const m=i.value.trim();if(!m)return;i.value='';add(m,true);
const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({message:m,conversation_id:cid,persona:document.getElementById('persona').value})});
const d=await r.json();add(d.response||d.error,false);
if(d.mood)document.getElementById('mood').textContent=d.mood+' · '+d.mood_level;}
async function quick(m){document.getElementById('inp').value=m;send();}
async function ask(m){document.getElementById('inp').value=m;send();}
async function research(t){const q=prompt('Research:');if(!q)return;add('🔎 '+q,true);
const r=await fetch('/research',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({query:q,type:t})});const d=await r.json();add(d.results||d.error,false);}
async function reset(){await fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({conversation_id:cid})});document.getElementById('msgs').innerHTML='';
add('Fresh start ✨',false);}
document.getElementById('inp').addEventListener('keydown',e=>{if(e.key==='Enter')send();});
add('Heyy 😊 pick a persona up top and talk to me — or hit 📈 to run a real backtest.',false);
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    stack = None          # (cfg, router, memory, orch, companion)
    lock = threading.Lock()
    server_version = f"GodQuant/{__version__}"

    def log_message(self, *a):
        log.debug(*a)

    # ---- helpers ----
    def _json(self, obj: dict, code: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    # ---- routes ----
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        cid = q.get("conversation_id", ["default"])[0]
        cfg, _, _, _, companion = self.stack
        if parsed.path == "/":
            return self._html(UI_HTML)
        if parsed.path in ("/health", "/status"):
            return self._json({"status": "online", "version": __version__,
                               "provider": getattr(cfg, "llm_provider", "auto"),
                               "persona": cfg.persona})
        if parsed.path == "/mood":
            with self.lock:
                return self._json(companion.moods.snapshot(cid))
        if parsed.path == "/personas":
            from godquant.companion.personas import PERSONAS
            return self._json({k: v["blurb"] for k, v in PERSONAS.items()})
        if parsed.path == "/outbox":
            channel = q.get("channel", ["whatsapp"])[0]
            with self.lock:
                return self._json({"pending": self.outbox.pending(channel)})
        if parsed.path == "/contacts":
            with self.lock:
                return self._json({"contacts": self.outbox.list_contacts(),
                                   **self.outbox.stats()})
        if parsed.path == "/bond":
            channel = q.get("channel", ["telegram"])[0]
            chat_id = q.get("chat_id", [""])[0]
            if not chat_id:
                return self._json({"error": "need chat_id"}, 400)
            _, _, _, _, companion = self.stack
            with self.lock:
                return self._json({"bond": companion.bonds.get(channel, chat_id)})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        cfg, _, _, orch, companion = self.stack
        data = self._body()
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/chat":
                msg, cid = data.get("message", ""), data.get("conversation_id", "default")
                if not msg and not data.get("image"):
                    return self._json({"error": "No message"}, 400)
                with self.lock:
                    if data.get("channel") and data.get("chat_id"):
                        self.outbox.upsert_contact(data["channel"], str(data["chat_id"]),
                                                   data.get("display", ""),
                                                   touch_inbound=True)
                    out = companion.chat(
                        msg, cid,
                        persona=data.get("persona"),
                        use_search=data.get("use_search", False),
                        image_data=data.get("image"),
                        sender_name=data.get("sender_name"),
                        is_group=bool(data.get("is_group")),
                        bond_id=data.get("bond_id"))
                return self._json({**out, "conversation_id": cid})
            if path == "/send":
                if not data.get("message") or not data.get("to"):
                    return self._json({"error": "need {channel, to, message}"}, 400)
                with self.lock:
                    mid = self.outbox.enqueue(data.get("channel", "whatsapp"),
                                              str(data["to"]), data["message"])
                return self._json({"queued": mid})
            if path == "/ack":
                with self.lock:
                    self.outbox.ack(int(data.get("id", 0)),
                                    bool(data.get("ok", True)))
                return self._json({"ok": True})
            if path == "/bond":
                ch, chat_id = data.get("channel", "telegram"), data.get("chat_id", "")
                if not chat_id:
                    return self._json({"error": "need {chat_id, level}"}, 400)
                try:
                    n = int(data.get("level"))
                except (TypeError, ValueError):
                    return self._json({"error": "need level 0-3"}, 400)
                if n not in (0, 1, 2, 3):
                    return self._json({"error": "need level 0-3"}, 400)
                with self.lock:
                    bond = companion.bonds.set_level(ch, str(chat_id), n)
                return self._json({"bond": bond})
            if path == "/contacts":
                ch, chat_id = data.get("channel"), data.get("chat_id")
                if not ch or not chat_id:
                    return self._json({"error": "need {channel, chat_id}"}, 400)
                with self.lock:
                    if data.get("remove"):
                        self.outbox.remove(ch, str(chat_id))
                    else:
                        self.outbox.upsert_contact(ch, str(chat_id),
                                                   data.get("display", ""))
                        if "enabled" in data:
                            self.outbox.set_enabled(ch, str(chat_id),
                                                    bool(data["enabled"]))
                    return self._json({"contacts": self.outbox.list_contacts()})
            if path == "/tick":
                with self.lock:
                    queued = self.engine.tick()
                return self._json({"queued": queued})
            if path == "/reset":
                with self.lock:
                    return self._json(companion.reset(data.get("conversation_id", "default")))
            if path == "/search":
                if not data.get("query"):
                    return self._json({"error": "No query provided"}, 400)
                return self._json({"results": WS.advanced_search(data["query"], "text")})
            if path == "/research":
                if not data.get("query"):
                    return self._json({"error": "No query provided"}, 400)
                return self._json({"results": WS.advanced_search(
                    data["query"], data.get("type", "text"))})
            if path == "/mission":
                if not data.get("goal"):
                    return self._json({"error": "No goal provided"}, 400)
                with self.lock:
                    results = orch.run(data["goal"])
                return self._json({"results": [
                    {"agent": r.agent, "ok": r.ok, "output": r.output[:4000],
                     "score": r.score, "elapsed": round(r.elapsed, 2)}
                    for r in results]})
            if path == "/backtest":
                with self.lock:
                    res = orch.run_task("backtest", AgentTask(
                        f"backtest {data.get('strategy', 'sma_cross')}",
                        {"symbol": data.get("symbol", cfg.default_symbol),
                         "strategy": data.get("strategy", "sma_cross"),
                         "params": data.get("params") or {},
                         "optimize": bool(data.get("optimize", False)),
                         "metric": data.get("metric", "sharpe")}))
                return self._json({"output": res.output[:6000],
                                   "metrics": res.artifacts.get("metrics", {}),
                                   "params": res.artifacts.get("params", {})})
            if path == "/risk":
                with self.lock:
                    res = orch.run_task("risk", AgentTask(
                        "risk sizing",
                        {"equity": float(data.get("equity", cfg.initial_cash)),
                         "risk_pct": float(data.get("risk", cfg.max_risk_per_trade)),
                         "entry": float(data.get("entry", 100)),
                         "stop": float(data.get("stop", 95))}))
                return self._json({"output": res.output})
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            log.exception("server error on %s", path)
            return self._json({"error": str(e)[:500]}, 500)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def create_server(cfg, router, memory, orch, companion,
                  host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    from godquant.companion.outbox import Outbox, ProactiveEngine
    _Handler.stack = (cfg, router, memory, orch, companion)
    _Handler.outbox = Outbox(cfg.resolved_memory_db())
    _Handler.engine = ProactiveEngine(cfg, companion, _Handler.outbox)
    srv = ThreadingHTTPServer(
        (host or cfg.server_host,
         port if port is not None else cfg.server_port),
        _Handler)
    srv.daemon_threads = True
    return srv


def serve_forever(cfg, router, memory, orch, companion):
    import time as _time
    srv = create_server(cfg, router, memory, orch, companion)
    addr = f"http://{cfg.server_host}:{cfg.server_port}"
    print(f"\n◈ Unrestrained bot server v{__version__} → {addr}")
    print("  chat UI: /   api: /chat /mission /backtest /risk /research /mood")
    print("  messaging: /send /outbox /contacts /tick")
    print("  WhatsApp:  AI_SERVER_URL=" + addr + " node bridges/whatsapp.js")
    print("  Telegram:  python bridges/telegram_userbot.py  (own-account userbot)\n")
    if cfg.proactive_interval > 0:
        print(f"  proactive ticker: every {cfg.proactive_interval}s "
              f"(nudge after {cfg.nudge_after}s silence, "
              f"{cfg.max_nudges}/day, quiet '{cfg.quiet_hours or 'none'}')\n")

        def _ticker():
            while True:
                _time.sleep(cfg.proactive_interval)
                try:
                    with _Handler.lock:
                        queued = _Handler.engine.tick()
                    for m in queued:
                        print(f"  ✉ queued proactive → {m['channel']}:{m['to']}: "
                              f"{m['message'][:80]}")
                except Exception as e:
                    log.warning("ticker: %s", e)

        threading.Thread(target=_ticker, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n stopped.")
