"""v3.6 command deck: owner core, persona/model/memory routes, discord life."""
import asyncio
import datetime
import importlib.util
import json
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.request
from pathlib import Path

import pytest

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stack(**over):
    tmp = Path(tempfile.mkdtemp())
    kw = dict(offline=True, workspace=str(tmp / "ws"),
              memory_db=str(tmp / "mem.db"), max_workers=2)
    kw.update(over)
    cfg = GodQuantConfig(**kw)
    mem = MemoryStore(cfg.resolved_memory_db())
    return cfg, mem


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
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


# ---------- shared owner core ----------
def _fake_api():
    async def fake(path, payload=None, timeout=120):
        if path == "/tick":
            return {"queued": []}
        if path == "/send":
            return {"queued": 7}
        if path == "/contacts":
            return {"contacts": [{"channel": "telegram", "chat_id": "1",
                                  "display": "Zed", "enabled": True}]}
        if path.startswith("/mood"):
            return {"current": "happy", "level": 8}
        if path == "/reset":
            return {"message": "Fresh start"}
        if path == "/mission":
            return {"results": [{"agent": "a", "output": "did it"}]}
        if path == "/code":
            return {"path": "/w/code/x.py", "summary": "ok"}
        if path == "/exec":
            return {"exit": 0, "output": "hi\n"}
        if path == "/models":
            return {"primary": "groq", "chain": ["groq", "heuristic"],
                    "usage": {"llm_calls": 3, "spend_usd": 0}}
        if path == "/model":
            return {"primary": payload["primary"]} if payload["primary"] != "nope" \
                else {"error": "unknown provider"}
        if path == "/persona":
            if payload and payload.get("persona") == "bad":
                return {"error": "unknown persona"}
            if payload and payload.get("chat_id"):
                return {"persona": payload["persona"], "override": True} \
                    if payload.get("persona") != "clear" else {"cleared": True}
            if payload:
                return {"default": payload["persona"]}
            return {"default": "devon", "overrides": []}
        if path.startswith("/persona?"):
            return {"persona": "alex", "override": True}
        if path == "/bond":
            return {"bond": {"level": 3}}
        if path.startswith("/bond?"):
            return {"bond": {"level": 1, "score": 20, "friction": 0,
                             "streak": 2, "manual": False}}
        if path.startswith("/memory"):
            return {"facts": [{"key": "like", "value": "suya"}],
                    "bond": {"level": 1}}
        if path == "/forget":
            return {"facts": 4, "deep": bool(payload.get("deep"))}
        if path == "/status":
            return {"version": "3.6.0", "status": "online",
                    "memories": {"chat": 5}, "llm_calls": 9, "spend_usd": 0,
                    "persona": "devon"}
        raise AssertionError(path)
    return fake


def _run(cmd, arg, channel="telegram"):
    mod = _load("owner_core", "bridges/owner.py")
    return asyncio.run(mod.run_owner_command(_fake_api(), channel, cmd, arg))


def test_owner_core_all_commands():
    assert _run("tick", "") == "nothing due 😴"
    assert "#7" in _run("send", "1 yo")
    assert "usage" in _run("send", "x")
    assert "Zed" in _run("contacts", "")
    assert "happy" in _run("mood", "")
    assert _run("reset", "") == "reset ✨"
    assert "[a] did it" in _run("mission", "do stuff")
    assert "usage" in _run("mission", "")
    assert "x.py" in _run("code", "fizz")
    assert "hi" in _run("exec", "echo hi")
    assert "groq" in _run("models", "")
    assert "groq" in _run("model", "groq")
    assert "failed" in _run("model", "nope")
    assert "devon" in _run("persona", "")
    assert "alex" in _run("persona", "alex")
    assert "override" in _run("persona", "42")
    assert "quant" in _run("persona", "42 quant")
    assert "cleared" in _run("persona", "42 clear")
    assert "unknown persona" in _run("persona", "42 xxx")
    assert "bond[42]" in _run("bond", "42")
    assert "pinned" in _run("bond", "42 3")
    assert "usage" in _run("bond", "42 9")
    assert "suya" in _run("memory", "42")
    assert "4 facts" in _run("forget", "42")
    assert "zeroed" in _run("forget", "42 deep")
    assert "3.6.0" in _run("stats", "")
    assert "userbot cmds" in _run("help", "")
    assert "discord cmds" in _run("help", "", channel="discord")
    assert "userbot cmds" in _run("frobnicate", "")


def test_bridge_wrappers_delegate(monkeypatch):
    tg = _load("tg_wrap", "bridges/telegram_userbot.py")
    monkeypatch.setattr(tg, "api", _fake_api())
    assert asyncio.run(tg.run_owner_cmd("tick", "")) == "nothing due 😴"
    assert "userbot cmds" in asyncio.run(tg.run_owner_cmd("help", ""))
    assert "client not ready" in asyncio.run(tg.run_owner_cmd("import", "me"))
    dc = _load("dc_wrap", "bridges/discord_bot.py")
    monkeypatch.setattr(dc, "api", _fake_api())
    assert asyncio.run(dc.run_owner_cmd("tick", "")) == "nothing due 😴"
    assert "discord cmds" in asyncio.run(dc.run_owner_cmd("frobnicate", ""))


# ---------- persona / model / forget / code / exec routes ----------
def test_persona_routes_and_chat_resolution():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        assert _post(base, "/persona", {"persona": "alex"})["default"] == "alex"
        assert _post(base, "/persona",
                     {"persona": "bad"})["error"]
        r = _post(base, "/persona", {"channel": "telegram", "chat_id": "5",
                                     "persona": "quant"})
        assert r["override"] is True
        g = json.loads(urllib.request.urlopen(
            base + "/persona?channel=telegram&chat_id=5",
            timeout=10).read().decode())
        assert g["persona"] == "quant" and g["override"] is True
        out = _post(base, "/chat", {"message": "hey", "conversation_id": "tg:5",
                                    "channel": "telegram", "chat_id": "5"})
        assert out["persona"] == "quant"  # override wins, no persona sent
        out2 = _post(base, "/chat", {"message": "hey",
                                     "conversation_id": "tg:6"})
        assert out2["persona"] == "alex"  # global default otherwise
        _post(base, "/persona", {"channel": "telegram", "chat_id": "5",
                                 "persona": "clear"})
        g2 = json.loads(urllib.request.urlopen(
            base + "/persona?channel=telegram&chat_id=5",
            timeout=10).read().decode())
        assert g2["override"] is False
    finally:
        srv.shutdown()
        mem.close()


def test_models_and_runtime_switch():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        with urllib.request.urlopen(base + "/models", timeout=10) as r:
            m = json.loads(r.read().decode())
        assert m["primary"] == "heuristic" and "heuristic" in m["chain"]
        assert "usage" in m
        r = _post(base, "/model", {"primary": "groq"})
        assert r["primary"] == "groq" and r["chain"][0] == "groq"
        assert "error" in _post(base, "/model", {"primary": "nope"})
    finally:
        srv.shutdown()
        mem.close()


def test_forget_wipes_facts_and_bond():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        _post(base, "/import", {"channel": "telegram", "chat_id": "88",
                                "messages": [{"role": "user", "text": "call me Mary"},
                                             {"role": "user", "text": "i love suya"}]})
        r = _post(base, "/forget", {"channel": "telegram", "chat_id": "88"})
        assert r["facts"] >= 2 and r["deep"] is False
        with urllib.request.urlopen(base + "/memory?channel=telegram&chat_id=88",
                                    timeout=10) as resp:
            assert json.loads(resp.read().decode())["facts"] == []
        _post(base, "/import", {"channel": "telegram", "chat_id": "89",
                                "messages": [{"role": "user",
                                              "text": "i miss you so much"}]})
        d = _post(base, "/forget", {"channel": "telegram", "chat_id": "89",
                                    "deep": True})
        assert d["deep"] is True
        with urllib.request.urlopen(base + "/bond?channel=telegram&chat_id=89",
                                    timeout=10) as resp:
            bond = json.loads(resp.read().decode())["bond"]
        assert bond["level"] == 0 and bond["score"] == 0
    finally:
        srv.shutdown()
        mem.close()


def test_code_writes_audited_file():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        r = _post(base, "/code", {"task": "add two numbers"})
        assert r["path"].endswith(".py") and Path(r["path"]).exists()
        assert "audit" in r and r["summary"]
    finally:
        srv.shutdown()
        mem.close()


def test_exec_flag_gates_shell():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        assert "disabled" in _post(base, "/exec", {"cmd": "echo hi"})["error"]
    finally:
        srv.shutdown()
        mem.close()
    cfg2, mem2 = _stack(allow_exec=True)
    srv2, base2 = _serve(cfg2, mem2)
    try:
        r = _post(base2, "/exec", {"cmd": "echo hello"})
        assert r["exit"] == 0 and "hello" in r["output"]
    finally:
        srv2.shutdown()
        mem2.close()


# ---------- wa_owner.js: node tests + static ----
def test_wa_owner_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node missing")
    r = subprocess.run([node, "tests/test_owner.js"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout


def test_wa_owner_defines_everything_it_calls():
    import re
    src = (ROOT / "bridges" / "wa_owner.js").read_text()
    code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    code = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`",
                  "''", code)
    code = re.sub(r"//[^\n]*", " ", code)
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_]\w*)", code))
    defined |= set(re.findall(r"const\s+([A-Za-z_]\w*)\s*=", src))
    assert {"handleOwnerCommand", "parseOwnerCommand", "personaCmd"} <= defined
    calls = set(m.group(1) for m in
                re.finditer(r"(?<![.\w$])([A-Za-z_]\w*)\s*\(", code))
    builtins = {"if", "for", "while", "switch", "catch", "function", "return",
                "require", "setTimeout", "parseInt", "console", "process",
                "setInterval", "Promise", "JSON", "Math", "Date", "isNaN",
                "Set", "Map", "async", "of", "new", "post"}  # post = injected
    undefined = {c for c in calls - defined - builtins
                 if not c.startswith("_")}
    assert not undefined, f"undefined calls in wa_owner.js: {undefined}"


# ---------- discord ambient life ----------
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
    def __init__(self, id=5, name="general"):
        self.id = id
        self.name = name
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


def test_ambient_score():
    mod = _load("dc_amb", "bridges/discord_bot.py")
    plain = mod.ambient_score("just a statement here")
    assert plain == mod.AMBIENT_P
    assert mod.ambient_score("anyone know why?") > plain
    assert mod.ambient_score("debug this python bug") > plain
    assert mod.ambient_score("hi", level=3) > mod.ambient_score("hi")
    assert mod.ambient_score("x? " + "code " * 60, level=3) <= 0.9


def test_maybe_ambient_joins_then_cooldown(monkeypatch):
    mod = _load("dc_amb2", "bridges/discord_bot.py")

    async def fake_api(path, payload=None, timeout=120):
        return {"bond": {"level": 2}}
    monkeypatch.setattr(mod, "api", fake_api)
    monkeypatch.setattr(mod, "chat_full",
                        lambda t, a, d, c, g=False: asyncio.sleep(
                            0, {"response": "ooh code talk 👀", "mood": "happy",
                                "substance": 0.4}))
    monkeypatch.setattr(random, "random", lambda: 0.0)
    msg = _FakeMsg("anyone debug python?", _FakeAuthor(3),
                   guild=_FakeGuild(), channel=_FakeChannel(5))
    out = asyncio.run(mod.maybe_ambient(msg, _FakeClient()))
    assert out == "ooh code talk 👀" and msg.channel.sent == [out]
    assert asyncio.run(mod.maybe_ambient(msg, _FakeClient())) is None


def test_maybe_ambient_hourly_cap(monkeypatch):
    mod = _load("dc_amb3", "bridges/discord_bot.py")
    monkeypatch.setattr(random, "random", lambda: 0.0)
    mod._ambient_hits["5"] = [time.time()] * 6
    msg = _FakeMsg("hello?", _FakeAuthor(3), guild=_FakeGuild(),
                   channel=_FakeChannel(5))
    assert asyncio.run(mod.maybe_ambient(msg, _FakeClient())) is None


def test_welcome_dm(monkeypatch):
    mod = _load("dc_wel", "bridges/discord_bot.py")
    monkeypatch.setattr(mod, "chat_full",
                        lambda t, a, d, c, g=False: asyncio.sleep(
                            0, {"response": "hey newcomer!", "mood": "happy",
                                "substance": 0.1}))
    dm = _FakeChannel(9)

    async def fake_dm():
        return dm
    member = types.SimpleNamespace(
        id=7, display_name="Newbie", bot=False,
        guild=types.SimpleNamespace(id=1, name="Cool", text_channels=[]),
        create_dm=fake_dm)
    asyncio.run(mod.welcome_member(member, _FakeClient()))
    assert dm.sent == ["hey newcomer!"]


def test_discord_catchup_imports(monkeypatch):
    mod = _load("dc_catch", "bridges/discord_bot.py")
    monkeypatch.setattr(mod, "CATCHUP", True)
    posted = {}

    async def fake_api(path, payload=None, timeout=120):
        posted.update(payload)
        return {"imported": 2, "facts": []}
    monkeypatch.setattr(mod, "api", fake_api)

    class HistChannel(_FakeChannel):
        def history(self, limit=50):
            async def gen():  # discord yields newest-first; catchup reverses
                for i, t in enumerate(["second msg here", "first msg here"]):
                    yield types.SimpleNamespace(
                        content=t, author=_FakeAuthor(3),
                        created_at=datetime.datetime.now(datetime.timezone.utc))
            return gen()

    ch = HistChannel(11, "lobby")
    client = types.SimpleNamespace(
        guilds=[types.SimpleNamespace(id=1, text_channels=[ch])])
    asyncio.run(mod.discord_catchup(client))
    assert posted["chat_id"] == "11" and len(posted["messages"]) == 2
    assert posted["messages"][0]["text"] == "first msg here"  # chronological
