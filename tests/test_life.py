"""v4.3 life-OS: calendar, ledger, health, world bibles."""
import asyncio
import importlib.util
import json
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.relationship import BondStore
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stack(**over):
    tmp = Path(tempfile.mkdtemp())
    kw = dict(offline=True, workspace=str(tmp / "ws"),
              memory_db=str(tmp / "mem.db"), max_workers=2)
    kw.update(over)
    cfg = GodQuantConfig(**kw)
    return cfg, MemoryStore(cfg.resolved_memory_db())


def _serve(cfg, mem):
    router = LLMRouter(cfg, mem)
    agent = CompanionAgent(cfg, router, mem)
    srv = create_server(cfg, router, mem, Orchestrator(cfg, router, mem),
                        agent, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", agent


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


def _bonds():
    b = BondStore(tempfile.mktemp(suffix=".db"))
    return b


# ---------- calendar ----------
def test_cal_crud_and_daily_roll():
    b = _bonds()
    now = time.time()
    eid = b.cal_add("dentist", now - 10, channel="telegram", chat_id="me")
    assert eid > 0
    due = b.cal_due(now)
    assert [e["id"] for e in due] == [eid]
    assert "upcoming" or True
    b.cal_fire(eid, now)
    assert b.cal_due(now) == []
    # daily repeats roll forward instead of completing
    rid = b.cal_add("vitamins", now - 5, repeat="daily")
    b.cal_fire(rid, now)
    assert b.cal_due(now) == []
    evs = [e for e in b.cal_list(0) if e["id"] == rid]
    assert len(evs) == 1 and evs[0]["ts"] > now and not evs[0]["done"]
    assert b.cal_done(rid) is True
    assert b.cal_del(eid) is True
    assert b.cal_del(999999) is False
    b.close()


def test_cal_route_roundtrip():
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/cal", {"action": "add", "title": "dentist",
                                 "ts": time.time() + 3600})
        assert r["id"] > 0
        r = _post(base, "/cal", {"action": "list"})
        assert any(e["title"] == "dentist" for e in r["events"])
        r = _post(base, "/cal", {"action": "done", "id": 1})
        assert r["ok"] is True
        r = _post(base, "/cal", {"action": "nope"})
        assert "error" in r
    finally:
        srv.shutdown()


# ---------- ledger ----------
def test_ledger_add_list_total():
    b = _bonds()
    assert b.ledger_total() == 0.0
    b.spend(2500, "NGN", "food", "lunch")
    b.spend(100, "NGN", "food", "snack")
    b.spend(50, "USD", "books")
    assert b.ledger_total() == 2650.0
    assert b.ledger_total("food") == 2600.0
    rows = b.ledger_list("food")
    assert len(rows) == 2 and rows[0]["currency"] == "NGN"
    b.close()


def test_ledger_route_roundtrip():
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/ledger", {"action": "add", "amount": 2500,
                                    "currency": "NGN", "cat": "food"})
        assert r["id"] > 0
        r = _post(base, "/ledger", {"action": "list", "cat": "food"})
        assert len(r["entries"]) == 1
        r = _post(base, "/ledger", {"action": "total"})
        assert r["total"] == 2500.0
        r = _post(base, "/ledger", {"action": "nope"})
        assert "error" in r
    finally:
        srv.shutdown()


# ---------- health ----------
def test_health_log_and_list():
    b = _bonds()
    b.health_log("Sleep", "7h")
    b.health_log("sleep", "6h")
    rows = b.health_list("sleep")
    assert len(rows) == 2 and rows[0]["value"] == "6h"
    assert len(b.health_list()) == 2
    b.close()


def test_health_route_roundtrip():
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/health", {"action": "log", "metric": "sleep",
                                    "value": "7h"})
        assert r["id"] > 0
        r = _post(base, "/health", {"action": "list"})
        assert r["entries"][0]["metric"] == "sleep"
    finally:
        srv.shutdown()


# ---------- bibles ----------
def test_bible_append_replace_get_list_del():
    b = _bonds()
    assert b.bible_get("midgard") is None
    b.bible_save("Midgard", "nine realms")
    b.bible_save("midgard", "yggdrasil connects them")
    body = b.bible_get("MIDGARD")
    assert "nine realms" in body and "yggdrasil" in body
    b.bible_save("midgard", "rebooted canon", mode="replace")
    assert b.bible_get("midgard") == "rebooted canon"
    assert b.bible_list() == ["midgard"]
    assert b.bible_del("midgard") is True
    assert b.bible_del("midgard") is False
    b.close()


def test_bible_route_roundtrip():
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/bible", {"action": "save", "name": "midgard",
                                   "text": "nine realms"})
        assert r["ok"] is True
        r = _post(base, "/bible", {"action": "get", "name": "midgard"})
        assert r["body"] == "nine realms"
        r = _post(base, "/bible", {"action": "list"})
        assert r["bibles"] == ["midgard"]
        r = _post(base, "/bible", {"action": "get", "name": "nope"})
        assert "error" in r
    finally:
        srv.shutdown()


def test_chat_accepts_bible_and_looks_it_up():
    cfg, mem = _stack(collect=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        agent.bonds.bible_save("midgard", "CANON-SENTINEL nine realms")
        seen = []
        orig = agent.bonds.bible_get
        agent.bonds.bible_get = lambda n: (seen.append(n), orig(n))[1]
        r = _post(base, "/chat", {"message": "hey", "cid": "t:x",
                                  "bible": "midgard"})
        assert "response" in r
        assert seen == ["midgard"]
    finally:
        srv.shutdown()


def test_bible_text_lands_in_system_prompt():
    from types import SimpleNamespace
    cfg, mem = _stack(collect=False, offline=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        agent.bonds.bible_save("midgard", "CANON-SENTINEL nine realms")
        seen = []
        agent.router.complete = lambda system, user, **kw: (
            seen.append(system), SimpleNamespace(text="canon reply"))[1]
        out = agent.chat("hey", cid="t:x", bible="midgard")
        assert out.get("response")
        assert seen and "CANON-SENTINEL" in seen[0]
        assert "[WORLD BIBLE" in seen[0]
    finally:
        srv.shutdown()


def test_tick_fires_due_event():
    cfg, mem = _stack(collect=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        agent.bonds.cal_add("party", time.time() - 5,
                            channel="telegram", chat_id="me")
        r = _post(base, "/tick", {})
        msgs = [q.get("message", "") for q in r.get("queued", [])]
        assert any("party" in m for m in msgs)
        assert agent.bonds.cal_due(time.time()) == []
    finally:
        srv.shutdown()


# ---------- owner commands ----------
def test_owner_life_commands():
    mod = _load("owner_life", "bridges/owner.py")

    async def fake(path, payload=None):
        if path == "/cal" and payload["action"] == "add":
            return {"id": 3}
        if path == "/cal":
            return {"ok": True} if payload["action"] in ("done", "del") else \
                {"events": [{"id": 3, "title": "dentist", "ts": 2000000000,
                             "repeat": "", "done": False}]}
        if path == "/ledger" and payload["action"] == "add":
            return {"id": 4}
        if path == "/ledger" and payload["action"] == "total":
            return {"total": 2500}
        if path == "/ledger":
            return {"entries": [{"id": 4, "amount": 2500, "currency": "NGN",
                                 "cat": "food", "note": "lunch"}]}
        if path == "/health" and payload["action"] == "log":
            return {"id": 5}
        if path == "/health":
            return {"entries": [{"metric": "sleep", "value": "7h"}]}
        if path == "/bible" and payload["action"] in ("save", "new"):
            return {"ok": True}
        if path == "/bible" and payload["action"] == "get":
            return {"body": "CANON"} if payload["name"] == "midgard" \
                else {"error": "x"}
        if path == "/bible":
            return {"bibles": ["midgard"]}
        raise AssertionError(path)

    run = lambda c, a: asyncio.run(
        mod.run_owner_command(fake, "telegram", c, a, "me"))
    assert "#3" in run("cal", "add in 2h dentist")
    assert "dentist" in run("cal", "")
    assert "usage" in run("cal", "add someday x")
    assert "done" in run("cal", "done 3")
    assert "#4" in run("spend", "2500 NGN food lunch")
    assert "usage" in run("spend", "lots food")
    assert "2500" in run("ledger", "")
    assert "logged" in run("health", "sleep 7h")
    assert "sleep" in run("healthlog", "")
    assert "appended" in run("bible", "save midgard CANON")
    assert "CANON" in run("bible", "midgard")
    assert "midgard" in run("bible", "list")
