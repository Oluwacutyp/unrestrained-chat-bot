"""LLM router: primary provider + graceful fallback chain + cost logging."""
from __future__ import annotations

import logging
import time

from godquant.llm.providers import LLMResponse, detect_provider, get_provider

log = logging.getLogger("godquant.llm")

PRICES_PER_1K = {  # rough USD, used only for budget display
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "llama-3.3-70b-versatile": (0.00059, 0.00079),
    "deepseek-chat": (0.00014, 0.00028),
    "claude-3-5-haiku-latest": (0.0008, 0.004),
    "gemini-2.0-flash": (0.0001, 0.0004),
}


class LLMRouter:
    def __init__(self, cfg, memory=None):
        self.cfg = cfg
        self.memory = memory
        self.primary = detect_provider(cfg)

    def _estimate_cost(self, resp: LLMResponse) -> float:
        pin, pout = PRICES_PER_1K.get(resp.model, (0.0, 0.0))
        return (resp.prompt_tokens / 1000) * pin + (resp.completion_tokens / 1000) * pout

    def complete(self, system: str, user: str, agent: str = "core") -> LLMResponse:
        provider = get_provider(self.cfg)
        t0 = time.time()
        try:
            resp = provider.complete(system, user)
        except Exception as e:
            log.warning("primary LLM %s failed: %s — falling back to heuristic",
                        self.primary, e)
            if self.primary == "heuristic":
                raise
            from godquant.llm.providers import HeuristicProvider
            resp = HeuristicProvider().complete(system, user)
        resp.cost_usd = self._estimate_cost(resp)
        latency = time.time() - t0
        if self.memory is not None:
            try:
                self.memory.log_cost(agent, resp.provider, resp.model,
                                     resp.prompt_tokens, resp.completion_tokens,
                                     resp.cost_usd, latency)
            except Exception:
                pass
        return resp
