"""v4.0 unified memory: Mind recall, scopes, episodes, consolidation, ops."""
import asyncio
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
from godquant.companion.dream import consolidate_global
from godquant.companion.relationship import BondStore
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.bm25 import bm25_scores, tokenize, trigram_sim
from godquant.memory.mind import Mind
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


def _mind():
    tmp = Path(tempfile.mkdtemp())
    mem = MemoryStore(tmp / "mem.db")
    bonds = BondStore(tmp / "bonds.db")
    return mem, bonds, Mind(mem, bonds)


# ---------- retrieval math ----------
def test_bm25_ranks_relevant_first():
    docs = [tokenize("the quick brown fox"),
            tokenize("quantum chromodynamics lecture notes"),
            tokenize("fox hunting regulations")]
    s = bm25_scores(tokenize("quantum physics"), docs)
    assert s[1] > s[0] and s[1] > s[2]
    assert bm25_scores([], docs) == [0.0, 0.0, 0.0]
    assert trigram_sim("hello", "hello") == 1.0
    assert trigram_sim("kitten", "sitting") < 0.5
    assert trigram_sim("", "x") == 0.0


# ---------- recall + scopes ----------
def test_recall_finds_and_isolates():
    mem, bonds, mind = _mind()
    mind.remember("mom's birthday is june 4th", scope="telegram:1",
                  importance=0.9)
    mind.remember("deploy uses blue-green strategy", scope="global")
    mind.remember("Zed owes me fifty bucks", scope="telegram:2")
    hits = mind.recall("birthday mom", scope="telegram:1")
    assert hits and "june" in hits[0]["content"]
    assert not any("june" in h["content"]
                   for h in mind.recall("birthday", scope="telegram:2"))
    assert any("blue-green" in h["content"]
               for h in mind.recall("deploy", scope="telegram:2"))
    assert any("fifty" in h["content"]
               for h in mind.recall("bucks", scope="*"))
    assert mem.get(hits[0]["id"]).access_count >= 1  # recall strengthens
    assert mind.recall("", scope="*") == []
    assert mind.recall_block("birthday mom", scope="telegram:1").startswith(
        "\n[MEMORY")
    assert mind.recall_block("zzz no such thing", scope="*") == ""
    mem.close()
    bonds.close()


def test_memory_ops():
    mem, bonds, mind = _mind()
    mid = mind.remember("vault code is 1234", importance=0.9)
    assert mind.pin(mid) is True
    assert mind.recall("vault", scope="*")[0]["pinned"] is True
    assert mind.edit(mid, "vault code is 5678") is True
    assert "5678" in mind.recall("vault", scope="*")[0]["content"]
    assert any(m["id"] == mid for m in mind.inspect())
    assert mind.forget(mid) is True
    assert mind.recall("vault", scope="*") == []
    assert mind.forget(999999) is False
    mem.close()
    bonds.close()


def test_episodes_and_working():
    mem, bonds, mind = _mind()
    mind.log_episode("telegram:1", "Zed", "got promoted, cried happy tears", 0.9)
    mind.log_episode("telegram:2", "Mom", "called about sunday rice", 0.4)
    assert len(mind.episodes("telegram:1")) == 1
    assert len(mind.episodes()) == 2
    mem.wset("draft", "hello", ttl_s=100)
    assert mem.wget("draft") == "hello"
    mem.wset("gone", "x", ttl_s=-1)
    assert mem.wget("gone") == ""
    mem.close()
    bonds.close()


# ---------- consolidation ----------
def test_consolidate_decays_prunes_merges_links():
    mem, bonds, mind = _mind()
    now = time.time()
    old = mem.add("fact", "trivial old thing", importance=0.1)
    mem._conn.execute("UPDATE memories SET created=? WHERE id=?",
                      (now - 40 * 86400, old))
    mem._conn.commit()
    keep = mem.add("fact", "pinned old thing", importance=0.1, pinned=True)
    mem._conn.execute("UPDATE memories SET created=? WHERE id=?",
                      (now - 40 * 86400, keep))
    mem._conn.commit()
    aging = mem.add("fact", "fading unaccessed thing", importance=0.8)
    mem._conn.execute("UPDATE memories SET created=? WHERE id=?",
                      (now - 8 * 86400, aging))
    mem._conn.commit()
    mind.remember("the vault code is 1234")
    mind.remember("the vault code is 1234!")
    bonds.set_level("telegram", "1", 2)
    bonds.set_level("whatsapp", "2", 2)
    bonds.add_fact("telegram", "1", "name", "Zed")
    bonds.add_fact("whatsapp", "2", "name", "Zed")
    rep = consolidate_global(mind, now)
    assert old in rep["pruned"] and mem.get(old) is None
    assert mem.get(keep) is not None  # pinned = immortal
    assert mem.get(aging).importance < 0.8  # decayed
    assert len(rep["merged"]) == 1
    assert any("zed" in link for link in rep["links"])
    mem.close()
    bonds.close()


# ---------- migration ----------
def test_old_db_migrates():
    tmp = Path(tempfile.mkdtemp())
    mp = tmp / "old.db"
    c = sqlite3.connect(str(mp))
    c.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, kind TEXT, "
              "content TEXT, tags TEXT, score REAL, created REAL)")
    c.execute("INSERT INTO memories(kind,content,created) VALUES(?,?,?)",
              ("fact", "legacy fact", time.time()))
    c.commit()
    c.close()
    mem = MemoryStore(mp)
    assert mem.candidates()[0].content == "legacy fact"
    assert mem.candidates()[0].layer == "semantic"  # default backfilled
    mem.add("fact", "new fact")
    assert len(mem.candidates()) == 2
    mem.close()
    bp = tmp / "oldb.db"
    c = sqlite3.connect(str(bp))
    c.execute("CREATE TABLE bond_facts(channel TEXT, chat_id TEXT, fkey TEXT, "
              "fvalue TEXT, updated REAL, "
              "PRIMARY KEY (channel, chat_id, fkey, fvalue))")
    c.execute("INSERT INTO bond_facts VALUES(?,?,?,?,?)",
              ("telegram", "1", "name", "Zed", time.time()))
    c.commit()
    c.close()
    bonds = BondStore(bp)
    assert bonds.get_facts("telegram", "1") == [("name", "Zed")]
    assert bonds.facts_meta("telegram", "1")[0]["importance"] == 0.5
    bonds.close()


# ---------- chat path + routes ----------
def test_chat_logs_episode():
    cfg, mem = _stack()
    agent = CompanionAgent(cfg, LLMRouter(cfg, mem), mem)
    agent.chat("i just got promoted to senior engineer at first bank and i am "
               "so incredibly proud and happy i cried happy tears when the "
               "director told me!!", cid="telegram:9", bond_id="telegram:9")
    eps = agent.mind.episodes("telegram:9")
    assert len(eps) >= 1 and "promoted" in eps[0]["what"]
    mem.close()


def test_recall_and_memories_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        mem.add("fact", "quantum batteries use quantum entanglement")
        with urllib.request.urlopen(base + "/recall?q=quantum+batteries&scope=*",
                                    timeout=10) as r:
            hits = json.loads(r.read().decode())["hits"]
        assert hits and "entanglement" in hits[0]["content"]
        mid = hits[0]["id"]
        with urllib.request.urlopen(base + "/memories?limit=50",
                                    timeout=10) as r:
            assert any(m["id"] == mid
                       for m in json.loads(r.read().decode())["memories"])
        assert _post(base, "/memories", {"action": "pin",
                                         "id": mid})["ok"] is True
        assert _post(base, "/memories", {"action": "edit", "id": mid,
                                         "content": "edited!"})["ok"] is True
        assert _post(base, "/memories", {"action": "delete",
                                         "id": mid})["ok"] is True
        assert "error" in _post(base, "/memories", {"action": "bogus",
                                                    "id": mid})
    finally:
        srv.shutdown()
        mem.close()


# ---------- owner commands ----------
def test_owner_recall_mem():
    mod = _load("owner_mem", "bridges/owner.py")

    async def fake(path, payload=None, timeout=120):
        if path.startswith("/recall"):
            return {"hits": [{"id": 3, "layer": "semantic", "scope": "global",
                               "content": "vault code", "pinned": True}]}
        if path.startswith("/memories?"):
            return {"memories": [{"id": 3, "layer": "semantic",
                                   "scope": "global", "content": "vault code",
                                   "pinned": False}]}
        if path == "/memories":
            return {"ok": payload["id"] == 3}
        raise AssertionError(path)

    run = lambda c, a: asyncio.run(mod.run_owner_command(fake, "telegram",
                                                         c, a))
    assert "#3" in run("recall", "vault") and "📌" in run("recall", "vault")
    assert "usage" in run("recall", "")
    assert "#3" in run("mem", "") and "#3" in run("mem", "list")
    assert run("mem", "pin 3") == "done ✅"
    assert run("mem", "del 9") == "no such memory"
    assert run("mem", "edit 3 new text") == "edited ✅"
    assert "usage" in run("mem", "frobnicate")
