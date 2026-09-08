"""Fusion tests: personas, mood, companion tools, server. Offline, no keys."""
import json
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

from godquant.agents.orchestrator import Orchestrator
from godquant.companion import web_search as WS
from godquant.companion.companion import CompanionAgent
from godquant.companion.mood import MoodEngine
from godquant.companion.personas import PERSONAS, get_persona, render_persona
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore


def _stack():
    tmp = Path(tempfile.mkdtemp())
    cfg = GodQuantConfig(offline=True, workspace=str(tmp / "ws"),
                         memory_db=str(tmp / "mem.db"), max_workers=2)
    mem = MemoryStore(cfg.resolved_memory_db())
    router = LLMRouter(cfg, mem)
    return cfg, mem, router, Orchestrator(cfg, router, mem)


# ---- personas ----
def test_all_personas_render():
    for name in PERSONAS:
        text = render_persona(name, mood_context="happy", history_summary="hi")
        assert "{mood_context}" not in text and "{history_summary}" not in text
        assert len(text) > 200


def test_unknown_persona_raises():
    with pytest.raises(ValueError):
        get_persona("nobody")


def test_alex_has_identity():
    assert "Alex" in render_persona("alex")


# ---- mood ----
def test_mood_triggers():
    eng = MoodEngine()
    assert eng.update("c1", "i love you so much").current in ("happy", "excited")
    assert eng.update("c2", "whatever, shut up").current in ("annoyed", "angry")
    assert eng.update("c3", "my ex is so pretty, talking to her").current == "jealous"


def test_mood_apology_cools():
    eng = MoodEngine()
    eng.update("c4", "shut up idiot")
    assert eng.state("c4").current in ("annoyed", "angry")
    eng.update("c4", "sorry, my bad")
    assert eng.state("c4").level < 10


def test_mood_sampling_ranges():
    eng = MoodEngine()
    eng.update("c5", "shut up")
    s = eng.sampling("c5")
    assert s["max_tokens"] <= 200
    eng.update("c6", "i love you")
    assert eng.sampling("c6")["max_tokens"] >= 200


# ---- web search (mocked network) ----
def test_ddg_parse_regex_path(monkeypatch):
    html = ('<a href="https://example.com/a">Example Title One</a>'
            '<a href="https://x.com/b">Second Result Title</a>')
    monkeypatch.setattr(WS, "_get", lambda url, timeout=15: html)
    monkeypatch.setattr(WS, "_BS", None)
    hits = WS.ddg_text("test", 5)
    assert len(hits) == 2 and hits[0]["href"].startswith("https://")


def test_ddg_parse_uddg_redirect(monkeypatch):
    # lite endpoint wraps result URLs: //duckduckgo.com/l/?uddg=<encoded>
    html = ('<a rel="nofollow" href="//duckduckgo.com/l/?uddg='
            'https%3A%2F%2Fexample.com%2Freal&amp;rut=abc">Wrapped Result Title</a>')
    monkeypatch.setattr(WS, "_get", lambda url, timeout=15: html)
    monkeypatch.setattr(WS, "_BS", None)
    hits = WS.ddg_text("test", 5)
    assert len(hits) == 1 and hits[0]["href"] == "https://example.com/real"


def test_smart_search_routes(monkeypatch):
    monkeypatch.setattr(WS, "advanced_search", lambda q, t="text": f"TYPE={t}")
    monkeypatch.setattr(WS, "fact_check_query", lambda q: "TYPE=fact_check")
    monkeypatch.setattr(WS, "wiki_search", lambda q: "TYPE=wiki")
    assert "news" in WS.smart_search("latest BTC news today")
    assert "fact" in WS.smart_search("verify this claim, is it true")
    assert "wiki" in WS.smart_search("what is quantum computing")


# ---- companion agent ----
def test_companion_chat_offline():
    cfg, mem, router, _ = _stack()
    agent = CompanionAgent(cfg, router, mem)
    out = agent.chat("hey, how are you?", "t1", persona="alex")
    from godquant.companion.mood import MOODS
    assert out["response"] and out["mood"] in MOODS
    assert out["persona"] == "alex"
    assert out["mood_level"] >= 1
    mem.close()


def test_companion_backtest_tool_fires():
    cfg, mem, router, _ = _stack()
    agent = CompanionAgent(cfg, router, mem)
    ctx = agent._maybe_tools("please backtest sma_cross on BTCUSDT")
    assert "TOOL:BACKTEST" in ctx and "Sharpe" in ctx
    mem.close()


def test_companion_history_persists():
    cfg, mem, router, _ = _stack()
    agent = CompanionAgent(cfg, router, mem)
    agent.chat("remember the codeword pineapple", "t2", persona="quant")
    hist = agent._load_history("t2")
    assert any("pineapple" in m["content"] for m in hist)
    mem.close()


# ---- server ----
def _serve(stack):
    cfg, mem, router, orch = stack
    companion = CompanionAgent(cfg, router, mem)
    srv = create_server(cfg, router, mem, orch, companion,
                        host="127.0.0.1", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(base, path, obj):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def test_server_chat_mood_status():
    stack = _stack()
    srv, base = _serve(stack)
    try:
        assert _post(base, "/chat", {"message": "hey babe i miss you",
                                     "conversation_id": "s1"})["mood"] in (
            "happy", "excited", "neutral")
        mood = json.loads(urllib.request.urlopen(
            base + "/mood?conversation_id=s1", timeout=10).read().decode())
        assert mood["current"] in ("happy", "excited", "neutral")
        assert _post(base, "/reset", {"conversation_id": "s1"})["message"] == "Fresh start"
        st = json.loads(urllib.request.urlopen(base + "/status", timeout=10).read().decode())
        assert st["status"] == "online"
    finally:
        srv.shutdown()
        stack[1].close()


def test_server_backtest_and_risk():
    stack = _stack()
    srv, base = _serve(stack)
    try:
        bt = _post(base, "/backtest", {"symbol": "BTCUSDT",
                                       "strategy": "sma_cross"})
        assert "sharpe" in json.dumps(bt).lower()
        rk = _post(base, "/risk", {"equity": 5000, "risk": 0.01,
                                   "entry": 100, "stop": 95})
        assert "VERDICT" in rk["output"]
    finally:
        srv.shutdown()
        stack[1].close()
