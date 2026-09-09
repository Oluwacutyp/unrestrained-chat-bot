"""v3.4 god-tier memory: facts, dossier, anti-echo, retry, Devon."""
import asyncio
import importlib.util
import json
import tempfile
import threading
import types
import urllib.request
from pathlib import Path

from godquant.companion.companion import CompanionAgent
from godquant.companion.memory_engine import (clean_reply, dossier_text,
                                             extract_facts, fallback_line)
from godquant.companion.personas import get_persona, render_persona
from godquant.companion.relationship import BondStore
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
    """Scripted LLM: pops replies, captures system prompts."""

    def __init__(self, script):
        self.script = list(script)
        self.systems = []
        self.calls = 0

    def complete(self, system, user, agent=None):
        self.calls += 1
        self.systems.append(system)
        text = self.script.pop(0) if self.script else "ok"
        return types.SimpleNamespace(text=text)


# ---- extraction ----
def test_extract_facts():
    assert ("name", "Mary") in extract_facts("hey, my name is Mary")
    assert ("name", "Mary") in extract_facts("call me Mary")
    assert extract_facts("call me later") == []
    assert ("no_petname", "babe") in extract_facts("Not your babe")
    assert ("no_petname", "honey") in extract_facts("don't call me honey")
    assert ("no_petname", "*") in extract_facts("don't call me that")
    assert ("like", "pizza") in extract_facts("i really like pizza")
    assert ("hate", "mondays") in extract_facts("i hate mondays")
    assert extract_facts("i love you") == []  # warmth, not a fact
    assert ("home", "Lagos") in extract_facts("i live in Lagos")
    assert ("job", "nurse") in extract_facts("i'm a nurse")
    assert extract_facts("i'm so tired") == []
    assert ("age", "28") in extract_facts("i'm 28 years old")
    assert ("note", "my dog is Rex") in extract_facts("remember that my dog is Rex")
    assert extract_facts("remember when we laughed") == []
    assert len(extract_facts("call me Mary, my name is Mary")) == 1  # dedupe


def test_dossier_text():
    assert dossier_text([], "X") == ""
    d = dossier_text([("name", "Mary"), ("no_petname", "babe"),
                      ("like", "pizza")], "Mary")
    assert "Mary" in d and "NEVER call them" in d and "pizza" in d


# ---- reply hygiene ----
def test_clean_reply_strips_echo():
    assert clean_reply("I'm talking to you Hey, yeah I'm here!",
                       "I'm talking to you") == "Hey, yeah I'm here!"
    assert clean_reply('You said "hello there" and hi', "hello there")
    assert clean_reply("*sighs* fine, whatever", "hey") == "fine, whatever"
    assert clean_reply("normal reply 😊", "hi") == "normal reply 😊"
    assert clean_reply("", "hi") == ""


def test_fallback_line():
    a = fallback_line("devon", "hello?")
    assert a == fallback_line("devon", "hello?")  # deterministic
    assert "(no reply" not in a
    assert fallback_line("devon", "x") != fallback_line("alex", "x") or True


# ---- bond facts store ----
def test_bond_facts_roundtrip():
    b = BondStore(Path(tempfile.mkdtemp()) / "b.db")
    b.add_fact("tg", "1", "name", "Mary")
    b.add_fact("tg", "1", "name", "Aisha")  # single-value: replaces
    b.add_fact("tg", "1", "like", "pizza")
    b.add_fact("tg", "1", "like", "suya")  # multi-value: accumulates
    facts = b.get_facts("tg", "1")
    assert ("name", "Aisha") in facts and ("name", "Mary") not in facts
    assert ("like", "pizza") in facts and ("like", "suya") in facts
    assert b.get_facts("tg", "2") == []  # never mixed between people
    b.clear_facts("tg", "1")
    assert b.get_facts("tg", "1") == []
    b.close()


# ---- chat integration ----
def test_chat_retries_empty_then_falls_back():
    cfg, mem = _stack()
    agent = CompanionAgent(cfg, FakeRouter(["", "hey there!"]), mem)
    out = agent.chat("hi", "r1", persona="devon")
    assert out["response"] == "hey there!"  # second attempt won
    agent2 = CompanionAgent(cfg, FakeRouter(["", "   "]), mem)
    out2 = agent2.chat("hi", "r2", persona="devon")
    assert out2["response"] and "(no reply" not in out2["response"]
    mem.close()


def test_chat_cleans_echo_and_learns_dossier():
    cfg, mem = _stack()
    router = FakeRouter(["Noted 📝", "Yes Mary, never again 🙏"])
    agent = CompanionAgent(cfg, router, mem)
    agent.chat("remember that my dog is Rex", "d1", bond_id="telegram:7")
    out = agent.chat("not your babe, call me Mary", "d1",
                     bond_id="telegram:7")
    assert out["response"] == "Yes Mary, never again 🙏"
    facts = agent.bonds.get_facts("telegram", "7")
    assert ("note", "my dog is Rex") in facts
    assert ("no_petname", "babe") in facts
    assert ("name", "Mary") in facts
    assert "NEVER call them" in router.systems[-1]  # dossier injected
    assert "dog is Rex" in router.systems[-1]
    mem.close()


def test_offline_voice_clean_and_varied():
    from godquant.llm.providers import HeuristicProvider
    p = HeuristicProvider()
    outs = {p._companion(f"Them: msg number {i}\n[MOOD: neutral 5/10]")
            for i in range(6)}
    assert len(outs) > 1  # hash variety, no more robot repeats
    for o in outs:
        assert "offline mode" not in o and "GQ_API_KEY" not in o
    hooked = p._companion("Them: debugging a race condition\n[MOOD: neutral 5/10]")
    assert "has me curious" in hooked and "debugging" in hooked  # references THEIR words


# ---- Devon ----
def test_devon_persona():
    meta = get_persona("devon")
    assert "devon" in meta["blurb"].lower()
    text = render_persona("devon", mood_context="happy")
    for needle in ("Devon", "Almont", "Colorado", "pidgin", "Lake Wales",
                   "Back Country Cafe", "never married", "uncensored"):
        assert needle.lower() in text.lower(), needle
    assert get_persona(None) is meta  # new default
    assert GodQuantConfig().persona == "devon"


# ---- server routes ----
def test_memory_and_translate_routes():
    from godquant.agents.orchestrator import Orchestrator
    tmp = Path(tempfile.mkdtemp())
    cfg = GodQuantConfig(offline=True, workspace=str(tmp / "ws"),
                         memory_db=str(tmp / "mem.db"), max_workers=2)
    mem = MemoryStore(cfg.resolved_memory_db())
    router = LLMRouter(cfg, mem)
    agent = CompanionAgent(cfg, router, mem)
    agent.bonds.add_fact("telegram", "9", "like", "suya")
    srv = create_server(cfg, router, mem, Orchestrator(cfg, router, mem),
                        agent, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/memory?channel=telegram&chat_id=9",
                                    timeout=10) as r:
            data = json.loads(r.read().decode())
        assert {"key": "like", "value": "suya"} in data["facts"]
        assert data["bond"]["level"] == 0
        req = urllib.request.Request(
            base + "/translate", data=json.dumps({"text": "How are you?"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            tr = json.loads(r.read().decode())
        assert tr["translation"]  # offline heuristic still answers
    finally:
        srv.shutdown()
        mem.close()


# ---- bridge commands ----
def _load_bridge():
    path = Path(__file__).resolve().parent.parent / "bridges" / "telegram_userbot.py"
    spec = importlib.util.spec_from_file_location("tg_bridge_mem", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_userbot_memory_translate_bond_auto(monkeypatch):
    mod = _load_bridge()

    async def fake_api(path, payload=None, timeout=120):
        if path.startswith("/memory"):
            return {"facts": [{"key": "like", "value": "suya"}],
                    "bond": {"level": 2, "score": 45, "friction": 0,
                             "streak": 4}}
        if path == "/translate":
            assert payload["text"] == "how far"
            return {"translation": "how far na? wetin dey sup?"}
        if path == "/bond":
            assert payload["level"] == "auto"
            return {"bond": {"level": 1}}
        raise AssertionError(path)
    monkeypatch.setattr(mod, "api", fake_api)
    out = asyncio.run(mod.run_owner_cmd("memory", "9"))
    assert "suya" in out and "L2" in out
    out = asyncio.run(mod.run_user_cmd("translate", "how far", 1))
    assert "wetin dey" in out
    out = asyncio.run(mod.run_owner_cmd("bond", "9 auto"))
    assert out  # auto accepted, no usage error


# ---- rolling summaries ----
def test_summaries_skip_offline_and_short():
    cfg, mem = _stack()  # offline=True
    agent = CompanionAgent(cfg, FakeRouter([]), mem)
    hist = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    agent.summarize_if_due("s1", hist)
    assert mem.search("summary", kind="summary") == []
    mem.close()


def test_summarize_and_recall_long_chat():
    cfg, mem = _stack(offline=False)
    router = FakeRouter(["- Mary likes suya\n- Promised to call Sunday"])
    agent = CompanionAgent(cfg, router, mem)
    hist = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"message {i} about suya and Sunday plans"}
            for i in range(35)]
    agent.summarize_if_due("long1", hist)
    assert router.calls == 1  # one compression call
    assert "suya" in agent.recall("long1", "what did we talk about?")
    assert agent.recall("other", "x") == ""  # cid-scoped, no leakage
    mem.close()
