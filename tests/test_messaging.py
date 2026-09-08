"""Messaging tests: outbox, contacts, proactive tick, endpoints. Offline."""
import importlib.util
import json
import sqlite3
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.outbox import Outbox, ProactiveEngine, _in_quiet
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore


def _stack(**over):
    tmp = Path(tempfile.mkdtemp())
    kw = dict(offline=True, workspace=str(tmp / "ws"),
              memory_db=str(tmp / "mem.db"), max_workers=2,
              nudge_after=60, nudge_gap=0)
    kw.update(over)
    cfg = GodQuantConfig(**kw)
    mem = MemoryStore(cfg.resolved_memory_db())
    router = LLMRouter(cfg, mem)
    orch = Orchestrator(cfg, router, mem)
    return cfg, mem, router, orch


def _backdate(db: Path, channel: str, chat_id: str, inbound_ago: int):
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE contacts SET last_inbound=? WHERE channel=? AND chat_id=?",
                 (time.time() - inbound_ago, channel, chat_id))
    conn.commit()
    conn.close()


# ---- outbox ----
def test_outbox_roundtrip():
    tmp = Path(tempfile.mkdtemp()) / "m.db"
    ob = Outbox(tmp)
    mid = ob.enqueue("telegram", "me", "hello self")
    pend = ob.pending("telegram")
    assert len(pend) == 1 and pend[0]["id"] == mid
    assert ob.pending("whatsapp") == []
    ob.ack(mid, True)
    assert ob.pending("telegram") == []
    assert ob.stats()["outbox"].get("sent") == 1
    ob.close()


def test_outbox_gives_up_after_5_nacks():
    tmp = Path(tempfile.mkdtemp()) / "m.db"
    ob = Outbox(tmp)
    mid = ob.enqueue("whatsapp", "1@c.us", "x")
    for _ in range(5):
        ob.ack(mid, False)
    assert ob.pending("whatsapp") == []
    assert ob.stats()["outbox"].get("failed") == 1
    ob.close()


# ---- contacts ----
def test_contacts_crud():
    tmp = Path(tempfile.mkdtemp()) / "m.db"
    ob = Outbox(tmp)
    ob.upsert_contact("telegram", "42", "Babe")
    assert len(ob.list_contacts()) == 1
    ob.set_enabled("telegram", "42", False)
    assert ob.list_contacts()[0]["enabled"] is False
    ob.remove("telegram", "42")
    assert ob.list_contacts() == []
    ob.close()


def test_quiet_hours():
    from datetime import datetime
    assert _in_quiet("23-7", datetime(2026, 1, 1, 2)) is True
    assert _in_quiet("23-7", datetime(2026, 1, 1, 12)) is False
    assert _in_quiet("", datetime(2026, 1, 1, 2)) is False
    assert _in_quiet("9-17", datetime(2026, 1, 1, 12)) is True


# ---- proactive ----
def test_tick_texts_silent_contact():
    cfg, mem, router, _ = _stack(nudge_after=60)
    ob = Outbox(cfg.resolved_memory_db())
    ob.upsert_contact("telegram", "7", "Babe")
    _backdate(cfg.resolved_memory_db(), "telegram", "7", inbound_ago=3600)
    queued = ProactiveEngine(cfg, CompanionAgent(cfg, router, mem), ob).tick()
    assert len(queued) == 1 and queued[0]["to"] == "7"
    assert ob.pending("telegram")[0]["message"]
    ob.close()
    mem.close()


def test_tick_skips_fresh_and_quiet():
    cfg, mem, router, _ = _stack(nudge_after=3600)
    ob = Outbox(cfg.resolved_memory_db())
    ob.upsert_contact("telegram", "8", "Fresh")  # just registered → not silent
    eng = ProactiveEngine(cfg, CompanionAgent(cfg, router, mem), ob)
    assert eng.tick() == []
    from datetime import datetime as _dt
    _h = _dt.now().hour  # window covering NOW → time-independent
    cfg.quiet_hours = f"{(_h - 1) % 24}-{(_h + 1) % 24}"
    _backdate(cfg.resolved_memory_db(), "telegram", "8", inbound_ago=99999)
    assert eng.tick() == []
    ob.close()
    mem.close()


# ---- server endpoints ----
def _serve(stack):
    cfg, mem, router, orch = stack
    srv = create_server(cfg, router, mem, orch,
                        CompanionAgent(cfg, router, mem),
                        host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _post(base, path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def test_server_send_outbox_ack_tick():
    stack = _stack(nudge_after=60)
    srv, base = _serve(stack)
    try:
        r = _post(base, "/send", {"channel": "telegram", "to": "me",
                                  "message": "self-text ❤"})
        assert r["queued"] >= 1
        pend = _post(base, "/outbox", {}) if False else json.loads(
            urllib.request.urlopen(base + "/outbox?channel=telegram",
                                   timeout=10).read().decode())["pending"]
        assert any(p["to"] == "me" for p in pend)
        _post(base, "/ack", {"id": r["queued"], "ok": True})
        # contacts + inbound tracking via /chat
        _post(base, "/chat", {"message": "hey", "conversation_id": "tg:9",
                              "channel": "telegram", "chat_id": "9",
                              "display": "Babe"})
        contacts = _post(base, "/contacts", {"channel": "telegram",
                                             "chat_id": "9"})["contacts"]
        assert any(c["chat_id"] == "9" for c in contacts)
        _backdate(stack[0].resolved_memory_db(), "telegram", "9", 7200)
        tick = _post(base, "/tick", {})
        assert any(m["to"] == "9" for m in tick["queued"])
    finally:
        srv.shutdown()
        stack[1].close()


# ---- telegram bridge (no telethon / no network needed) ----
def _load_bridge():
    path = Path(__file__).resolve().parent.parent / "bridges" / "telegram_userbot.py"
    spec = importlib.util.spec_from_file_location("tg_bridge", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_helpers():
    mod = _load_bridge()
    assert mod.allowed(1, None) is True  # empty allowlist
    mod.ALLOW.clear()
    mod.ALLOW.update({"42", "friend"})
    assert mod.allowed(42, None) is True
    assert mod.allowed(7, "Friend") is True
    assert mod.allowed(7, "stranger") is False
    import asyncio
    assert "mission" in asyncio.run(mod.run_owner_cmd("help", ""))
    assert "mission" in asyncio.run(mod.run_owner_cmd("bogus", ""))
