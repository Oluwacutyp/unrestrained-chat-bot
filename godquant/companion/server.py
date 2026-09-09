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
  GET|POST /bond         → relationship score/friction (get / owner-pin 0-3/auto)
  GET  /memory?channel&chat_id → stored facts + bond dossier
  POST /translate {text, target?} → English ↔ Naija pidgin translation
  POST /warn {message, kind?, channel?} → self-DM alert to owner
  POST /import {channel, chat_id, messages[]} → ingest past chats
  GET|POST /persona        → per-chat persona overrides + global default
  GET  /models | POST /model {primary} → LLM chain info + runtime switch
  POST /forget {channel, chat_id, deep?} → wipe dossier (+bond)
  POST /code {task, run?}  → agent writes code to workspace (audited)
  POST /exec {cmd}         → shell (needs GQ_ALLOW_EXEC=1) ⚠️
  POST /remind {action,text,due_ts..} → reminders that fire via outbox
  POST /want {text..} + GET|POST /mind → intentions engine
  GET  /journal /brief + POST /dream /note /fetch → memory + briefing
  GET  /recall?q=&scope= | GET|POST /memories → unified memory ops
  GET|POST /missions → persistent projects (create/list/get/resume)
GET / serves the single-file chat UI (mobile-first, Termux-friendly).
"""
from __future__ import annotations

import json
import logging
import threading
import time
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
    _warn_last: dict = {}   # kind -> last ts (self-alert throttle)
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
        cfg, router, memory, _, companion = self.stack
        if parsed.path == "/":
            return self._html(UI_HTML)
        if parsed.path in ("/health", "/status"):
            return self._json({"status": "online", "version": __version__,
                               "provider": getattr(cfg, "llm_provider", "auto"),
                               "persona": cfg.persona, **memory.stats()})
        if parsed.path == "/models":
            with self.lock:
                info = {"primary": getattr(router, "primary", "?"),
                        "chain": router.chain() if hasattr(router, "chain") else [],
                        "usage": memory.stats()}
            return self._json(info)
        if parsed.path == "/persona":
            channel = q.get("channel", [""])[0]
            chat_id = q.get("chat_id", [""])[0]
            with self.lock:
                if channel and chat_id:
                    ov = companion.bonds.get_persona(channel, chat_id)
                    return self._json({"persona": ov or cfg.persona,
                                       "override": bool(ov)})
                ovs = companion.bonds.list_personas()
            return self._json({"default": cfg.persona,
                               "overrides": [f"{c}:{i}:{p}" for c, i, p in ovs]})
        if parsed.path == "/mind":
            with self.lock:
                try:
                    import json as _js
                    dream = _js.loads(
                        companion.bonds.kv_get("dream_report", "") or "{}")
                except ValueError:
                    dream = {}
                mind = {"intentions": companion.bonds.active_intentions(),
                        "reminders": companion.bonds.list_reminders(),
                        "dream": dream, "stats": memory.stats()}
            return self._json(mind)
        if parsed.path == "/journal":
            with self.lock:
                if q.get("recent", [""])[0]:
                    es = companion.bonds.recent_journal(
                        int(q.get("recent", ["10"])[0] or 10))
                    out = [{"channel": c, "chat_id": i, "day": d, "entry": e}
                           for c, i, d, e in es]
                else:
                    es = companion.bonds.get_journal(
                        q.get("channel", [""])[0], q.get("chat_id", [""])[0],
                        int(q.get("limit", ["7"])[0] or 7))
                    out = [{"day": d, "entry": e} for d, e in es]
            return self._json({"entries": out})
        if parsed.path == "/brief":
            from godquant.companion.dream import build_brief
            with self.lock:
                pend = self.engine.outbox.stats()["outbox"].get("pending", 0)
                return self._json({"brief": build_brief(companion.bonds, pend)})
        if parsed.path == "/recall":
            with self.lock:
                hits = self.mind.recall(
                    q.get("q", [""])[0], scope=q.get("scope", [""])[0] or None,
                    limit=int(q.get("limit", ["8"])[0] or 8))
            return self._json({"hits": hits})
        if parsed.path == "/memories":
            with self.lock:
                mems = self.mind.inspect(
                    scope=q.get("scope", [""])[0], layer=q.get("layer", [""])[0],
                    limit=int(q.get("limit", ["50"])[0] or 50))
            return self._json({"memories": mems})
        if parsed.path == "/missions":
            from godquant.agents.missions import MissionStore
            with self.lock:
                ms = MissionStore(cfg.resolved_memory_db())
                try:
                    out = ms.list(status=q.get("status", [""])[0])
                finally:
                    ms.close()
            return self._json({"missions": [
                {"id": m["id"], "goal": m["goal"][:200], "status": m["status"],
                 "steps": [{"agent": s2["agent"], "status": s2["status"]}
                           for s2 in m["steps"]]} for m in out]})
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
        if parsed.path == "/memory":
            channel = q.get("channel", ["telegram"])[0]
            chat_id = q.get("chat_id", [""])[0]
            if not chat_id:
                return self._json({"error": "need chat_id"}, 400)
            _, _, _, _, companion = self.stack
            with self.lock:
                facts = companion.bonds.get_facts(channel, chat_id)
                bond = companion.bonds.get(channel, chat_id)
            return self._json({"facts": [{"key": k, "value": v}
                                         for k, v in facts],
                               "bond": bond})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        cfg, router, _, orch, companion = self.stack
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
                    _p = data.get("persona") or None
                    if not _p and data.get("channel") and data.get("chat_id"):
                        _p = companion.bonds.get_persona(
                            data["channel"], str(data["chat_id"]))
                    out = companion.chat(
                        msg, cid,
                        persona=_p,
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
                lvl = data.get("level")
                if lvl is None or (isinstance(lvl, str) and
                                   lvl.lower() == "auto"):
                    n = None  # unpin → resume auto-pilot from live score
                else:
                    try:
                        n = int(lvl)
                    except (TypeError, ValueError):
                        return self._json({"error": "need level 0-3 or auto"},
                                          400)
                    if n not in (0, 1, 2, 3):
                        return self._json({"error": "need level 0-3 or auto"},
                                          400)
                with self.lock:
                    bond = companion.bonds.set_level(ch, str(chat_id), n)
                return self._json({"bond": bond})
            if path == "/translate":
                text = (data.get("text") or "").strip()
                if not text:
                    return self._json({"error": "need {text}"}, 400)
                target = (data.get("target") or "auto").lower()
                if "pidgin" in target:
                    goal = "Translate the text to fluent Nigerian Pidgin."
                elif "english" in target:
                    goal = "Translate the text to natural English."
                else:
                    goal = ("If the text is English, translate it to fluent "
                            "Nigerian Pidgin. If it is Nigerian Pidgin (or "
                            "mixed), translate it to natural English.")
                with self.lock:
                    t = router.complete(
                        "You are Devon's translator. Output ONLY the "
                        "translation, no quotes, no explanation.",
                        f"{goal}\n\nTEXT: {text}",
                        agent="companion").text.strip()
                return self._json({"translation": t})
            if path == "/model":
                want = (data.get("primary") or "").strip().lower()
                if not want:
                    return self._json({"error": "need {primary}"}, 400)
                known = {"openai", "groq", "anthropic", "gemini", "ollama",
                         "deepseek", "openrouter", "together", "huggingface",
                         "hf", "pollinations", "llamacpp", "heuristic"}
                if want not in known:
                    return self._json({"error": "unknown provider "
                                                f"'{want}' ({sorted(known)})"},
                                      400)
                with self.lock:
                    router.primary = want
                return self._json({"primary": want, "chain": router.chain()})
            if path == "/persona":
                from godquant.companion.personas import PERSONAS
                ch = data.get("channel")
                cid2 = data.get("chat_id")
                name = (data.get("persona") or "").strip().lower()
                if ch and cid2:
                    if name == "clear":
                        with self.lock:
                            companion.bonds.clear_persona(ch, str(cid2))
                        return self._json({"cleared": True})
                    if name not in PERSONAS:
                        return self._json({"error": "unknown persona"}, 400)
                    with self.lock:
                        companion.bonds.set_persona(ch, str(cid2), name)
                    return self._json({"persona": name, "override": True})
                if name not in PERSONAS:
                    return self._json({"error": "need {channel, chat_id} or valid global persona"}, 400)
                with self.lock:
                    cfg.persona = name
                return self._json({"default": name})
            if path == "/forget":
                ch, cid2 = data.get("channel"), data.get("chat_id")
                if not ch or not cid2:
                    return self._json({"error": "need {channel, chat_id}"}, 400)
                deep = bool(data.get("deep"))
                with self.lock:
                    facts = companion.bonds.get_facts(ch, str(cid2))
                    companion.bonds.clear_facts(ch, str(cid2))
                    if deep:
                        companion.bonds.zero_bond(ch, str(cid2))
                return self._json({"facts": len(facts), "deep": deep})
            if path == "/code":
                from godquant.dev.sandbox import audit, run_python
                task = (data.get("task") or "").strip()
                if not task:
                    return self._json({"error": "need {task}"}, 400)
                want_run = bool(data.get("run"))
                with self.lock:
                    code = router.complete(
                        "You are a senior Python dev. Output ONLY runnable Python code, "
                        "no markdown fences, no explanation. Keep it dependency-free.",
                        f"TASK: {task}", agent="developer").text.strip()
                for fence in ("```python", "```"):
                    code = code.replace(fence, "")
                code = code.strip()[:12000]
                flags = audit(code)
                slug = "".join(c if c.isalnum() else "_" for c in task[:30]).strip("_") or "task"
                path = cfg.resolved_workspace() / "code" / f"{slug}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(code)
                result = {"path": str(path), "audit": flags,
                          "summary": code[:1500]}
                if want_run and not flags:
                    with self.lock:
                        result["run"] = run_python(code, timeout=30)
                elif want_run:
                    result["run"] = {"error": f"blocked: {flags}"}
                return self._json(result)
            if path == "/exec":
                import subprocess as _sp
                if not cfg.allow_exec:
                    return self._json({"error": "disabled — set GQ_ALLOW_EXEC=1 and restart"}, 403)
                cmd = (data.get("cmd") or "").strip()
                if not cmd:
                    return self._json({"error": "need {cmd}"}, 400)
                try:
                    p = _sp.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=int(data.get("timeout", 30)),
                                cwd=str(cfg.resolved_workspace()))
                    out = (p.stdout + p.stderr)[-3000:]
                    return self._json({"exit": p.returncode, "output": out})
                except _sp.TimeoutExpired:
                    return self._json({"exit": -1, "output": "TIMEOUT"})
            if path == "/remind":
                b = companion.bonds
                act = (data.get("action") or "add").lower()
                if act == "add":
                    if not data.get("text") or not data.get("due_ts"):
                        return self._json({"error": "need {text, due_ts}"}, 400)
                    with self.lock:
                        rid = b.add_reminder(
                            data.get("channel", ""), str(data.get("chat_id", "")),
                            data["text"], float(data["due_ts"]),
                            data.get("repeat", ""))
                    return self._json({"id": rid, "due_ts": float(data["due_ts"])})
                if act == "list":
                    with self.lock:
                        rs = b.list_reminders()
                    ch, cid = data.get("channel"), data.get("chat_id")
                    if ch or cid:
                        rs = [r for r in rs
                              if (not ch or r["channel"] == ch)
                              and (not cid or r["chat_id"] == str(cid))]
                    return self._json({"reminders": rs})
                if act == "cancel":
                    with self.lock:
                        ok = b.cancel_reminder(int(data.get("id", 0)))
                    return self._json({"cancelled": ok})
                if act == "snooze":
                    with self.lock:
                        ok = b.snooze_reminder(int(data.get("id", 0)),
                                               float(data.get("due_ts", 0)))
                    return self._json({"snoozed": ok})
                return self._json({"error": "action: add|list|cancel"}, 400)
            if path == "/want":
                if not (data.get("text") or "").strip():
                    return self._json({"error": "need {text}"}, 400)
                with self.lock:
                    iid = companion.bonds.add_intention(
                        data["text"], data.get("kind", "custom"),
                        data.get("channel", ""), str(data.get("chat_id", "")),
                        data.get("display", ""))
                return self._json({"id": iid})
            if path == "/mind":
                if (data.get("action") or "") not in ("done", "drop"):
                    return self._json({"error": "action: done|drop"}, 400)
                with self.lock:
                    ok = companion.bonds.resolve_intention(
                        int(data.get("id", 0)), data["action"])
                return self._json({"ok": ok})
            if path == "/note":
                b = companion.bonds
                act = (data.get("action") or "get").lower()
                name = (data.get("name") or "").strip()
                if act == "save" and name and data.get("body") is not None:
                    with self.lock:
                        b.save_note(name, data["body"])
                    return self._json({"saved": name.lower()})
                if act == "get" and name:
                    with self.lock:
                        body = b.get_note(name)
                    return self._json({"body": body} if body is not None
                                      else {"error": "no such note"})
                if act == "list":
                    with self.lock:
                        return self._json({"notes": b.list_notes()})
                if act == "del" and name:
                    with self.lock:
                        return self._json({"deleted": b.del_note(name)})
                return self._json({"error": "action: save|get|list|del"}, 400)
            if path == "/dream":
                from godquant.companion.dream import run_dream
                with self.lock:
                    cmap = {f"{c['channel']}:{c['chat_id']}": c["display"]
                            for c in self.engine.outbox.list_contacts()
                            if c["display"]}
                    return self._json(run_dream(companion.bonds, cmap, mind=self.mind))
            if path == "/fetch":
                from godquant.tools.registry import run_tool
                r = run_tool("web_fetch",
                             {"url": (data.get("url") or "").strip()},
                             {"memory": self.mind.mem})
                if not r["ok"]:
                    code = 400 if "need http" in r["error"] else 502
                    return self._json({"error": r["error"]}, code)
                out, title, body = r["output"], "", ""
                if out.startswith("TITLE: "):
                    first, _, rest = out.partition("\n")
                    title = first[7:].strip()
                    body = rest[6:].strip() if rest.startswith("TEXT: ") else rest
                elif out.startswith("TEXT: "):
                    body = out[6:].strip()
                return self._json({"ok": True, "title": title, "text": body})
            if path == "/memories":
                act = (data.get("action") or "").lower()
                mid = int(data.get("id", 0))
                with self.lock:
                    if act == "pin":
                        return self._json({"ok": self.mind.pin(mid, True)})
                    if act == "unpin":
                        return self._json({"ok": self.mind.pin(mid, False)})
                    if act == "edit" and data.get("content"):
                        return self._json({"ok": self.mind.edit(
                            mid, data["content"])})
                    if act == "delete":
                        return self._json({"ok": self.mind.forget(mid)})
                return self._json(
                    {"error": "action: pin|unpin|edit|delete + id"}, 400)
            if path == "/missions":
                from godquant.agents.missions import MissionStore
                act = (data.get("action") or "list").lower()
                ms = MissionStore(cfg.resolved_memory_db())
                try:
                    if act == "list":
                        return self._json({"missions": [
                            {"id": m["id"], "goal": m["goal"][:200],
                             "status": m["status"]} for m in ms.list()]})
                    if act == "get":
                        m = ms.get(int(data.get("id", 0)))
                        return self._json({"mission": m} if m
                                          else {"error": "no such mission"})
                    if act in ("create", "resume"):
                        import threading as _th
                        a, g = act, (data.get("goal") or "")
                        rid, rto = int(data.get("id", 0)), data.get("report_to", "")

                        def _run():
                            try:
                                if a == "create":
                                    orch.run_mission(g, {"user": "owner"}, rto)
                                else:
                                    orch.resume_mission(rid)
                            except Exception as e:
                                log.warning("mission %s failed: %s", a, e)
                        _th.Thread(target=_run, daemon=True).start()
                        return self._json({"started": True, "action": act})
                finally:
                    ms.close()
                return self._json({"error": "action: list|get|create|resume"},
                                  400)
            if path == "/warn":
                msg = (data.get("message") or "").strip()[:500]
                if not msg:
                    return self._json({"error": "need {message}"}, 400)
                kind = str(data.get("kind") or "general")
                now = time.time()
                if now - self._warn_last.get(kind, 0) < 300:
                    return self._json({"queued": [], "throttled": True})
                self._warn_last[kind] = now
                channels = data.get("channel") or ["telegram", "whatsapp",
                                                   "discord"]
                if isinstance(channels, str):
                    channels = [channels]
                owners = {"telegram": cfg.owner_tg, "whatsapp": cfg.owner_wa,
                          "discord": cfg.owner_discord}
                queued = []
                with self.lock:
                    for ch in channels:
                        to = owners.get(ch)
                        if to:
                            queued.append(self.outbox.enqueue(
                                ch, str(to),
                                f"\u26a0\ufe0f bot alert [{kind}]\n{msg}"))
                return self._json({"queued": queued})
            if path == "/import":
                from godquant.companion.memory_engine import extract_facts
                ch = data.get("channel", "telegram")
                chat_id = str(data.get("chat_id", ""))
                items = (data.get("messages") or [])[:300]
                if not chat_id or not items:
                    return self._json({"error": "need {chat_id, messages[]}"},
                                      400)
                learned, n = [], 0
                prefix = {"telegram": "tg"}.get(ch, ch)
                cid = f"{prefix}:{chat_id}"
                with self.lock:
                    for it in items:
                        text = (it.get("text") or "").strip()
                        if not text:
                            continue
                        ts = it.get("ts") or time.time()
                        sender = str(it.get("sender") or chat_id)
                        if it.get("role", "user") == "user":
                            companion.bonds.note_message(ch, sender, text,
                                                         now=float(ts))
                            for k, v in extract_facts(text):
                                companion.bonds.add_fact(ch, sender, k, v)
                                learned.append(f"{k}={v}")
                        n += 1
                    for it in items[-60:]:  # recent slice as chat context
                        if (it.get("text") or "").strip():
                            companion._save_msg(
                                cid, it.get("role", "user") or "user",
                                it["text"][:1000])
                return self._json({"imported": n, "facts": learned[:50]})
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
    from godquant.memory.mind import Mind
    _Handler.mind = Mind(memory, companion.bonds)
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
