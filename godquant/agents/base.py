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

    def ask_with_tools(self, instruction: str, tools: list[str] | None = None,
                       max_steps: int = 3) -> str:
        """Agentic loop: model emits {"tool":..} or FINAL:. Heuristic-safe —
        a plain answer (no tool JSON) returns immediately, so offline
        behavior is identical to ask()."""
        import json as _js
        import re as _re
        from godquant.tools.registry import TOOLS, run_tool, tool_list
        tools = [t for t in (tools or []) if t in TOOLS]
        if not tools:
            return self.ask(instruction)
        sys = (self.system_prompt +
               "\n[TOOLS — reply with ONE json line {\"tool\": name, "
               "\"args\": {...}} to act, or FINAL: <answer> when done]\n" +
               tool_list(tools))
        convo, last = instruction, ""
        for _ in range(max_steps):
            last = self.router.complete(sys, convo, agent=self.name).text or ""
            call = _extract_tool_call(last)
            if not isinstance(call, dict) or call.get("tool") not in tools:
                return _re.sub(r"^FINAL:\s*", "", last).strip()
            res = run_tool(call["tool"], call.get("args") or {},
                           {"memory": self.memory,
                            "mind": getattr(self, "mind", None),
                            "cfg": self.cfg, "repo": ".",
                            "allow_dangerous": bool(
                                getattr(self.cfg, "allow_exec", False))})
            convo += (f"\n[TOOL {call['tool']} → "
                      f"{'OK' if res.get('ok') else 'FAIL'}]\n"
                      f"{(res.get('output') or res.get('error'))[:1500]}\n"
                      f"Continue with another tool call or FINAL:.")
        return _re.sub(r"^FINAL:\s*", "", last).strip()

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


def _extract_tool_call(text: str):
    """First balanced {...} containing a "tool" key → dict, else None."""
    import json as _js
    i = (text or "").find('{"tool"')
    if i < 0:
        i = (text or "").find("{\"tool\"")
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    call = _js.loads(text[i:j + 1])
                except Exception:
                    return None
                return call if isinstance(call, dict) else None
    return None

