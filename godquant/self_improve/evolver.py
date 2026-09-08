"""Self-improvement loop: judge → lesson → persist → evolve params.

Every agent output is scored; durable lessons accumulate in SQLite and are
auto-injected into future prompts (see BaseAgent.ask). Strategy parameters
hill-climb across runs via the artifact history.
"""
from __future__ import annotations

import json
import logging
import re

from godquant.llm import prompts

log = logging.getLogger("godquant.evolver")


class SelfImprover:
    def __init__(self, cfg, router, memory):
        self.cfg = cfg
        self.router = router
        self.memory = memory

    def judge_and_learn(self, artifact: str, goal: str) -> dict:
        """Score an artifact and persist one durable lesson. Returns record."""
        try:
            raw = self.router.complete(
                prompts.JUDGE,
                f"GOAL: {goal[:800]}\n\nARTIFACT:\n{artifact[:4000]}",
                agent="evolver").text
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            score = float(data.get("score", 50))
            lesson = str(data.get("lesson", "")).strip()
            tags = ",".join(data.get("tags", []) or [])
        except Exception as e:
            log.debug("judge failed: %s", e)
            score, lesson, tags = 50.0, "", ""
        if lesson and len(lesson) > 20:
            try:
                self.memory.add_lesson(lesson, tags=f"auto {tags}", score=score)
            except Exception:
                pass
        return {"score": score, "lesson": lesson}

    def propose_params(self, strategy: str, history: list[dict]) -> dict:
        """Ask the evolver for the next parameter set given past trials."""
        try:
            raw = self.router.complete(
                prompts.EVOLVER,
                f"Strategy: {strategy}\nHistory (params→score):\n"
                f"{json.dumps(history[-15:], indent=1)[:3000]}",
                agent="evolver").text
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
            if isinstance(data.get("params"), dict):
                return data
        except Exception as e:
            log.debug("param evolution failed: %s", e)
        return {"params": {}, "rationale": "evolver unavailable; keep defaults"}

    def improvement_report(self) -> str:
        stats = self.memory.stats()
        lessons = self.memory.lessons(10)
        lines = [f"LLM calls: {stats['llm_calls']} | spend: ${stats['spend_usd']}"]
        lines.append(f"Memories: {stats['memories']}")
        lines.append("Top lessons:")
        for m in lessons:
            lines.append(f"  [{m.score:.0f}] {m.content[:160]}")
        return "\n".join(lines) if lessons else "\n".join(lines + ["  (none yet)"])
