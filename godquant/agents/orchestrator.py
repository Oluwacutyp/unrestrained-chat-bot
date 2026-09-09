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


def _waves(deps_list: list[list[int]]) -> list[list[int]]:
    """Topological waves over index deps. Invalid refs ignored; cycles and
    leftovers run last (never dropped, never deadlocked)."""
    n = len(deps_list)
    done: set[int] = set()
    waves: list[list[int]] = []
    while len(done) < n:
        wave = [i for i in range(n)
                if i not in done and all(
                    (not isinstance(d, int) or d < 0 or d >= n or d in done)
                    for d in (deps_list[i] or []))]
        if not wave:  # cycle → flush leftovers
            wave = [i for i in range(n) if i not in done]
        waves.append(wave)
        done.update(wave)
    return waves


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
        if any(k in g for k in ("chat", "talk to", "partner", "companion", "alex",
                                "say ", "tell me about yourself", "wyd", "miss you")):
            return [AgentTask(goal, dict(context))]
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
        if any(k in g for k in ("chat", "talk to", "partner", "companion", "alex",
                                "say ", "tell me about yourself", "wyd", "miss you")):
            return "companion"
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
        # dependency waves: independent steps parallel, dependents wait
        results: list[AgentResult | None] = [None] * len(tasks)
        with cf.ThreadPoolExecutor(max_workers=self.cfg.max_workers) as ex:
            for wave in _waves([t.depends_on for t in tasks]):
                futs = {ex.submit(self.run_task, names[i], tasks[i]): i
                        for i in wave}
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

    # ---------- missions v2: persistent, resumable, sub-agents ----------
    def run_mission(self, goal: str, context: dict | None = None,
                    report_to: str = "", depth: int = 0,
                    mission_id: int | None = None, _store=None) -> dict:
        from godquant.agents.missions import MissionStore
        store = _store or MissionStore(self.cfg.resolved_memory_db())
        context = context or {}
        if mission_id:
            m = store.get(mission_id)
            if not m:
                return {"error": "no such mission"}
            goal, steps, mid = m["goal"], m["steps"], mission_id
        else:
            tasks = self.plan(goal, context)
            steps = [{"agent": self._assign(i, goal),
                      "instruction": t.instruction,
                      "depends_on": t.depends_on}
                     for i, t in enumerate(tasks)]
            m = store.create(goal, steps, context, report_to)
            mid, steps = m["id"], m["steps"]
        order = [i for wave in _waves([s.get("depends_on", [])
                                       for s in steps]) for i in wave]
        for i in order:
            if steps[i]["status"] in ("ok", "fail", "skip"):
                continue
            instr = steps[i]["instruction"]
            try:
                if instr.strip().lower().startswith("spawn:") and depth < 1:
                    sub = self.run_mission(instr[6:].strip(), context, "",
                                           depth + 1, _store=store)
                    out = (f"[SUB-MISSION #{sub.get('id')}] "
                           f"{sub.get('summary', '')[:2000]}")
                    ok = sub.get("status") == "done"
                else:
                    res = self.run_task(steps[i]["agent"],
                                        AgentTask(instr, dict(context)))
                    out, ok = res.output, res.ok
                    if ok and steps[i]["agent"] != "reviewer":
                        out, ok = self._critique(steps[i]["agent"], instr,
                                                 out, goal, context)
                store.save_step(mid, i, "ok" if ok else "fail", out)
                steps[i]["status"] = "ok" if ok else "fail"
            except Exception as e:
                store.save_step(mid, i, "fail", str(e)[:500])
                steps[i]["status"] = "fail"
        failed = [s for s in steps if s["status"] == "fail"]
        store.finish(mid, "done" if not failed else "failed")
        if getattr(self.cfg, "collect", True):
            try:
                from godquant.train.collector import TrajectoryLogger
                TrajectoryLogger(self.cfg.resolved_workspace()).log_mission(
                    goal, "done" if not failed else "failed",
                    "\n".join((s2["result"] or "")[:500] for s2 in steps))
            except Exception:
                pass
        summary = "\n\n".join(f"[{s['agent']}] {(s['result'] or '')[:1500]}"
                                for s in steps)
        try:
            self.improver.judge_and_learn(summary[:4000], goal)
        except Exception:
            pass
        return {"id": mid, "status": "done" if not failed else "failed",
                "summary": summary[:4000]}

    def _critique(self, agent: str, instruction: str, output: str,
                  goal: str, context: dict) -> tuple:
        """Reviewer gate: score<40 → single retry with critique attached."""
        try:
            rev = self.run_task(
                "reviewer",
                AgentTask(f"Score 0-100 + APPROVE/REVISE verdict for work "
                          f"toward '{goal}':\n{output[:2000]}"))
            if rev.score >= 40 or "APPROVE" in (rev.output or "").upper():
                return output, True
            retry = self.run_task(
                agent, AgentTask(
                    instruction + "\n\n[REVIEWER CRITIQUE — address this]:\n"
                    + (rev.output or "")[:1000], dict(context)))
            return retry.output, retry.ok
        except Exception:
            return output, True

    def resume_mission(self, mission_id: int) -> dict:
        return self.run_mission("", mission_id=mission_id)
