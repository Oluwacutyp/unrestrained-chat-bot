"""v3.7 autonomous mind: reminders, dream, intentions, notes, brief, fetch."""
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
from godquant.companion.dream import build_brief, run_dream
from godquant.companion.outbox import Outbox, ProactiveEngine
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


# ---------- store unit tests ----------
def test_reminder_lifecycle():
    b = BondStore(Path(tempfile.mkdtemp()) / "m.db")
    now = time.time()
    rid = b.add_reminder("telegram", "me", "call mom", now + 3600)
    assert b.due_reminders(now) == []
    assert b.due_reminders(now + 3600)[0]["id"] == rid
    b.fire_reminder(rid, now + 3600)
    assert b.list_reminders() == []
    d = b.add_reminder("telegram", "me", "water", now - 10, repeat="daily")
    b.fire_reminder(d, now)
    nxt = b.list_reminders()
    assert len(nxt) == 1 and nxt[0]["due_ts"] > now  # rolled forward
    assert b.cancel_reminder(d) is True
    assert b.cancel_reminder(999) is False
    b.close()


def test_intentions():
    b = BondStore(Path(tempfile.mkdtemp()) / "m.db")
    b.add_intention("learn drums")
    b.add_intention("check on Zed", kind="checkin", channel="telegram",
                    chat_id="1", display="Zed")
    assert len(b.active_intentions()) == 2
    assert b.resolve_intention(1) is True
    assert b.resolve_intention(1) is False  # already resolved
    assert len(b.active_intentions()) == 1
    b.close()


def test_journal_notes_kv():
    b = BondStore(Path(tempfile.mkdtemp()) / "m.db")
    b.save_journal("telegram", "1", "2026-09-08", "met Zed")
    assert b.get_journal("telegram", "1") == [("2026-09-08", "met Zed")]
    assert b.recent_journal()[0][3] == "met Zed"
    b.save_note("WiFi", "pw is jollof")
    assert b.get_note("wifi") == "pw is jollof"  # case-insensitive
    assert b.list_notes() == ["wifi"]
    assert b.del_note("WIFI") is True and b.get_note("wifi") is None
    assert b.kv_get("k", "d") == "d"
    b.kv_set("k", "v")
    assert b.kv_get("k") == "v"
    b.close()


def test_parse_when():
    mod = _load("owner_mind", "bridges/owner.py")
    now = time.time()
    assert mod.parse_when("in 30m", now) == (now + 1800, "")
    assert mod.parse_when("in 2h", now) == (now + 7200, "")
    assert mod.parse_when("in 3d", now) == (now + 259200, "")
    due, rep = mod.parse_when("every day 08:00", now)
    assert rep == "daily" and 0 < due - now <= 86400
    due2, rep2 = mod.parse_when("tomorrow 07:00", now)
    assert rep2 == "" and due2 > now
    import datetime as _dt
    assert _dt.datetime.fromtimestamp(due2).hour == 7
    assert mod.parse_when("someday", now) is None


# ---------- dream ----------
def test_dream_resolves_conflicts_journals_and_plans():
    from godquant.companion.memory_engine import SINGLE_KEYS
    assert SINGLE_KEYS, "need single-keys to test conflict repair"
    key = sorted(SINGLE_KEYS)[0]
    b = BondStore(Path(tempfile.mkdtemp()) / "m.db")
    b.set_level("telegram", "1", 2)
    b._conn.execute("UPDATE bonds SET updated=? WHERE channel=? AND chat_id=?",
                    (time.time() - 3 * 86400, "telegram", "1"))
    b._conn.commit()
    # legacy conflict: same single-key, two truths (bypasses add_fact replace)
    b._conn.execute(
        "INSERT INTO bond_facts(channel,chat_id,fkey,fvalue,updated) VALUES(?,?,?,?,?)",
        ("telegram", "1", key, "OLD", time.time() - 100))
    b._conn.execute(
        "INSERT INTO bond_facts(channel,chat_id,fkey,fvalue,updated) VALUES(?,?,?,?,?)",
        ("telegram", "1", key, "NEW", time.time()))
    b._conn.commit()
    b.add_fact("telegram", "1", "like", "suya")
    b.add_fact("telegram", "1", "like", "jollof")
    rep = run_dream(b, {"telegram:1": "Zed"})
    assert rep["chats"] == 1 and rep["merged"] == 1
    assert len(rep["conflicts"]) == 1 and "NEW" in rep["conflicts"][0]
    assert (key, "NEW") in b.get_facts("telegram", "1")
    assert (key, "OLD") not in b.get_facts("telegram", "1")
    likes = [v for k, v in b.get_facts("telegram", "1") if k == "like"]
    assert sorted(likes) == ["jollof", "suya"]  # multi-values survive
    day, entry = b.get_journal("telegram", "1")[0]
    assert "Zed" in entry and "L2" in entry
    ints = b.active_intentions()
    assert len(ints) == 1 and ints[0]["kind"] == "checkin"
    assert "Zed" in ints[0]["text"] and ints[0]["chat_id"] == "1"
    assert b.kv_get("last_dream_day") == day
    b.close()


def test_build_brief():
    b = BondStore(Path(tempfile.mkdtemp()) / "m.db")
    b.add_reminder("telegram", "me", "call mom", time.time() + 100)
    b.add_intention("learn drums")
    b.set_level("telegram", "1", 2)
    b.kv_set("dream_report", json.dumps({"chats": 3, "merged": 1,
                                         "intentions": [1]}))
    out = build_brief(b, pending_outbox=2)
    assert "call mom" in out and "learn drums" in out
    assert "1 close bond" in out and "3 chats" in out and "2 queued" in out
    b.close()


# ---------- routes ----------
def test_remind_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        r = _post(base, "/remind", {"action": "add", "channel": "telegram",
                                    "chat_id": "me", "text": "call mom",
                                    "due_ts": time.time() + 60})
        assert r["id"] >= 1
        assert "error" in _post(base, "/remind", {"action": "add"})
        lst = _post(base, "/remind", {"action": "list"})["reminders"]
        assert len(lst) == 1 and lst[0]["text"] == "call mom"
        assert _post(base, "/remind", {"action": "cancel",
                                       "id": r["id"]})["cancelled"] is True
        assert _post(base, "/remind", {"action": "list"})["reminders"] == []
    finally:
        srv.shutdown()
        mem.close()


def test_want_mind_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        assert "error" in _post(base, "/want", {"text": "  "})
        iid = _post(base, "/want", {"text": "learn drums"})["id"]
        with urllib.request.urlopen(base + "/mind", timeout=10) as r:
            mind = json.loads(r.read().decode())
        assert len(mind["intentions"]) == 1 and "stats" in mind
        assert _post(base, "/mind", {"action": "done",
                                     "id": iid})["ok"] is True
        with urllib.request.urlopen(base + "/mind", timeout=10) as r:
            assert json.loads(r.read().decode())["intentions"] == []
        assert "error" in _post(base, "/mind", {"action": "bogus", "id": 1})
    finally:
        srv.shutdown()
        mem.close()


def test_dream_journal_brief_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        _post(base, "/chat", {"message": "i love hanging out with you",
                              "conversation_id": "telegram:7",
                              "channel": "telegram", "chat_id": "7"})
        rep = _post(base, "/dream", {})
        assert rep["chats"] >= 1 and rep["journal"] >= 1
        with urllib.request.urlopen(
                base + "/journal?channel=telegram&chat_id=7&limit=3",
                timeout=10) as r:
            es = json.loads(r.read().decode())["entries"]
        assert len(es) == 1 and "bond L" in es[0]["entry"]
        with urllib.request.urlopen(base + "/journal?recent=5",
                                    timeout=10) as r:
            assert json.loads(r.read().decode())["entries"]
        with urllib.request.urlopen(base + "/brief", timeout=10) as r:
            assert "brief" in json.loads(r.read().decode())["brief"]
    finally:
        srv.shutdown()
        mem.close()


def test_note_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        assert _post(base, "/note", {"action": "save", "name": "wifi",
                                     "body": "pw"})["saved"] == "wifi"
        assert _post(base, "/note", {"action": "get",
                                     "name": "wifi"})["body"] == "pw"
        assert "error" in _post(base, "/note", {"action": "get",
                                                "name": "nope"})
        assert _post(base, "/note", {"action": "list"})["notes"] == ["wifi"]
        assert _post(base, "/note", {"action": "del",
                                     "name": "wifi"})["deleted"] is True
    finally:
        srv.shutdown()
        mem.close()


def test_fetch_route():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        html = Path(tempfile.mkdtemp()) / "p.html"
        html.write_text("<html><head><title>Hi T</title></head><body>"
                        "<p>hello world</p><script>var x=1;</script></body></html>")
        r = _post(base, "/fetch", {"url": html.as_uri()})
        assert r["title"] == "Hi T" and "hello world" in r["text"]
        assert "var x" not in r["text"]  # scripts stripped
        assert "error" in _post(base, "/fetch", {"url": "gopher://x"})
    finally:
        srv.shutdown()
        mem.close()


def test_facts_never_cross_chats():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        _post(base, "/import", {"channel": "telegram", "chat_id": "A",
                                "messages": [{"role": "user",
                                              "text": "call me Mary"}]})
        with urllib.request.urlopen(base + "/memory?channel=telegram&chat_id=A",
                                    timeout=10) as r:
            a = json.loads(r.read().decode())["facts"]
        assert any(f["value"] == "Mary" for f in a)
        with urllib.request.urlopen(base + "/memory?channel=telegram&chat_id=B",
                                    timeout=10) as r:
            assert json.loads(r.read().decode())["facts"] == []
    finally:
        srv.shutdown()
        mem.close()


# ---------- tick engine ----------
def test_tick_fires_reminders():
    cfg, mem, eng = _engine()
    eng.companion.bonds.add_reminder("telegram", "me", "call mom",
                                     time.time() - 5)
    q = eng.tick()
    assert any("call mom" in m["message"] for m in q)
    pend = eng.outbox.pending("telegram")
    assert len(pend) == 1 and pend[0]["message"].startswith("⏰")
    assert eng.companion.bonds.list_reminders() == []
    mem.close()


def test_tick_dreams_once_daily():
    cfg, mem, eng = _engine()
    eng.tick()
    import datetime as _dt
    assert eng.companion.bonds.kv_get("last_dream_day") == \
        _dt.datetime.now().strftime("%Y-%m-%d")
    eng.tick()  # second pass: no crash, no duplicate
    mem.close()


def test_tick_brief_gated_and_once():
    cfg, mem, eng = _engine(brief_to="telegram:me", brief_hour=0)
    q1 = eng.tick()
    assert any("brief" in m["message"] for m in q1)
    assert eng.outbox.pending("telegram"), "brief must queue"
    n = len(eng.outbox.pending("telegram"))
    eng.tick()
    assert len(eng.outbox.pending("telegram")) == n  # not re-sent
    mem.close()
    _, mem2, eng2 = _engine()  # no brief_to → silence
    assert not any("brief" in m["message"] for m in eng2.tick())
    mem2.close()


def test_tick_acts_on_checkin_skips_custom():
    cfg, mem, eng = _engine()
    eng.companion.bonds.add_intention("learn drums")  # untargeted
    eng.companion.bonds.add_intention("check on Zed", kind="checkin",
                                      channel="telegram", chat_id="1",
                                      display="Zed")
    eng.tick()
    pend = eng.outbox.pending("telegram")
    assert len(pend) == 1 and pend[0]["to"] == "1"
    left = eng.companion.bonds.active_intentions()
    assert len(left) == 1 and left[0]["text"] == "learn drums"
    mem.close()


# ---------- owner core ----------
def _fake_mind_api():
    async def fake(path, payload=None, timeout=120):
        if path == "/remind" and (payload or {}).get("action") == "add":
            return {"id": 5}
        if path == "/remind":
            if (payload or {}).get("action") == "cancel":
                return {"cancelled": payload.get("id") == 5}
            return {"reminders": [{"id": 5, "channel": "telegram",
                                   "chat_id": "me", "text": "call mom",
                                   "repeat": "", "due_ts": 2000000000}]}
        if path == "/want":
            return {"id": 9}
        if path == "/mind":
            return {"ok": True} if payload else {
                "intentions": [{"id": 9, "kind": "custom",
                                "text": "learn drums"}],
                "reminders": [], "dream": {"chats": 2, "merged": 1}}
        if path.startswith("/journal"):
            return {"entries": [{"day": "2026-09-08", "entry": "met Zed"}]}
        if path == "/note" and payload["action"] == "save":
            return {"saved": payload["name"]}
        if path == "/note" and payload["action"] == "get":
            return {"body": "pw is jollof"} if payload["name"] == "wifi" \
                else {"error": "x"}
        if path == "/note":
            return {"notes": ["wifi"]} if payload["action"] == "list" \
                else {"deleted": True}
        if path == "/dream":
            return {"chats": 2, "merged": 1, "journal": 2, "intentions": [1],
                    "conflicts": ["telegram:1 city → Abuja"]}
        if path == "/brief":
            return {"brief": "☀️ brief — today"}
        if path == "/fetch":
            return {"error": "nope"} if payload["url"] == "bad" \
                else {"title": "T", "text": "hello world"}
        raise AssertionError(path)
    return fake


def _run(cmd, arg, chat="me"):
    mod = _load("owner_mind2", "bridges/owner.py")
    return asyncio.run(mod.run_owner_command(_fake_mind_api(), "telegram",
                                             cmd, arg, chat))


def test_owner_mind_commands():
    assert "#5" in _run("remind", "in 2h call mom")
    assert "usage" in _run("remind", "someday call mom")
    assert "call mom" in _run("reminders", "")
    assert _run("cancel", "5") == "cancelled ✅"
    assert "usage" in _run("cancel", "x")
    assert "#9" in _run("want", "learn drums")
    assert "usage" in _run("want", "")
    assert "learn drums" in _run("mind", "")
    assert _run("mind", "done 9") == "updated ✅"
    assert "met Zed" in _run("journal", "")
    assert "[wifi]" in _run("note", "save wifi pw is jollof")
    assert "jollof" in _run("note", "wifi")
    assert "wifi" in _run("note", "list")
    assert "usage" in _run("note", "save wifi")
    assert "conflicts" in _run("dream", "")
    assert "brief" in _run("brief", "")
    assert "hello" in _run("fetch", "https://x")
    assert "usage" in _run("fetch", "")
