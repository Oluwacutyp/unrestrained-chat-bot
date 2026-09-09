"""LLM router: ordered fallback chain across MANY providers + cost logging.

Chain = primary + cfg.llm_fallbacks + heuristic-always-last.
Each link resolves its OWN key from env (GROQ_API_KEY, HF_TOKEN, ...),
so one dead/slow/rate-limited provider never kills the bot — the next
link answers. Example: groq → huggingface → pollinations → heuristic.
"""
from __future__ import annotations

import copy
import logging
import time

from godquant.llm.providers import (LLMResponse, detect_provider, get_provider,
                                    key_for)

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

    def chain(self) -> list[str]:
        kinds = [self.primary]
        for f in (self.cfg.llm_fallbacks or []):
            f = str(f).strip().lower()
            if f and f not in kinds:
                kinds.append(f)
        if "heuristic" not in kinds:
            kinds.append("heuristic")  # always-last safety net, never fails
        return kinds

    def _build(self, kind: str):
        if kind == "heuristic":
            from godquant.llm.providers import HeuristicProvider
            return HeuristicProvider()
        cfg2 = copy.copy(self.cfg)
        cfg2.llm_provider = kind
        if kind != self.primary:
            cfg2.llm_model = ""  # fallbacks use their own sane defaults
        cfg2.llm_api_key = key_for(kind, self.cfg)
        return get_provider(cfg2)

    def _estimate_cost(self, resp: LLMResponse) -> float:
        pin, pout = PRICES_PER_1K.get(resp.model, (0.0, 0.0))
        return (resp.prompt_tokens / 1000) * pin + (resp.completion_tokens / 1000) * pout

    def complete(self, system: str, user: str, agent: str = "core",
                       images: list | None = None) -> LLMResponse:
        last_err: Exception | None = None
        for kind in self.chain():
            try:
                provider = self._build(kind)
                t0 = time.time()
                resp = provider.complete(system, user, images=images)
                resp.cost_usd = self._estimate_cost(resp)
                if self.memory is not None:
                    try:
                        self.memory.log_cost(agent, resp.provider, resp.model,
                                             resp.prompt_tokens,
                                             resp.completion_tokens,
                                             resp.cost_usd, time.time() - t0)
                    except Exception:
                        pass
                if kind != self.primary:
                    log.info("LLM answered via fallback: %s", kind)
                return resp
            except Exception as e:
                last_err = e
                log.warning("LLM %s failed, trying next: %s", kind, str(e)[:150])
        # unreachable in practice (heuristic never raises) — satisfy types
        raise RuntimeError(f"all LLM providers failed: {last_err}")
