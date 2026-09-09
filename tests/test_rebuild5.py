"""v5 rebuild integration — generated via scripts/codegen_megabuild.py."""
import base64
import json
import tempfile
import threading
import urllib.request
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.companion import persona_pack as PP
from godquant.companion.companion import CompanionAgent
from godquant.companion.personas import render_persona
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.providers import LLMResponse, OpenAICompatibleProvider
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.memory.vault import HistoryVault

IMG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"0" * 50).decode()


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


def test_chat_lands_in_vault_and_survives():
    cfg, mem = _stack(collect=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        agent.chat("hello vault", cid="t:v")
        agent.chat("second msg", cid="t:v")
        v = HistoryVault(cfg.resolved_memory_db())
        assert v.count("t:v") >= 4  # user+assistant x2
        rows = v.load("t:v")
        assert any("hello vault" in r["content"] for r in rows)
        v.close()
        v2 = HistoryVault(cfg.resolved_memory_db())
        assert v2.count("t:v") >= 4
        v2.close()
    finally:
        srv.shutdown()


def test_legacy_blobs_migrate_once():
    cfg, mem = _stack(collect=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        mem.add("chat", json.dumps({"cid": "t:old", "role": "user",
                                    "content": "ancient words"}),
                tags="chat t:old")
        rows = agent._load_history("t:old")
        assert any("ancient words" in r["content"] for r in rows)
        v = HistoryVault(cfg.resolved_memory_db())
        assert v.count("t:old") >= 1
        v.close()
    finally:
        srv.shutdown()


def test_vision_payload_swaps_model(monkeypatch):
    import godquant.llm.providers as PV
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen.update(payload)
        return {"choices": [{"message": {"content": "I see it"}}],
                "usage": {}}
    monkeypatch.setattr(PV, "_http_post", fake_post)
    p = OpenAICompatibleProvider("groq", api_key="x")
    r = p.complete("sys", "look", images=[IMG])
    assert r.model == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert isinstance(seen["messages"][1]["content"], list)
    assert seen["messages"][1]["content"][1]["type"] == "image_url"


def test_router_threads_images(monkeypatch):
    cfg, mem = _stack()
    router = LLMRouter(cfg, mem)
    got = {}

    class Stub:
        model = "stub"
        name = "stub"

        def complete(self, system, user, images=None):
            got["images"] = images
            return LLMResponse(text="ok", model="stub", provider="stub",
                               prompt_tokens=0, completion_tokens=0)
    monkeypatch.setattr(router, "_build", lambda kind: Stub())
    monkeypatch.setattr(router, "chain", lambda: ["stub"])
    router.complete("s", "u", images=["i"])
    assert got["images"] == ["i"]


def test_chat_with_photo_offline_marks_vault():
    cfg, mem = _stack(collect=False)
    srv, base, agent = _serve(cfg, mem)
    try:
        out = agent.chat("look at this", cid="t:p", image_data=IMG)
        assert out.get("response")
        v = HistoryVault(cfg.resolved_memory_db())
        rows = v.load("t:p")
        assert any("[photo]" in r["content"] for r in rows)
        v.close()
    finally:
        srv.shutdown()


def test_pack_merges_into_render(tmp_path, monkeypatch):
    monkeypatch.setattr(PP, "pack_dir", lambda: tmp_path)
    (tmp_path / "devon.md").write_text(
        "blurb: live\n---\n\n# voice\ntalk soft\n")
    out = render_persona("devon", mood_context="ok", history_summary="none")
    assert "[VOICE PACK]" in out and "talk soft" in out


def test_persona_pack_route_e2e():
    cfg, mem = _stack()
    srv, base, agent = _serve(cfg, mem)
    try:
        r = _post(base, "/persona", {"pack_action": "create",
                                     "name": "zztestpack", "blurb": "b"})
        assert r.get("created") == "zztestpack"
        r = _post(base, "/persona", {"pack_action": "show",
                                     "name": "zztestpack"})
        assert r["pack"]["blurb"] == "b"
        r = _post(base, "/persona", {"pack_action": "list"})
        assert "zztestpack" in r["packs"]
    finally:
        srv.shutdown()
        for f in ("zztestpack.md", "zztestpack.learned.md"):
            try:
                (PP.pack_dir() / f).unlink()
            except Exception:
                pass
