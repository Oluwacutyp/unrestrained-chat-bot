"""v4.4 personal model: runtime load, GGUF switch, export formats."""
import asyncio
import importlib.util
import json
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.local_llm import download_model
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.train.export import build_packs

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
                                 data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


# ---------- export formats ----------
def test_export_writes_sharegpt_and_alpaca():
    ws = Path(tempfile.mkdtemp())
    (ws / "train").mkdir()
    (ws / "train" / "trajectories.jsonl").write_text(
        json.dumps({"user": "hi", "response": "hey you"}) + "\n")
    (ws / "train" / "prefs.jsonl").write_text("")
    out = build_packs(ws)
    assert out["sft"] == 1
    sg = json.loads((ws / "train" / "sft_sharegpt.jsonl")
                    .read_text().splitlines()[0])
    assert [m["from"] for m in sg["conversations"]] == \
        ["system", "human", "gpt"]
    al = json.loads((ws / "train" / "sft_alpaca.jsonl")
                    .read_text().splitlines()[0])
    assert al["instruction"] == "hi" and al["output"] == "hey you"
    card = (ws / "train" / "README.md").read_text()
    assert "ShareGPT" in card and "oluwacutyp" in card


# ---------- /model route ----------
def test_model_route_sets_model_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/model", {"primary": "huggingface",
                                   "model": "u/m"})
        assert r["primary"] == "huggingface" and r["model"] == "u/m"
        assert r["chain"][0] == "huggingface"
        assert cfg.llm_model == "u/m"
        saved = json.loads((tmp_path / ".godquant" / "config.json")
                           .read_text())
        assert saved["llm_model"] == "u/m"
        with urllib.request.urlopen(base + "/models", timeout=10) as h:
            m = json.loads(h.read().decode())
        assert m["model"] == "u/m"
        assert "error" in _post(base, "/model", {"primary": "nope"})
        assert "error" in _post(base, "/model", {})
    finally:
        srv.shutdown()


def test_model_route_gguf_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    gg = tmp_path / "devon.gguf"
    gg.write_bytes(b"GGUF")
    cfg, mem = _stack()
    srv, base, _ = _serve(cfg, mem)
    try:
        r = _post(base, "/model", {"gguf": str(gg)})
        assert r["primary"] == "llamacpp" and r["gguf"] == str(gg)
        assert cfg.model_path == str(gg)
        assert "error" in _post(base, "/model",
                                {"gguf": str(tmp_path / "no.gguf")})
        bad = tmp_path / "x.bin"
        bad.write_bytes(b"x")
        assert "error" in _post(base, "/model", {"gguf": str(bad)})
    finally:
        srv.shutdown()


# ---------- GGUF download keys ----------
def test_download_model_custom_key(tmp_path):
    (tmp_path / "f.gguf").write_bytes(b"GGUF")
    p = download_model("u/r/f.gguf", dest_dir=tmp_path)
    assert p.name == "f.gguf"
    with pytest.raises(ValueError):
        download_model("nope")


# ---------- owner commands ----------
def test_owner_model_commands():
    mod = _load("owner_model", "bridges/owner.py")

    async def fake(path, payload=None):
        if path == "/model":
            if (payload or {}).get("primary") == "nope":
                return {"error": "unknown"}
            return {"primary": (payload or {}).get("primary", "llamacpp"),
                    "model": (payload or {}).get("model"),
                    "gguf": (payload or {}).get("gguf")}
        if path == "/models":
            return {"primary": "groq", "model": "u/m",
                    "chain": ["groq", "heuristic"]}
        raise AssertionError(path)

    run = lambda c, a: asyncio.run(
        mod.run_owner_command(fake, "telegram", c, a, "me"))
    assert "u/m" in run("model", "load u/m")
    assert "y.gguf" in run("model", "load /x/y.gguf")
    assert "u/m" in run("model", "list")
    assert "u/m" in run("models", "")
    assert "groq" in run("model", "groq")
    assert "usage" in run("model", "")
