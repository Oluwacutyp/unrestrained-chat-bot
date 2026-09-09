"""Specialist agents: researcher, coder, quant, backtest, risk, reviewer."""
from __future__ import annotations

import itertools
import json
import re

from godquant.agents.base import AgentResult, AgentTask, BaseAgent
from godquant.dev.sandbox import run_python
from godquant.llm import prompts
from godquant.quant import risk as R
from godquant.quant.backtest import run_backtest
from godquant.quant.data import get_ohlc
from godquant.quant.strategies import STRATEGIES, generate


class ResearcherAgent(BaseAgent):
    name = "researcher"
    system_prompt = prompts.RESEARCHER

    def execute(self, task: AgentTask) -> AgentResult:
        brief = task.instruction
        if not self.cfg.offline:
            try:
                from godquant.companion import web_search as WS
                brief += f"\n\n[LIVE WEB RESULTS — cite these]\n{WS.smart_search(task.instruction)[:2500]}"
            except Exception:
                pass
        if self.cfg.offline:
            out = self.ask(brief)
        else:
            out = self.ask_with_tools(brief, ["web_fetch", "recall_memory"])
        try:
            self.memory.add("fact", out[:2000], tags="research")
        except Exception:
            pass
        return AgentResult(agent=self.name, ok=True, output=out)


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = prompts.CODER

    CODE_RE = re.compile(r"```python\n(.*?)```", re.S)

    def execute(self, task: AgentTask) -> AgentResult:
        if self.cfg.offline:  # offline: byte-identical legacy path
            out = self.ask(task.instruction)
        else:
            out = self.ask_with_tools(task.instruction, ["py_run", "fs_read"])
        artifacts: dict = {}
        # extract first python block and smoke-test it in the sandbox
        m = self.CODE_RE.search(out)
        if m:
            code = m.group(1)
            res = run_python(code, timeout=20)
            artifacts["smoke_test"] = res
            out += ("\n\n---\n[SANDBOX SMOKE TEST] exit="
                    f"{res['exit_code']} {res['elapsed']:.1f}s\n"
                    f"{(res['stdout'] + res['stderr'])[:1500]}")
        ws = self.cfg.resolved_workspace()
        target = ws / "agent_build.py"
        if m and self.cfg.auto_apply_patches:
            target.write_text(m.group(1))
            artifacts["file"] = str(target)
        return AgentResult(agent=self.name, ok=True, output=out, artifacts=artifacts)


class QuantAgent(BaseAgent):
    name = "quant"
    system_prompt = prompts.QUANT

    def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.context
        symbol = ctx.get("symbol", self.cfg.default_symbol)
        data, src = get_ohlc(symbol, source=self.cfg.data_source,
                             offline=self.cfg.offline)
        brief = (f"{task.instruction}\n\n[MARKET CONTEXT] {symbol} via {src}: "
                 f"n={len(data)}, last_close={data.close[-1]:.4f}, "
                 f"range=({min(data.close):.2f},{max(data.close):.2f})")
        out = self.ask(brief)
        return AgentResult(agent=self.name, ok=True, output=out,
                           artifacts={"symbol": symbol, "source": src,
                                      "last_close": data.close[-1], "n": len(data)})


class BacktestAgent(BaseAgent):
    name = "backtest"
    system_prompt = prompts.BACKTEST

    def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.context
        symbol = ctx.get("symbol", self.cfg.default_symbol)
        strategy = ctx.get("strategy", "sma_cross")
        params = ctx.get("params") or {}
        optimize = ctx.get("optimize", False)
        metric = ctx.get("metric", "sharpe")
        data, src = get_ohlc(symbol, source=self.cfg.data_source,
                             offline=self.cfg.offline)

        def one(p: dict):
            sig = generate(strategy, data, p)
            return run_backtest(
                data.close, sig, initial_cash=self.cfg.initial_cash,
                commission=self.cfg.commission, slippage=self.cfg.slippage)

        if optimize and strategy in STRATEGIES:
            grid = STRATEGIES[strategy]["grid"]
            keys, vals = list(grid), list(grid.values())
            best, best_p, tried = None, {}, 0
            for combo in itertools.product(*vals):
                p = dict(zip(keys, combo))
                r = one(p)
                tried += 1
                key = r.metrics.get(metric, 0)
                if best is None or key > best.metrics.get(metric, 0):
                    best, best_p = r, p
            assert best is not None
            res, used_params = best, best_p
            header = (f"[OPTIMIZED] {strategy} on {symbol} ({src}) — "
                      f"{tried} combos, best {metric}={best.metrics.get(metric, 0):.3f} "
                      f"params={used_params}")
        else:
            res = one(params)
            used_params = params or STRATEGIES.get(strategy, {}).get("defaults", {})
            header = f"[BACKTEST] {strategy} on {symbol} ({src}) params={used_params}"

        narrative = self.ask(
            f"Summarize this backtest honestly in 5 bullets. Flag risks.\n"
            f"{header}\n{res.summary()}")
        output = f"{header}\n{res.summary()}\n\n{narrative}"
        try:
            self.memory.add("artifact", f"{header} | {res.metrics}",
                            tags=f"backtest {strategy} {symbol}",
                            score=float(res.metrics.get(metric, 0)))
        except Exception:
            pass
        return AgentResult(agent=self.name, ok=True, output=output,
                           artifacts={"metrics": res.metrics, "params": used_params,
                                      "symbol": symbol, "strategy": strategy,
                                      "source": src})


class RiskAgent(BaseAgent):
    name = "risk"
    system_prompt = prompts.RISK

    def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.context
        report = R.risk_report(
            equity=float(ctx.get("equity", self.cfg.initial_cash)),
            risk_pct=float(ctx.get("risk_pct", self.cfg.max_risk_per_trade)),
            entry=float(ctx.get("entry", 100)),
            stop=float(ctx.get("stop", 95)),
            win_rate=float(ctx.get("win_rate", 0.5)),
            avg_win=float(ctx.get("avg_win", 1.5)),
            avg_loss=float(ctx.get("avg_loss", 1.0)),
            open_heat=float(ctx.get("open_heat", 0.0)),
            day_pnl_pct=float(ctx.get("day_pnl_pct", 0.0)))
        verdict = self.ask(f"Risk case:\n{task.instruction}\n\nMath:\n{report}\n\n"
                           f"Give a one-paragraph ruling: APPROVE/VETO/HALT and why.")
        return AgentResult(agent=self.name, ok=True,
                           output=f"{report}\n\n---\n{verdict}")


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    system_prompt = prompts.REVIEWER

    def execute(self, task: AgentTask) -> AgentResult:
        out = self.ask(task.instruction)
        score = 50.0
        m = re.search(r"(\d{1,3})\s*/\s*100|score\s*[:=]\s*(\d+)", out, re.I)
        if m:
            try:
                score = float(m.group(1) or m.group(2))
            except (ValueError, TypeError):
                pass
        verdict = "REVISE"
        up = out.upper()
        if "APPROVE" in up:
            verdict = "APPROVE"
        elif "REJECT" in up:
            verdict = "REJECT"
        return AgentResult(agent=self.name, ok=True, output=out,
                           artifacts={"verdict": verdict}, score=score)


from godquant.companion.companion import CompanionAgent  # noqa: E402

AGENTS = {
    "researcher": ResearcherAgent,
    "coder": CoderAgent,
    "quant": QuantAgent,
    "backtest": BacktestAgent,
    "risk": RiskAgent,
    "reviewer": ReviewerAgent,
    "companion": CompanionAgent,
}
