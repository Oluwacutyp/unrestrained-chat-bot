"""Agent primitives: tasks, results, and the lesson-aware base agent."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("godquant.agents")


@dataclass
class AgentTask:
    instruction: str
    context: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)


@dataclass
class AgentResult:
    agent: str
    ok: bool
    output: str
    artifacts: dict = field(default_factory=dict)
    score: float = 0.0
    elapsed: float = 0.0
    error: str = ""


class BaseAgent:
    name = "base"
    system_prompt = "You are a helpful agent."

    def __init__(self, cfg, router, memory):
        self.cfg = cfg
        self.router = router
        self.memory = memory

    def ask(self, instruction: str, extra_system: str = "") -> str:
        """LLM call auto-injected with relevant learned lessons."""
        lessons = ""
        try:
            lessons = self.memory.lesson_context(instruction)
        except Exception:
            pass
        system = self.system_prompt + lessons
        if extra_system:
            system += "\n" + extra_system
        return self.router.complete(system, instruction, agent=self.name).text

    def execute(self, task: AgentTask) -> AgentResult:
        raise NotImplementedError

    def run(self, task: AgentTask) -> AgentResult:
        t0 = time.time()
        try:
            res = self.execute(task)
            res.elapsed = time.time() - t0
            return res
        except Exception as e:
            log.exception("%s failed", self.name)
            return AgentResult(agent=self.name, ok=False, output="",
                               error=str(e)[:500], elapsed=time.time() - t0)
