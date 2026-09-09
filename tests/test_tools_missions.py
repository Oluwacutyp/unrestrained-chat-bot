"""v4.1 tools + missions v2: registry, tool loop, waves, projects."""
import asyncio
import importlib.util
import json
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from godquant.agents.base import BaseAgent
from godquant.agents.missions import MissionStore
from godquant.agents.orchestrator import Orchestrator, _waves
from godquant.companion.companion import CompanionAgent
from godquant.companion.outbox import Outbox, ProactiveEngine
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.tools.registry import run_tool, tool_list

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


def _ctx(cfg, mem):
    return {"cfg": cfg, "memory": mem, "mind": None,
            "allow_dangerous": False, "repo": "."}


# ---------- registry ----------
def test_tool_levels_and_audit():
    cfg, mem = _stack()
    ctx = _ctx(cfg, mem)
    assert "py_run" in tool_list() and "shell" in tool_list(["shell"])
    assert run_tool("nope", {}, ctx)["ok"] is False
    r = run_tool("shell", {"cmd": "echo hi"}, ctx)
    assert r["ok"] is False and "disabled" in r["error"]
    ctx["allow_dangerous"] = True
    r = run_tool("shell", {"cmd": "echo hi"}, ctx)
    assert r["ok"] is True and "hi" in r["output"]
    assert mem.search("shell", kind="tool"), "calls must audit-log"
    mem.close()


def test_fs_jail_py_run_fetch():
    cfg, mem = _stack()
    ctx = _ctx(cfg, mem)
    assert run_tool("fs_write", {"path": "sub/a.txt", "content": "hello"},
                    ctx)["ok"] is True
    assert run_tool("fs_read", {"path": "sub/a.txt"}, ctx)["output"] == "hello"
    assert run_tool("fs_write", {"path": "../evil.txt", "content": "x"},
                    ctx)["ok"] is False
    r = run_tool("py_run", {"code": "print(40+2)"}, ctx)
    assert r["ok"] and "42" in r["output"]
    html = Path(tempfile.mkdtemp()) / "p.html"
    html.write_text("<html><head><title>T</title></head>"
                    "<body><p>hi</p></body></html>")
    r = run_tool("web_fetch", {"url": html.as_uri()}, ctx)
    assert r["ok"] and "TITLE: T" in r["output"] and "hi" in r["output"]
    mem.close()


def test_ask_with_tools():
    cfg, mem = _stack()
    agent = BaseAgent(cfg, LLMRouter(cfg, mem), mem)
    out = agent.ask_with_tools("hello there", ["py_run"])  # offline: direct
    assert isinstance(out, str) and out

    class Resp:
        def __init__(self, t):
            self.text = t

    class StubRouter:
        def __init__(self):
            self.n = 0

        def complete(self, sys, user, agent="x", images=None):
            self.n += 1
            if self.n == 1:
                return Resp('{"tool": "py_run", "args": '
                            '{"code": "print(6*7)"}}')
            return Resp("FINAL: the answer is 42")

    agent2 = BaseAgent(cfg, StubRouter(), mem)
    assert agent2.ask_with_tools("compute", ["py_run"]) == "the answer is 42"
    assert mem.search("py_run", kind="tool")
    assert agent2.ask_with_tools("x", ["nope"])  # unknown tools → plain ask
    mem.close()


# ---------- waves + missions ----------
def test_waves():
    assert _waves([[], [0], [0], [1, 2]]) == [[0], [1, 2], [3]]
    assert _waves([[], [99], [-1]]) == [[0, 1, 2]]
    assert _waves([[1], [0]]) == [[0, 1]]


def test_mission_crud_and_run_offline():
    cfg, mem = _stack()
    orch = Orchestrator(cfg, LLMRouter(cfg, mem), mem)
    store = MissionStore(cfg.resolved_memory_db())
    m = store.create("test goal", [{"agent": "researcher", "instruction": "x",
                                    "depends_on": []}], {"k": 1}, "telegram:me")
    assert m["status"] == "running" and m["report_to"] == "telegram:me"
    store.save_step(m["id"], 0, "ok", "did x")
    assert store.get(m["id"])["steps"][0]["status"] == "ok"
    r = orch.run_mission("research lizards", {}, "telegram:me", _store=store)
    assert r["status"] == "done" and "researcher" in r["summary"].lower()
    got = store.get(r["id"])
    assert got["status"] == "done" and all(s["status"] == "ok"
                                           for s in got["steps"])
    assert orch.resume_mission(r["id"])["status"] == "done"
    assert orch.run_mission("", mission_id=999999)["error"]
    store.close()
    mem.close()


def test_mission_tick_reports_once():
    cfg, mem, eng = _engine()
    store = MissionStore(cfg.resolved_memory_db())
    m = store.create("g", [{"agent": "researcher", "instruction": "x",
                            "depends_on": []}], {}, "telegram:me")
    store.save_step(m["id"], 0, "ok", "result here")
    store.finish(m["id"], "done")
    assert any("project" in x["message"] for x in eng.tick())
    pend = eng.outbox.pending("telegram")
    assert any("🏁" in p["message"] for p in pend)
    eng.tick()
    assert len(eng.outbox.pending("telegram")) == len(pend)
    store.close()
    mem.close()


def test_missions_routes():
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        r = _post(base, "/missions", {"action": "create",
                                      "goal": "research lizards"})
        assert r["started"] is True
        done = None
        for _ in range(60):
            time.sleep(0.5)
            ms = _post(base, "/missions", {"action": "list"})["missions"]
            if ms and ms[0]["status"] in ("done", "failed"):
                done = ms[0]
                break
        assert done and done["status"] == "done"
        g = _post(base, "/missions", {"action": "get", "id": done["id"]})
        assert g["mission"]["goal"] == "research lizards"
        assert _post(base, "/missions", {"action": "resume",
                                         "id": done["id"]})["started"] is True
        assert "error" in _post(base, "/missions", {"action": "bogus"})
        with urllib.request.urlopen(base + "/missions", timeout=10) as resp:
            assert json.loads(resp.read().decode())["missions"]
    finally:
        srv.shutdown()
        mem.close()


def test_owner_projects():
    mod = _load("owner_proj", "bridges/owner.py")

    async def fake(path, payload=None, timeout=120):
        assert path == "/missions"
        if payload["action"] == "create":
            return {"started": True}
        if payload["action"] == "resume":
            return {"started": True} if payload["id"] == 1 else {"error": "x"}
        return {"missions": [{"id": 1, "status": "done", "goal": "g"}]}

    run = lambda c, a: asyncio.run(mod.run_owner_command(fake, "telegram",
                                                         c, a, "me"))
    assert "🚀" in run("project", "research lizards")
    assert "usage" in run("project", "")
    assert "#1" in run("projects", "")
    assert "resumed" in run("resume", "1")
    assert "usage" in run("resume", "x")
