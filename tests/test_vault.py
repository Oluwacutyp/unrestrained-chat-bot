"""Vault tests — generated via scripts/codegen_megabuild.py."""
import tempfile
from pathlib import Path

from godquant.memory.vault import HistoryVault


def _vault():
    return HistoryVault(Path(tempfile.mkdtemp()) / "v.db")


def test_append_load_ordered():
    v = _vault()
    for i in range(100):
        v.append("c", "user" if i % 2 == 0 else "assistant", f"m{i}")
    assert v.count("c") == 100
    rows = v.load("c", limit=200)
    assert [r["content"] for r in rows] == [f"m{i}" for i in range(100)]
    assert rows[0]["role"] == "user" and rows[1]["role"] == "assistant"
    v.close()


def test_limit_takes_tail():
    v = _vault()
    for i in range(50):
        v.append("c", "user", f"m{i}")
    rows = v.load("c", limit=10)
    assert [r["content"] for r in rows] == [f"m{i}" for i in range(40, 50)]
    v.close()


def test_restart_safe_and_cid_isolated():
    d = Path(tempfile.mkdtemp()) / "v.db"
    v = HistoryVault(d)
    v.append("a", "user", "hello")
    v.close()
    v2 = HistoryVault(d)
    assert v2.count("a") == 1 and v2.load("a")[0]["content"] == "hello"
    assert v2.count("b") == 0 and v2.load("b") == []
    v2.close()


def test_import_legacy():
    v = _vault()
    n = v.import_legacy([{"cid": "a", "role": "user", "content": "x"},
                         {"cid": "", "content": "skip"},
                         {"cid": "a", "role": "assistant", "content": "y"}])
    assert n == 2 and v.count("a") == 2
    v.close()
