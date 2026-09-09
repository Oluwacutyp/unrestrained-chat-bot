#!/usr/bin/env python3
"""codegen megabuild: multi-file project generator + verifier.

Takes a JSON spec, writes every file jailed under --project, byte-compiles
all Python, optionally runs test commands, and prints a REPORT. Used by
humans AND by the coder agent (see the `codegen` tool).

Spec: {"name": "demo",
        "files": {"main.py": "print('hi')\n", "README.md": "..."},
        "tests": ["python -m pytest -q"]}   # optional, run in project dir

Usage:
  python scripts/codegen_megabuild.py spec.json [--project DIR]
  echo '{"files": {...}}' | python scripts/codegen_megabuild.py -

Safety: absolute paths and `..` are rejected; test commands run with a
timeout and no network assumptions.
"""
from __future__ import annotations

import argparse
import json
import py_compile
import subprocess
import sys
from pathlib import Path


def _safe(rel: str) -> Path | None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        return None
    return p


def build(spec: dict, project: Path, test_timeout: int = 180) -> dict:
    report: dict = {"project": str(project), "files": [], "rejected": [],
                    "compiled": [], "compile_errors": [], "tests": []}
    files = spec.get("files", {})
    if not isinstance(files, dict) or not files:
        raise ValueError("spec needs files {path: content}")
    for rel, content in files.items():
        sp = _safe(str(rel))
        if sp is None or not isinstance(content, str):
            report["rejected"].append(str(rel))
            continue
        dest = project / sp
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        report["files"].append(str(sp))
    for rel in report["files"]:
        if not rel.endswith(".py"):
            continue
        try:
            py_compile.compile(str(project / rel), doraise=True)
            report["compiled"].append(rel)
        except Exception as e:
            report["compile_errors"].append(f"{rel}: {e}")
    for cmd in spec.get("tests", []) or []:
        try:
            p = subprocess.run(cmd, shell=True, cwd=str(project),
                               capture_output=True, text=True,
                               timeout=test_timeout)
            report["tests"].append(
                {"cmd": cmd, "exit": p.returncode,
                 "out": (p.stdout + p.stderr)[-1500:]})
        except Exception as e:
            report["tests"].append({"cmd": cmd, "exit": -1,
                                    "out": f"error: {e}"})
    ok = not report["rejected"] and not report["compile_errors"] and \
        all(t["exit"] == 0 for t in report["tests"])
    report["ok"] = ok
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="multi-file build + verify")
    ap.add_argument("spec", help="spec.json path or - for stdin")
    ap.add_argument("--project", default="megabuild_out")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.spec == "-" else \
        Path(args.spec).read_text()
    try:
        spec = json.loads(raw)
    except Exception as e:
        print(f"bad spec JSON: {e}")
        return 2
    project = Path(args.project)
    project.mkdir(parents=True, exist_ok=True)
    try:
        rep = build(spec, project)
    except ValueError as e:
        print(f"spec error: {e}")
        return 2
    (project / "report.json").write_text(json.dumps(rep, indent=1))
    print(f"PROJECT: {project}")
    print(f"files: {len(rep['files'])} "
          f"compiled: {len(rep['compiled'])} "
          f"rejected: {len(rep['rejected'])} "
          f"errors: {len(rep['compile_errors'])}")
    for r in rep["rejected"]:
        print(f"  rejected: {r}")
    for e in rep["compile_errors"]:
        print(f"  compile: {e}")
    for t in rep["tests"]:
        print(f"  test exit={t['exit']}: {t['cmd']}")
        if t["out"].strip():
            print("   | " + "\n   | ".join(t["out"].strip().splitlines()[:8]))
    print("OK" if rep["ok"] else "FAILED")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
