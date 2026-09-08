"""End-to-end: offline mission, memory, risk math. No network, no keys."""
import tempfile
from pathlib import Path

from godquant.agents.orchestrator import Orchestrator
from godquant.config import GodQuantConfig
from godquant.llm.router import LLMRouter
from godquant.memory.store import MemoryStore
from godquant.quant.risk import kelly_fraction, position_size_fixed_risk


def _stack():
    tmp = Path(tempfile.mkdtemp())
    cfg = GodQuantConfig(offline=True, workspace=str(tmp / "ws"),
                         memory_db=str(tmp / "mem.db"), max_workers=2)
    mem = MemoryStore(cfg.resolved_memory_db())
    router = LLMRouter(cfg, mem)
    return cfg, mem, Orchestrator(cfg, router, mem)


def test_offline_backtest_mission():
    cfg, mem, orch = _stack()
    from godquant.agents.base import AgentTask
    res = orch.run_task("backtest", AgentTask(
        "test", {"symbol": "BTCUSDT", "strategy": "sma_cross"}))
    assert res.ok and res.artifacts["metrics"]["num_trades"] >= 0
    mem.close()


def test_memory_lessons_roundtrip():
    cfg, mem, orch = _stack()
    mem.add_lesson("never trade without a stop", tags="risk", score=80)
    hits = mem.search("stop loss trading")
    assert hits and "stop" in hits[0].content
    mem.close()


def test_risk_math():
    s = position_size_fixed_risk(10000, 0.01, 100, 95)
    assert abs(s["qty"] - 20.0) < 1e-9
    assert kelly_fraction(0.6, 2.0, 1.0) > 0
    assert kelly_fraction(0.4, 1.0, 1.0) == 0.0


def test_full_mission_offline():
    cfg, mem, orch = _stack()
    results = orch.run("backtest an sma strategy on BTCUSDT", improve=True)
    assert len(results) >= 3
    assert all(r.ok for r in results)
    assert mem.stats()["llm_calls"] >= 1
    mem.close()
