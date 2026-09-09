"""v3.8 smart alarms: weave into live relevant chats, snooze."""
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
from godquant.companion.outbox import (Outbox, ProactiveEngine, weave_score)
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig, load_config
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


def _engine(**over):
    cfg, mem = _stack(**over)
    router = LLMRouter(cfg, mem)
    agent = CompanionAgent(cfg, router, mem)
    return cfg, mem, ProactiveEngine(cfg, agent, Outbox(cfg.resolved_memory_db()))


# ---------- scorer ----------
def test_weave_score():
    assert weave_score("call mom", "Mom", [("name", "Mom")]) >= 1
    assert weave_score("call mom", "Zed", [("like", "suya")]) == 0
    assert weave_score("ask Zed about the interview", "Zed", []) >= 1
    assert weave_score("buy milk", "Mom", [("name", "Mom")]) == 0
    assert weave_score("get jollof rice", "Zed", [("like", "jollof")]) >= 1
    assert weave_score("!!!", "Mom", []) == 0
    assert weave_score("", "Mom", []) == 0


# ---------- tick behavior ----------
def test_tick_weaves_into_live_relevant_chat():
    cfg, mem, eng = _engine()
    eng.outbox.upsert_contact("telegram", "1", "Mom", touch_inbound=True)
    eng.companion.bonds.add_fact("telegram", "1", "name", "Mom")
    eng.companion.bonds.add_reminder("telegram", "me", "call mom",
                                     time.time() - 5)
    eng.tick()
    by_to = {m["to"]: m["message"]
             for m in eng.outbox.pending("telegram")}
    assert "1" in by_to and "me" in by_to
    assert "⏰" not in by_to["1"]  # natural weave, no robot alarm
    assert by_to["me"].startswith("✅") and "Mom" in by_to["me"]
    assert eng.companion.bonds.list_reminders() == []
    mem.close()


def test_tick_raw_alarm_when_nothing_live():
    cfg, mem, eng = _engine()
    eng.outbox.upsert_contact("telegram", "1", "Mom", touch_inbound=True)
    with eng.outbox._lock:  # stale for weave (900s) but not nudge (3600s)
        eng.outbox._conn.execute(
            "UPDATE contacts SET last_inbound=? WHERE chat_id='1'",
            (time.time() - 1000,))
        eng.outbox._conn.commit()
    eng.companion.bonds.add_reminder("telegram", "me", "call mom",
                                     time.time() - 5)
    eng.tick()
    pend = eng.outbox.pending("telegram")
    assert len(pend) == 1 and pend[0]["to"] == "me"
    assert pend[0]["message"].startswith("⏰")
    mem.close()


def test_tick_group_never_weaves():
    cfg, mem, eng = _engine()
    eng.outbox.upsert_contact("whatsapp", "1@g.us", "Mom Fam",
                              touch_inbound=True)
    eng.companion.bonds.add_reminder("whatsapp", "me", "call mom",
                                     time.time() - 5)
    eng.tick()
    pend = eng.outbox.pending("whatsapp")
    assert len(pend) == 1 and pend[0]["to"] == "me"
    mem.close()


def test_tick_weave_can_disable():
    cfg, mem, eng = _engine(remind_weave=False)
    eng.outbox.upsert_contact("telegram", "1", "Mom", touch_inbound=True)
    eng.companion.bonds.add_reminder("telegram", "me", "call mom",
                                     time.time() - 5)
    eng.tick()
    pend = eng.outbox.pending("telegram")
    assert len(pend) == 1 and pend[0]["message"].startswith("⏰")
    mem.close()


# ---------- snooze ----------
def test_snooze_store_and_route():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        r = _post(base, "/remind", {"action": "add", "channel": "telegram",
                                    "chat_id": "me", "text": "x",
                                    "due_ts": time.time() - 10})
        later = time.time() + 3600
        assert _post(base, "/remind", {"action": "snooze", "id": r["id"],
                                       "due_ts": later})["snoozed"] is True
        lst = _post(base, "/remind", {"action": "list"})["reminders"]
        assert len(lst) == 1 and abs(lst[0]["due_ts"] - later) < 1
        assert _post(base, "/remind", {"action": "snooze", "id": 999,
                                       "due_ts": later})["snoozed"] is False
    finally:
        srv.shutdown()
        mem.close()


def test_owner_snooze():
    mod = _load("owner_snz", "bridges/owner.py")

    async def fake(path, payload=None, timeout=120):
        assert path == "/remind" and payload["action"] == "snooze"
        return {"snoozed": payload["id"] == 5}

    run = lambda a: asyncio.run(mod.run_owner_command(fake, "telegram",
                                                      "snooze", a))
    assert "snoozed" in run("5 in 2h")
    assert "usage" in run("x")
    assert "no such reminder" in run("9 in 2h")


def test_env_coercion_regression(monkeypatch):
    monkeypatch.setenv("GQ_REMIND_WEAVE", "0")
    monkeypatch.setenv("GQ_REMIND_WINDOW", "300")
    monkeypatch.setenv("GQ_ALLOW_EXEC", "0")
    monkeypatch.setenv("GQ_BRIEF_HOUR", "9")
    c = load_config()
    assert c.remind_weave is False and c.remind_window == 300
    assert c.allow_exec is False and c.brief_hour == 9
