"""Safe-ish local execution: subprocess sandbox + test runner.

Termux-safe: uses the same python interpreter, temp files, timeouts.
Not a security boundary against malicious code — it guards against
accidents (infinite loops, tracebacks), not adversaries.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

BLOCKED = ("os.system", "subprocess", "socket", "__import__(\"os\")",
           "shutil.rmtree", "rm -rf")


def audit(code: str) -> list[str]:
    return [b for b in BLOCKED if b in code]


def run_python(code: str, timeout: int = 30) -> dict:
    t0 = time.time()
    flags = audit(code)
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=timeout)
        return {"exit_code": p.returncode, "stdout": p.stdout[-4000:],
                "stderr": p.stderr[-4000:], "elapsed": time.time() - t0,
                "audit_flags": flags}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "TIMEOUT",
                "elapsed": time.time() - t0, "audit_flags": flags}
    finally:
        try:
            Path(path).unlink()
        except Exception:
            pass


def run_pytest(path: str = "tests", timeout: int = 120) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", path, "-q"],
                           capture_output=True, text=True, timeout=timeout)
        return {"exit_code": p.returncode,
                "output": (p.stdout + p.stderr)[-4000:],
                "elapsed": time.time() - t0}
    except FileNotFoundError:
        return {"exit_code": -1, "output": "pytest not installed", "elapsed": 0}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": "TIMEOUT", "elapsed": time.time() - t0}
