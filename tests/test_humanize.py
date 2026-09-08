"""v3.2 humanizer tests: delays, bubbles, limiter, bonds, group-skip. Offline."""
import asyncio
import json
import random
import sqlite3
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.humanize import (RateLimiter, bubble_gap, read_delay,
                                        split_bubbles, typing_delay)
from godquant.companion.outbox import Outbox, ProactiveEngine
from godquant.companion.relationship import (BondStore, addressing,
                                            parse_bond_id)
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
    return cfg, mem, LLMRouter(cfg, mem), Orchestrator(cfg, LLMRouter(cfg, mem), mem)


# ---- delay math ----
def test_read_delay_bounds():
    assert 1.0 <= read_delay(len("hi")) <= 7.0
    assert read_delay(10000) == 7.0
    # same draw → longer text strictly longer pause
    assert read_delay(1500, random.Random(1)) > read_delay(2, random.Random(1))


def test_typing_delay_scales_and_caps():
    short, long = typing_delay(len("hey")), typing_delay(len("hey " * 200))
    assert 0 < short < long <= 22.0
    assert typing_delay(10000) == 22.0


def test_bubble_gap_range():
    for _ in range(20):
        assert 0.8 <= bubble_gap() <= 2.2


def test_split_bubbles():
    assert split_bubbles("") == []
    assert split_bubbles("hey babe") == ["hey babe"]
    assert split_bubbles("one\n\ntwo\n\nthree\n\nfour") == ["one", "two", "three"]
    long = "word " * 200  # 1000 chars, no paragraphs → sentence/word split
    parts = split_bubbles(long)
    assert 1 < len(parts) <= 3
    assert "".join(parts).replace(" ", "") == long.replace(" ", "")


# ---- limiter ----
def test_limiter_allows_then_paces():
    lim = RateLimiter(max_per_min=3, min_chat_gap=60)
    now = time.time()
    assert lim.wait_time("a", now) == 0
    lim._hits.append(now)
    lim._last["a"] = now
    assert lim.wait_time("a", now) == 60  # per-chat gap dominates
    for _ in range(3):  # blow the global budget too
        lim._hits.append(now)
    assert lim.wait_time("b", now + 1) >= 59  # global window dominates
    assert lim.wait_time("b", now + 120) == 0  # window slides, all clear


def test_limiter_wait_under_budget_is_instant():
    lim = RateLimiter(max_per_min=100, min_chat_gap=0)
    t0 = time.time()
    asyncio.run(lim.wait("z"))
    assert time.time() - t0 < 1.0


# ---- bonds ----
def _bonds():
    return BondStore(Path(tempfile.mkdtemp()) / "b.db")


def test_bond_thresholds_and_signals():
    b = _bonds()
    assert b.get("tg", "1")["level"] == 0
    for _ in range(4):
        b.note_message("tg", "1", "hey")
    assert b.get("tg", "1")["level"] == 0  # 4 < 5
    assert b.note_message("tg", "1", "hey")["level"] == 1  # 5th msg
    for _ in range(15):
        b.note_message("tg", "1", "hey")
    assert b.get("tg", "1")["level"] == 2  # 20 msgs
    for _ in range(40):
        b.note_message("tg", "1", "hey")
    assert b.get("tg", "1")["level"] == 3  # 60 msgs
    r = b.note_message("tg", "new", "i love you so much")
    assert r["level"] == 1 and r["count"] == 1  # romantic fast-tracks L1
    b.close()


def test_bond_manual_pin_survives_traffic():
    b = _bonds()
    b.set_level("tg", "9", 3)
    for _ in range(70):
        bond = b.note_message("tg", "9", "hey")
    assert bond["level"] == 3 and bond["manual"] is True
    assert b.get("tg", "9")["count"] == 70
    b.close()


def test_parse_bond_id():
    assert parse_bond_id("tg:42") == ("tg", "42")
    assert parse_bond_id("me") == ("telegram", "me")


def test_addressing_gates_pet_names_and_caps_groups():
    l0 = addressing(0, "Mary", False)
    assert "Mary" in l0 and "NEVER use pet names" in l0
    assert "NO pet names yet" in addressing(1, "Mary", False)
    assert "ok SPARINGLY" in addressing(2, "Mary", False)
    assert "Full romance" in addressing(3, "Mary", False)
    g3 = addressing(3, "Mary", True)  # groups cap effective L1
    assert "GROUP CHAT" in g3 and "no flirting" in g3
    assert "Full romance" not in g3


# ---- server: /bond + chat bond fields ----
def _serve(cfg, router, mem, orch):
    srv = create_server(cfg, router, mem, orch,
                        CompanionAgent(cfg, router, mem),
                        host="127.0.0.1", port=0)
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


def test_server_bond_roundtrip_and_chat_fields():
    cfg, mem, router, orch = _stack()
    srv, base = _serve(cfg, router, mem, orch)
    try:
        out = _post(base, "/chat", {"message": "hey babe i miss you",
                                    "conversation_id": "tg:42", "channel": "telegram",
                                    "chat_id": "42", "display": "Babe",
                                    "sender_name": "Babe", "is_group": False,
                                    "bond_id": "telegram:42", "persona": "alex"})
        assert out["response"] and out["bond"] == 1  # romantic → L1
        assert out["bond_count"] == 1
        assert "_neutral" not in out["response"]  # no footer from server
        with urllib.request.urlopen(base + "/bond?channel=telegram&chat_id=42",
                                    timeout=10) as r:
            assert json.loads(r.read().decode())["bond"]["level"] == 1
        pinned = _post(base, "/bond", {"channel": "telegram", "chat_id": "42",
                                       "level": 3})
        assert pinned["bond"]["level"] == 3 and pinned["bond"]["manual"] is True
        bad = _post(base, "/bond", {"channel": "telegram", "chat_id": "42",
                                    "level": 9})
        assert "error" in bad
    finally:
        srv.shutdown()
        mem.close()


def test_tick_skips_groups_but_texts_dm():
    cfg, mem, router, orch = _stack(nudge_after=60)
    ob = Outbox(cfg.resolved_memory_db())
    ob.upsert_contact("telegram", "-1001", "My Group")
    ob.upsert_contact("telegram", "7", "Babe")
    conn = sqlite3.connect(str(cfg.resolved_memory_db()))
    conn.execute("UPDATE contacts SET last_inbound=?",
                 (time.time() - 3600,))
    conn.commit()
    conn.close()
    queued = ProactiveEngine(cfg, CompanionAgent(cfg, router, mem), ob).tick()
    assert [q["to"] for q in queued] == ["7"]  # group skipped, DM texted
    ob.close()
    mem.close()
