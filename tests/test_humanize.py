"""v3.3 god-level social brain: dynamic bonding, friction, vibe, typos."""
import asyncio
import datetime as dt
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
from godquant.companion.humanize import (ExchangeTracker, RateLimiter,
                                        bubble_gap, plan_typos, read_delay,
                                        split_bubbles, typing_delay)
from godquant.companion.outbox import Outbox, ProactiveEngine
from godquant.companion.relationship import (BondStore, addressing,
                                            level_for_score, parse_bond_id,
                                            substance, vibe_context, warmth)
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


def _bonds():
    return BondStore(Path(tempfile.mkdtemp()) / "b.db")


CARE = ("hey how are you doing today? "
        "i was thinking about our chat yesterday and it honestly made my "
        "whole day better. tell me everything, i want to hear it all. ") * 2


# ---- delay math ----
def test_read_delay_bounds_and_depth():
    assert 1.0 <= read_delay(len("hi")) <= 9.0
    assert read_delay(10000) == 9.0
    assert read_delay(1500, 0.0, random.Random(1)) > \
        read_delay(2, 0.0, random.Random(1))
    assert read_delay(100, 1.0, random.Random(1)) > \
        read_delay(100, 0.0, random.Random(1))  # heavy texts absorb slower


def test_typing_delay_scales_caps_and_feels_mood():
    short, long = typing_delay(len("hey")), typing_delay(len("hey " * 200))
    assert 0 < short < long <= 22.0
    assert typing_delay(10000) == 22.0
    assert typing_delay(20, mood="excited", rng=random.Random(0)) < \
        typing_delay(20, rng=random.Random(0)) < \
        typing_delay(20, mood="sad", rng=random.Random(0))
    assert typing_delay(20, energy="rapid", rng=random.Random(0)) < \
        typing_delay(20, rng=random.Random(0))


def test_bubble_gap_range():
    for _ in range(20):
        assert 0.8 <= bubble_gap() <= 2.2


def test_split_bubbles():
    assert split_bubbles("") == []
    assert split_bubbles("hey babe") == ["hey babe"]
    assert split_bubbles("one\n\ntwo\n\nthree\n\nfour") == ["one", "two", "three"]
    long = "word " * 200
    parts = split_bubbles(long)
    assert 1 < len(parts) <= 3
    assert "".join(parts).replace(" ", "") == long.replace(" ", "")


def test_plan_typos():
    long = ("i really think we should talk about what happened yesterday "
            "because it has been on my mind all day")
    out = plan_typos([long], p=1.0, rng=random.Random(7))
    assert len(out) == 2 and out[0] != long
    assert out[1].startswith("*") and out[1][1:] in long  # *correction
    assert plan_typos(["hey babe"], p=1.0, rng=random.Random(0)) == ["hey babe"]
    assert plan_typos([long], p=0.0, rng=random.Random(0)) == [long]


def test_exchange_tracker_spaces_out_once():
    t = ExchangeTracker(rapid_n=3, rapid_window=600)
    assert t.note("a", 0) == 0 and t.note("a", 60) == 0
    pause = t.note("a", 120)
    assert 40.0 <= pause <= 100.0
    assert t.note("a", 180) == 0  # counter reset after spacing out
    slow = ExchangeTracker(rapid_n=3, rapid_window=600)
    assert [slow.note("b", i * 700) for i in range(4)] == [0, 0, 0, 0]
    assert slow.gap("missing") is None
    slow.note("c", 100.0)
    assert slow.gap("c", 150.0) == 50.0


# ---- limiter ----
def test_limiter_allows_then_paces():
    lim = RateLimiter(max_per_min=3, min_chat_gap=60)
    now = time.time()
    assert lim.wait_time("a", now) == 0
    lim._hits.append(now)
    lim._last["a"] = now
    assert lim.wait_time("a", now) == 60
    for _ in range(3):
        lim._hits.append(now)
    assert lim.wait_time("b", now + 1) >= 59
    assert lim.wait_time("b", now + 120) == 0


def test_limiter_wait_under_budget_is_instant():
    lim = RateLimiter(max_per_min=100, min_chat_gap=0)
    t0 = time.time()
    asyncio.run(lim.wait("z"))
    assert time.time() - t0 < 1.0


# ---- substance / warmth ----
def test_substance_and_warmth():
    assert substance("lol") < 0.08 and substance("k") < 0.08
    assert substance("😂") < 0.08
    assert substance(CARE) > 0.8
    assert warmth("i love you so much") == 1.0
    assert warmth("how are you today?") == 0.6
    assert warmth("shut up, you're stupid") == -1.0
    assert warmth("whatever, don't care") == -0.5
    assert warmth("the weather is nice") == 0.0


def test_level_hysteresis():
    assert level_for_score(50, 0) == 2  # climbs fast
    assert level_for_score(33, 2) == 2  # holds (down-threshold 32)
    assert level_for_score(31, 2) == 1  # ...until it really slips
    assert level_for_score(5, 1) == 0


# ---- dynamic bonds ----
def test_twenty_meaningless_msgs_earn_nothing():
    b = _bonds()
    t0 = time.time()
    for i in range(20):  # spaced-out "lol"s
        r = b.note_message("tg", "1", "lol", now=t0 + i * 120)
    assert r["score"] < 5 and r["level"] == 0
    for i in range(20):  # rapid-fire "lol"s — cheaper + annoying
        r = b.note_message("tg", "2", "lol", now=t0 + i * 5)
    assert r["score"] < 5 and r["level"] == 0 and r["friction"] >= 90
    b.close()


def test_substantive_warmth_climbs_fast():
    b = _bonds()
    t0 = time.time()
    for i in range(4):
        r = b.note_message("tg", "1", CARE, now=t0 + i * 120)
    assert r["level"] == 2 and r["score"] > 40 and r["deep"] == 4
    b.close()


def test_romantic_first_text_fast_tracks_l1():
    b = _bonds()
    r = b.note_message("tg", "new", "i miss you so much")
    assert r["level"] == 1 and r["score"] >= 15 and r["count"] == 1
    b.close()


def test_insults_build_friction_and_cost_closeness():
    b = _bonds()
    t0 = time.time()
    for i in range(3):
        r = b.note_message("tg", "9", "you're stupid, shut up",
                           now=t0 + i * 120)
    assert r["friction"] >= 55 and r["score"] < 5 and r["level"] == 0
    assert "FED UP" in addressing(0, "Zed", False, r)
    cooler = b.note_message("tg", "9", "sorry, my bad, i didn't mean it",
                            now=t0 + 400)
    assert cooler["friction"] < r["friction"]  # apologies cool things down
    b.close()


def test_silence_decays_with_hysteresis():
    b = _bonds()
    t0 = time.time()
    for i in range(4):
        b.note_message("tg", "1", CARE, now=t0 + i * 120)
    assert b.get("tg", "1", now=t0 + 2 * 86400)["level"] == 2  # holds
    assert b.get("tg", "1", now=t0 + 7 * 86400)["level"] == 1  # slips
    b.close()


def test_streaks_count_consecutive_days():
    b = _bonds()
    noon = dt.datetime.combine(dt.date.today(),
                               dt.time(12, 0)).timestamp()
    days = [b.note_message("tg", "1", "hey how are you",
                           now=noon + d * 86400)["streak"] for d in (0, 1, 2)]
    assert days == [1, 2, 3]
    assert b.note_message("tg", "1", "hey", now=noon + 5 * 86400)["streak"] == 1
    assert "3 days straight" in addressing(
        1, "Mary", False, {"friction": 0, "streak": 3})
    b.close()


def test_manual_pin_and_auto_resume():
    b = _bonds()
    t0 = time.time()
    assert b.set_level("tg", "9", 3)["score"] == 70.0
    for i in range(70):  # spam can't move a pinned bond's level
        r = b.note_message("tg", "9", "lol", now=t0 + i * 5)
    assert (r["level"], r["manual"]) == (3, True)
    back = b.set_level("tg", "9", None)
    assert back["manual"] is False and back["level"] == 3  # resumes from 70+
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
    g3 = addressing(3, "Mary", True)
    assert "GROUP CHAT" in g3 and "no flirting" in g3
    assert "Full romance" not in g3


def test_vibe_context():
    assert "rapid-fire" in vibe_context("hey", 30)
    assert "reappeared" in vibe_context("hey", 7 * 3600)
    assert "EXCITED" in vibe_context("OMG YES!!!", 500)
    assert "UPSET" in vibe_context("i feel so sad and heartbroken", 500)
    assert "HOSTILE" in vibe_context("shut up idiot", 500)
    night = dt.datetime(2024, 8, 22, 3, 0).timestamp()
    assert "3am" in vibe_context("hey", 500, now_ts=night)
    noon = dt.datetime(2024, 8, 22, 12, 0).timestamp()
    assert vibe_context("ok", 500, now_ts=noon) == ""


# ---- server ----
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
        assert out["bond_score"] >= 15 and out["bond_count"] == 1
        assert "_neutral" not in out["response"]
        with urllib.request.urlopen(base + "/bond?channel=telegram&chat_id=42",
                                    timeout=10) as r:
            got = json.loads(r.read().decode())["bond"]
        assert got["level"] == 1 and got["score"] >= 15
        pinned = _post(base, "/bond", {"channel": "telegram", "chat_id": "42",
                                       "level": 3})
        assert pinned["bond"]["level"] == 3 and pinned["bond"]["manual"] is True
        resumed = _post(base, "/bond", {"channel": "telegram", "chat_id": "42",
                                        "level": "auto"})
        assert resumed["bond"]["manual"] is False
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
    assert [q["to"] for q in queued] == ["7"]
    ob.close()
    mem.close()
