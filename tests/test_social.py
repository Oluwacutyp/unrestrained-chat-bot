"""v4.6 social bridges, research, codegen."""
import asyncio
import importlib
import importlib.util
import json
import py_compile
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

import pytest

from godquant.agents.orchestrator import Orchestrator
from godquant.companion.companion import CompanionAgent
from godquant.companion.server import create_server
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.tools import registry as REG
from godquant.tools import research as RES
from scripts.codegen_megabuild import build as megabuild

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


# ---------- research core ----------
def test_html_text_strips_noise():
    html_doc = ("<html><head><title>Hi</title><style>.x{}</style></head>"
                "<body><script>evil()</script><p>Hello <b>you</b></p></body>")
    title, text = RES.html_text(html_doc)
    assert title == "Hi" and text == "Hello you"


def test_extract_file_and_bad_scheme(tmp_path):
    f = tmp_path / "a.html"
    f.write_text("<title>T</title><p>deep words here</p>")
    d = RES.extract("file://" + str(f))
    assert d["title"] == "T" and "deep words" in d["text"]
    with pytest.raises(ValueError, match="need http"):
        RES.extract("gopher://x")


def test_extract_youtube_uses_transcript(monkeypatch):
    monkeypatch.setattr(RES, "youtube_transcript",
                        lambda u: "hello world transcript")
    monkeypatch.setattr(RES, "oembed", lambda u: {"title": "vid"})
    d = RES.extract("https://www.youtube.com/watch?v=abcdef123")
    assert d["kind"] == "youtube-transcript"
    assert "hello world" in d["text"] and d["title"] == "vid"


def test_oembed_dispatch(monkeypatch):
    seen = []

    def fake_get(url, timeout=15):
        seen.append(url)
        return b'{"title": "t"}', "application/json"
    monkeypatch.setattr(RES, "_get", fake_get)
    RES.oembed("https://www.tiktok.com/@u/video/1")
    RES.oembed("https://x.com/u/status/1")
    assert any("tiktok.com/oembed" in u for u in seen)
    assert any("publish.twitter.com" in u for u in seen)
    assert RES.oembed("https://example.com/x") == {}


def test_research_brief_shape(monkeypatch):
    from godquant.companion import web_search as WS
    monkeypatch.setattr(WS, "ddg_text",
                        lambda q, max_results=3: [{"title": "T", "href": "https://e.com"}])
    monkeypatch.setattr(RES, "extract",
                        lambda u, max_chars=1500: {"title": "T", "text": "words",
                                                  "source": u, "kind": "page"})
    out = RES.research("lagos weather")
    assert "RESEARCH: lagos weather" in out and "[1] T" in out


def test_registry_has_new_tools():
    assert {"deep_research", "codegen", "web_fetch"} <= set(REG.TOOLS)
    assert REG.TOOLS["deep_research"].level == "read"
    assert REG.TOOLS["codegen"].level == "act"


# ---------- codegen megabuild ----------
def test_megabuild_happy_and_rejected(tmp_path):
    spec = {"name": "demo",
            "files": {"main.py": "print('hi')\n", "README.md": "yo"},
            "tests": ["python -c \"import main\""]}
    rep = megabuild(spec, tmp_path / "proj")
    assert rep["ok"] is True
    assert "main.py" in rep["files"]
    assert "main.py" in rep["compiled"]
    assert rep["tests"][0]["exit"] == 0
    bad = megabuild({"files": {"../evil.py": "x", "ok.py": "Y = 2\n"}},
                    tmp_path / "p2")
    assert "../evil.py" in bad["rejected"] and bad["ok"] is False
    with pytest.raises(ValueError):
        megabuild({"name": "x"}, tmp_path / "p3")


def test_megabuild_cli(tmp_path):
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({"files": {"a.py": "X = 1\n"}}))
    p = subprocess.run([sys.executable, str(ROOT / "scripts" /
                                            "codegen_megabuild.py"),
                        str(spec), "--project", str(tmp_path / "out")],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "OK" in p.stdout
    assert (tmp_path / "out" / "report.json").exists()


def test_coder_agent_has_codegen_tool():
    src = (ROOT / "godquant" / "agents" / "specialists.py").read_text()
    assert '"codegen"' in src


# ---------- bridges: compile + lib guards ----------
@pytest.mark.parametrize("rel", ["bridges/reddit_bridge.py",
                                 "bridges/x_bridge.py",
                                 "bridges/discord_history.py",
                                 "bridges/discord_bot.py"])
def test_bridges_compile(rel):
    py_compile.compile(str(ROOT / rel), doraise=True)


def _reload_without(modname, lib):
    saved = sys.modules.pop(lib, None)
    sys.modules[lib] = None
    try:
        mod = importlib.import_module(modname)
        return importlib.reload(mod)
    finally:
        sys.modules.pop(lib, None)
        if saved is not None:
            sys.modules[lib] = saved


@pytest.mark.parametrize("modname,lib,flag,needle", [
    ("bridges.reddit_bridge", "praw", "PRAW_OK", "pip install praw"),
    ("bridges.x_bridge", "tweepy", "TWEEPY_OK", "pip install tweepy"),
    ("bridges.discord_history", "discord", "DISCORD_OK", "discord.py"),
])
def test_bridge_lib_guard(modname, lib, flag, needle, capsys):
    mod = _reload_without(modname, lib)
    assert getattr(mod, flag) is False
    assert mod.main([]) == 1
    assert needle in capsys.readouterr().out


# ---------- /research route + owner cmd ----------
def test_research_route(monkeypatch):
    cfg, mem = _stack()
    srv, base = _serve(cfg, mem)
    try:
        assert "error" in _post(base, "/research", {"query": ""})
        monkeypatch.setattr(REG, "run_tool",
                            lambda n, a, c: {"ok": True, "output": "BRIEF"})
        r = _post(base, "/research", {"query": "x"})
        assert r["brief"] == "BRIEF"
    finally:
        srv.shutdown()


def test_owner_research_command():
    mod = _load("owner_research", "bridges/owner.py")

    async def fake(path, payload=None):
        if path == "/research":
            return {"brief": "BRIEF-BODY"}
        raise AssertionError(path)

    run = lambda c, a: asyncio.run(
        mod.run_owner_command(fake, "telegram", c, a, "me"))
    assert "BRIEF-BODY" in run("research", "lagos weather")
    assert "usage" in run("research", "")
