"""Patch application: unified diffs + safe file writes + git helpers."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_DIFF_FILE = re.compile(r"^\+\+\+\s+[ab]/(.+)$", re.M)


def files_in_diff(diff: str) -> list[str]:
    return _DIFF_FILE.findall(diff)


def apply_diff(diff: str, root: str | Path = ".", check: bool = True) -> dict:
    """Apply a unified diff via `patch` (preferred) or `git apply` fallback."""
    root = Path(root)
    if check:
        dangerous = [f for f in files_in_diff(diff)
                     if f.startswith(("..", "/", ".git/"))]
        if dangerous:
            return {"ok": False, "error": f"blocked paths: {dangerous}"}
    for cmd in (["patch", "-p1", "-i", "-"], ["git", "apply", "-"]):
        try:
            p = subprocess.run(cmd, input=diff, capture_output=True, text=True,
                               cwd=str(root), timeout=30)
            if p.returncode == 0:
                return {"ok": True, "tool": cmd[0], "files": files_in_diff(diff)}
        except FileNotFoundError:
            continue
    return {"ok": False, "error": "neither `patch` nor `git apply` available/failed"}


def write_file_safely(root: str | Path, rel: str, content: str) -> dict:
    root = Path(root).resolve()
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        return {"ok": False, "error": "path escapes workspace"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"ok": True, "path": str(target)}


def git_commit(message: str, root: str | Path = ".") -> dict:
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(root), check=True,
                       capture_output=True, timeout=30)
        p = subprocess.run(["git", "commit", "-m", message], cwd=str(root),
                           capture_output=True, text=True, timeout=30)
        return {"ok": p.returncode == 0, "output": (p.stdout + p.stderr)[-1000:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
