"""Multi-agent orchestrator: plan → fan-out → verify → improve.

- Planner decomposes goals into a DAG of AgentTasks (LLM or heuristic).
- Independent tasks run in parallel via threads (Termux-safe, no asyncio needed).
- Every run is judged; lessons persist to memory (self-improvement loop).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import re

from godquant.agents.base import AgentResult, AgentTask
from godquant.agents.specialists import AGENTS
from godquant.llm import prompts
from godquant.self_improve.evolver import SelfImprover

log = logging.getLogger("godquant.orchestrator")


class Orchestrator:
    def __init__(self, cfg, router, memory):
        self.cfg = cfg
        self.router = router
        self.memory = memory
        self.agents = {name: cls(cfg, router, memory) for name, cls in AGENTS.items()}
        self.improver = SelfImprover(cfg, router, memory)

    # ---------- planning ----------
    def plan(self, goal: str, context: dict | None = None) -> list[AgentTask]:
        context = context or {}
        try:
            raw = self.router.complete(prompts.ORCHESTRATOR, goal, agent="orchestrator").text
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            tasks = []
            for t in data.get("tasks", [])[:6]:
                if t.get("agent") in self.agents:
                    tasks.append(AgentTask(instruction=t.get("instruction", goal),
                                           context=dict(context),
                                           depends_on=t.get("depends_on", [])))
            if tasks:
                return tasks
        except Exception as e:
            log.debug("LLM planning failed (%s); using heuristic plan", e)
        return self._heuristic_plan(goal, context)

    def _heuristic_plan(self, goal: str, context: dict) -> list[AgentTask]:
        g = goal.lower()
        if any(k in g for k in ("backtest", "strategy", "sharpe", "optimize", "trade")):
            return [AgentTask(f"Research context for: {goal}", dict(context)),
                    AgentTask(f"Design/validate quant approach for: {goal}", dict(context)),
                    AgentTask(f"Execute and report: {goal}", dict(context)),
                    AgentTask(f"Risk-gate the outcome of: {goal}", dict(context))]
        return [AgentTask(f"Research context for: {goal}", dict(context)),
                AgentTask(f"Build: {goal}", dict(context)),
                AgentTask(f"Review the build for: {goal}", dict(context))]

    def _assign(self, i: int, goal: str) -> str:
        g = goal.lower()
        if any(k in g for k in ("backtest", "strategy", "sharpe", "optimize", "trade")):
            return ["researcher", "quant", "backtest", "risk"][min(i, 3)]
        return ["researcher", "coder", "reviewer"][min(i, 2)]

    # ---------- execution ----------
    def run_task(self, agent_name: str, task: AgentTask) -> AgentResult:
        agent = self.agents[agent_name]
        res = agent.run(task)
        try:
            self.memory.add("run", f"[{agent_name}] {task.instruction[:300]} → "
                                   f"{'OK' if res.ok else 'FAIL: ' + res.error}",
                            tags=f"run {agent_name}", score=res.score)
        except Exception:
            pass
        return res

    def run(self, goal: str, context: dict | None = None,
            improve: bool = True) -> list[AgentResult]:
        tasks = self.plan(goal, context)
        names = [self._assign(i, goal) for i in range(len(tasks))]
        # heuristic plans return bare tasks; align agent per stage
        results: list[AgentResult | None] = [None] * len(tasks)
        with cf.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as ex:
            futs = {ex.submit(self.run_task, names[i], tasks[i]): i
                    for i in range(len(tasks))}
            for f in cf.as_completed(futs):
                results[futs[f]] = f.result()
        final = [r for r in results if r is not None]
        if improve:
            for r in final:
                try:
                    self.improver.judge_and_learn(r.output, goal)
                except Exception as e:
                    log.debug("improve step failed: %s", e)
        return final  # type: ignore
