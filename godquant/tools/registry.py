"""Tool registry: named, leveled, audited capabilities for agents.

Levels: read (no side effects) → act (workspace writes, code runs) →
dangerous (raw shell — needs cfg.allow_exec). Every call is audit-logged
to memory (kind="tool"). Agents discover via tool_list(); the agentic
loop lives in BaseAgent.ask_with_tools().
"""
from __future__ import annotations

import logging
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("godquant.tools")


@dataclass
class Tool:
    name: str
    desc: str
    level: str  # read|act|dangerous
    params: dict = field(default_factory=dict)
    func: object = None


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    TOOLS[tool.name] = tool
    return tool


def tool_list(names: list[str] | None = None) -> str:
    names = names or sorted(TOOLS)
    lines = []
    for n in names:
        t = TOOLS.get(n)
        if t:
            lines.append(f"- {t.name}({', '.join(t.params) or 'none'}): "
                         f"{t.desc} [{t.level}]")
    return "\n".join(lines)


def run_tool(name: str, args: dict | None, ctx: dict) -> dict:
    t = TOOLS.get(name)
    if not t:
        return {"ok": False, "error": f"unknown tool: {name}"}
    if t.level == "dangerous" and not ctx.get("allow_dangerous"):
        return {"ok": False, "error": "dangerous tools disabled (GQ_ALLOW_EXEC=1)"}
    try:
        out = t.func(args or {}, ctx)
        _audit(ctx, name, args, True, "")
        return {"ok": True, "output": str(out)[:4000]}
    except Exception as e:
        _audit(ctx, name, args, False, str(e)[:200])
        return {"ok": False, "error": str(e)[:500]}


def _audit(ctx: dict, name: str, args, ok: bool, err: str):
    try:
        mem = ctx.get("memory")
        if mem is not None:
            mem.add("tool", f"{name} {str(args)[:200]} → "
                           f"{'OK' if ok else 'FAIL ' + err}",
                    tags=f"tool {name}", score=1.0 if ok else 0.0)
    except Exception:
        pass


def _ws(ctx: dict) -> Path:
    root = Path(ctx["cfg"].resolved_workspace())
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe(rel: str, root: Path) -> Path:
    p = (root / (rel or "")).resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"path escapes workspace: {rel}")
    return p


# ---------- implementations ----------
def _fetch(args: dict, ctx: dict) -> str:
    from godquant.tools.research import fetch_text
    return fetch_text((args.get("url") or "").strip())


def _research(args: dict, ctx: dict) -> str:
    from godquant.tools.research import research
    return research(args.get("query", ""),
                    max_sources=int(args.get("max_sources", 3) or 3))


def _codegen(args: dict, ctx: dict) -> str:
    import json as _json
    import sys as _sys
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "codegen_megabuild.py"
    if not script.exists():
        raise ValueError("codegen script missing")
    spec = args.get("spec")
    if isinstance(spec, str):
        try:
            spec = _json.loads(spec)
        except Exception:
            raise ValueError("spec must be a dict or JSON string")
    if not isinstance(spec, dict) or not spec.get("files"):
        raise ValueError("need spec.files {path: content}")
    ws = _ws(ctx)
    name = re.sub(r"[^\w-]+", "_", str(spec.get("name", "build")))[:40]
    proj = ws / "codegen" / name
    spec_path = proj / "spec.json"
    proj.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(_json.dumps(spec))
    p = subprocess.run([_sys.executable, str(script), str(spec_path),
                        "--project", str(proj)],
                       capture_output=True, text=True, timeout=300)
    out = (p.stdout + p.stderr).strip()[-4000:]
    if p.returncode != 0:
        raise ValueError(f"codegen failed (exit {p.returncode}): {out[-500:]}")
    return out or f"built {proj}"


def _recall(args: dict, ctx: dict) -> str:
    mind = ctx.get("mind")
    if mind is None:
        raise ValueError("no mind in context")
    hits = mind.recall(args.get("query", ""), scope=args.get("scope") or None,
                       limit=int(args.get("limit", 5) or 5))
    if not hits:
        return "(nothing recalled)"
    return "\n".join(f"#{h['id']} [{h['layer']}] {h['content'][:200]}"
                     for h in hits)


def _remember(args: dict, ctx: dict) -> str:
    mind = ctx.get("mind")
    if mind is None:
        raise ValueError("no mind in context")
    if not (args.get("content") or "").strip():
        raise ValueError("need content")
    mid = mind.remember(args["content"], layer=args.get("layer", "semantic"),
                        scope=args.get("scope", "global"),
                        importance=float(args.get("importance", 0.5) or 0.5))
    return f"remembered #{mid}"


def _fs_read(args: dict, ctx: dict) -> str:
    p = _safe(args.get("path", ""), _ws(ctx))
    return p.read_text()[:6000]


def _fs_write(args: dict, ctx: dict) -> str:
    root = _ws(ctx)
    p = _safe(args.get("path", ""), root)
    if not args.get("content"):
        raise ValueError("need content")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args["content"][:100000])
    return f"wrote {p.relative_to(root)} ({len(args['content'])} chars)"


def _py_run(args: dict, ctx: dict) -> str:
    from godquant.dev.sandbox import run_python
    res = run_python(args.get("code", ""),
                     timeout=int(args.get("timeout", 20) or 20))
    out = (res.get("stdout", "") + res.get("stderr", ""))[:2000]
    return f"exit={res.get('exit_code')} {res.get('elapsed', 0):.1f}s\n{out}"


def _shell(args: dict, ctx: dict) -> str:
    cmd = (args.get("cmd") or "").strip()
    if not cmd:
        raise ValueError("need cmd")
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       timeout=int(args.get("timeout", 30) or 30),
                       cwd=str(_ws(ctx)))
    return f"exit={p.returncode}\n{(p.stdout + p.stderr)[-3000:]}"


def _git(args: dict, ctx: dict, action: str) -> str:
    repo = str(ctx.get("repo", "."))
    if action == "status":
        cmd = ["git", "status", "--short"]
    else:
        msg = (args.get("message") or "").strip() or "agent commit"
        cmd = ["git", "commit", "-qm", msg]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                       cwd=repo)
    out = (p.stdout + p.stderr).strip()[:2000]
    return f"exit={p.returncode}\n{out or '(clean)'}"


register(Tool("web_fetch", "read a web/file page as text", "read",
              {"url": "http(s):// or file://"}, _fetch))
register(Tool("recall_memory", "search unified memory", "read",
              {"query": "text", "scope": "optional"}, _recall))
register(Tool("remember_fact", "store a durable memory", "act",
              {"content": "text", "importance": "0..1"}, _remember))
register(Tool("fs_read", "read a workspace file", "read",
              {"path": "relative"}, _fs_read))
register(Tool("fs_write", "write a workspace file", "act",
              {"path": "relative", "content": "text"}, _fs_write))
register(Tool("py_run", "run python in sandbox", "act",
              {"code": "python", "timeout": "sec"}, _py_run))
register(Tool("shell", "raw shell in workspace", "dangerous",
              {"cmd": "shell", "timeout": "sec"}, _shell))
register(Tool("git_status", "git status of repo", "read", {}, 
              lambda a, c: _git(a, c, "status")))
register(Tool("deep_research", "search + read N pages, brief with sources",
              "read", {"query": "str", "max_sources": "int=3"}, _research))
register(Tool("codegen", "multi-file project build from spec (coder)",
              "act", {"spec": "{name, files, tests}"}, _codegen))
register(Tool("git_commit", "git commit staged/all", "act",
              {"message": "msg"}, lambda a, c: _git(a, c, "commit")))
