"""v3.5 god-tier drop: LLM extraction, learning, import, warn, discord, baileys."""
import asyncio
import importlib.util
import json
import tempfile
import threading
import time
import types
import urllib.request
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.learner import extract_facts_llm, style_lessons
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore


def _stack(**over):
    tmp = Path(tempfile.mkdtemp())
    kw = dict(offline=True, workspace=str(tmp / "ws"),
              memory_db=str(tmp / "mem.db"), max_workers=2)
    kw.update(over)
    cfg = GodQuantConfig(**kw)
    mem = MemoryStore(cfg.resolved_memory_db())
    return cfg, mem


class FakeRouter:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, system, user, agent=None):
        self.calls += 1
        text = self.script.pop(0) if self.script else "ok"
        return types.SimpleNamespace(text=text)


def _serve(cfg, mem):
    router = LLMRouter(cfg, mem)
    agent = CompanionAgent(cfg, router, mem)
    srv = create_server(cfg, router, mem, Orchestrator(cfg, router, mem),
                        agent, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(base, path, payload):
    import urllib.error
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


# ---- LLM extraction ----
def test_llm_extract_parses_and_validates():
    r = FakeRouter(['[{"key":"job","value":"nurse"},'
                    '{"key":"zzz","value":"no"},{"key":"note","value":"x"}]'])
    assert extract_facts_llm(r, "I'm a nurse btw") == [("job", "nurse"),
                                                       ("note", "x")]
    assert extract_facts_llm(FakeRouter(["not json"]), "hi") == []
    assert extract_facts_llm(FakeRouter(["[]"]), "hi") == []
    assert extract_facts_llm(FakeRouter(['[{"key":"name","value":"Chidi"}]']),
                             "I'm Chidi btw") == [("name", "Chidi")]


def test_style_lessons():
    short = [{"role": "user", "content": "lol ok"}] * 7
    assert any("short rapid-fire" in v for _, v in style_lessons(short))
    qs = [{"role": "user", "content": "why is this? " + "x" * 30}] * 6
    assert any("questions" in v for _, v in style_lessons(qs))
    pid = [{"role": "user", "content": "wetin dey happen my guy, how far"}] * 6
    assert any("Pidgin" in v for _, v in style_lessons(pid))
    assert style_lessons([{"role": "user", "content": "hi"}] * 3) == []


def test_chat_llm_extract_when_regex_misses():
    cfg, mem = _stack(offline=False)
    router = FakeRouter(['[{"key": "name", "value": "Chidi"}]',
                         "Hey Chidi! 😊"])
    agent = CompanionAgent(cfg, router, mem)
    out = agent.chat("I'm Chidi by the way, good to meet you, i have been "
                     "working late nights on this project and it is honestly "
                     "exhausting but we move", "lx", bond_id="telegram:5")
    assert out["response"] == "Hey Chidi! 😊" and router.calls == 2
    assert ("name", "Chidi") in agent.bonds.get_facts("telegram", "5")
    mem.close()


def test_chat_regex_hit_skips_llm():
    cfg, mem = _stack(offline=False)
    router = FakeRouter(["Yes Mary 🙏"])
    agent = CompanionAgent(cfg, router, mem)
    agent.chat("call me Mary please, i have been working late nights on this "
               "project and it is honestly exhausting but we move",
               "ly", bond_id="telegram:6")
    assert router.calls == 1  # no extraction call needed
    assert ("name", "Mary") in agent.bonds.get_facts("telegram", "6")
    mem.close()


# ---- self alerts ----
def test_warn_queues_to_self_and_throttles():
    cfg, mem = _stack(owner_tg="me")
    srv, base = _serve(cfg, mem)
    try:
        r1 = _post(base, "/warn", {"message": "brain down",
                                   "kind": "testkind_warn"})
        assert len(r1["queued"]) == 1
        with urllib.request.urlopen(base + "/outbox?channel=telegram",
                                    timeout=10) as r:
            pend = json.loads(r.read().decode())["pending"]
        assert any(p["to"] == "me" and "bot alert" in p["message"]
                   for p in pend)
        r2 = _post(base, "/warn", {"message": "brain down",
                                   "kind": "testkind_warn"})
        assert r2.get("throttled") is True and r2["queued"] == []
    finally:
        srv.shutdown()
        mem.close()


# ---- history import ----
def test_import_backfills_bonds_and_facts():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        now = time.time()
        msgs = [
            {"role": "user", "text": "call me Mary", "ts": now - 3 * 86400},
            {"role": "assistant", "text": "hey Mary!", "ts": now - 3 * 86400},
            {"role": "user", "text": "i hate traffic so much",
             "ts": now - 86400},
            {"role": "user", "text": "how are you today my friend",
             "ts": now - 100},
        ]
        r = _post(base, "/import", {"channel": "telegram", "chat_id": "77",
                                    "messages": msgs})
        assert r["imported"] == 4
        assert "name=Mary" in r["facts"] and "hate=traffic so much" in r["facts"]
        with urllib.request.urlopen(base + "/memory?channel=telegram&chat_id=77",
                                    timeout=10) as resp:
            data = json.loads(resp.read().decode())
        assert data["bond"]["count"] == 3  # user msgs only
        assert {"key": "name", "value": "Mary"} in data["facts"]
    finally:
        srv.shutdown()
        mem.close()


# ---- lesson distillation ----
def test_summary_distills_lesson():
    cfg, mem = _stack(offline=False)
    router = FakeRouter(["- Mary likes suya\nLESSON: keep texts short with Mary"])
    agent = CompanionAgent(cfg, router, mem)
    hist = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message {i} about suya"} for i in range(35)]
    agent.summarize_if_due("lz", hist)
    assert mem.search("short with Mary", kind="lesson") != []
    sums = mem.search("suya", kind="summary")
    assert sums and "LESSON" not in sums[0].content
    mem.close()


# ---- discord ----
def _load_discord():
    path = Path(__file__).resolve().parent.parent / "bridges" / "discord_bot.py"
    spec = importlib.util.spec_from_file_location("discord_bridge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAuthor:
    def __init__(self, id, bot=False, name="Zed"):
        self.id = id
        self.bot = bot
        self.display_name = name


class _FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeChannel:
    def __init__(self, id=1):
        self.id = id
        self.sent = []

    def typing(self):
        return _FakeTyping()

    async def send(self, text):
        self.sent.append(text)


class _FakeGuild:
    def __init__(self, id=9):
        self.id = id


class _FakeMsg:
    def __init__(self, content, author, guild=None, mentions=None, channel=None):
        self.content = content
        self.author = author
        self.guild = guild
        self.mentions = mentions or []
        self.channel = channel or _FakeChannel()


class _FakeClient:
    def __init__(self, bot_id=999):
        self.user = types.SimpleNamespace(id=bot_id)


def test_discord_should_reply():
    mod = _load_discord()
    me = _FakeAuthor(999, bot=True)
    assert mod.should_reply(_FakeMsg("hi", me), 999) is False
    assert mod.should_reply(_FakeMsg("   ", _FakeAuthor(1)), 999) is False
    assert mod.should_reply(_FakeMsg("hi", _FakeAuthor(1)), 999) is True
    g = _FakeMsg("hi all", _FakeAuthor(1), guild=_FakeGuild())
    assert mod.should_reply(g, 999) is False
    gm = _FakeMsg("hi <@999>", _FakeAuthor(1), guild=_FakeGuild(),
                  mentions=[_FakeAuthor(999, bot=True)])
    assert mod.should_reply(gm, 999) is True


def test_discord_owner_cmds(monkeypatch):
    mod = _load_discord()

    async def fake_api(path, payload=None, timeout=120):
        if path == "/tick":
            return {"queued": []}
        if path.startswith("/bond"):
            return {"bond": {"level": 2, "score": 45, "friction": 0, "streak": 4}}
        if path.startswith("/memory"):
            return {"facts": [{"key": "like", "value": "suya"}]}
        raise AssertionError(path)
    monkeypatch.setattr(mod, "api", fake_api)
    assert asyncio.run(mod.run_owner_cmd("tick", "")) == "nothing due 😴"
    assert "bond[5]" in asyncio.run(mod.run_owner_cmd("bond", "5"))
    assert "suya" in asyncio.run(mod.run_owner_cmd("memory", "5"))
    assert "discord cmds" in asyncio.run(mod.run_owner_cmd("frobnicate", ""))


def test_discord_dm_flow(monkeypatch):
    mod = _load_discord()

    async def fake_chat(text, aid, display, cid, is_group=False):
        return {"response": "yo 😊", "mood": "happy", "substance": 0.5}
    monkeypatch.setattr(mod, "chat_full", fake_chat)
    ch = _FakeChannel()
    msg = _FakeMsg("hello", _FakeAuthor(1), channel=ch)
    out = asyncio.run(mod.handle_message(msg, _FakeClient()))
    assert out == "yo 😊" and ch.sent == ["yo 😊"]


# ---- baileys static ----
def test_baileys_defines_everything_it_calls():
    import re
    src = (Path(__file__).resolve().parent.parent / "bridges"
           / "whatsapp_baileys.js").read_text()
    code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    code = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`",
                  "''", code)
    code = re.sub(r"//[^\n]*", " ", code)
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_]\w*)", code))
    defined |= set(re.findall(r"const\s+([A-Za-z_]\w*)\s*=", src))
    assert {"handleMessage", "pollOutbox", "getAIResponse", "shouldRespond",
            "isSpamming", "humanSend", "warnSelf", "startSock",
            "registerContact", "splitBubbles"} <= defined
    calls = set(m.group(1) for m in
                re.finditer(r"(?<![.\w$])([A-Za-z_]\w*)\s*\(", code))
    builtins = {"if", "for", "while", "switch", "catch", "function", "return",
                "require", "setTimeout", "parseInt", "console", "process",
                "setInterval", "Promise", "JSON", "Math", "Date", "isNaN",
                "Set", "Map", "Client", "LocalAuth", "async", "of", "new"}
    undefined = {c for c in calls - defined - builtins
                 if not c.startswith("_")}
    assert not undefined, f"undefined calls in whatsapp_baileys.js: {undefined}"


# ---- tg_import pure ----
def test_build_items():
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                             / "bridges"))
    import tg_import
    recs = [{"text": "hi", "out": False, "ts": 100.0, "sender_id": 5,
             "chat_id": "5"},
            {"text": "  ", "out": False, "ts": 101.0, "sender_id": 5},
            {"text": "hey", "out": True, "ts": 102.0, "sender_id": 1,
             "chat_id": "5"}]
    items = tg_import.build_items(recs)
    assert len(items) == 2 and items[0]["role"] == "user"
    assert items[1]["role"] == "assistant" and items[0]["ts"] == 100.0
