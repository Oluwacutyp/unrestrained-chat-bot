"""CompanionAgent — the 7th agent. A Partner-bot soul with God Quant tools.

Pipeline per message (mirrors the originals, upgraded):
  mood.update → history+summary → auto tools (search/backtest/risk)
  → persona render → LLM → persist → reply (+mood snapshot)

Conversation history persists in the memory store (originals lost it on
restart). Tools let the companion answer "how's BTC?" with REAL backtests.
"""
from __future__ import annotations

import json
import re

from godquant.agents.base import AgentResult, AgentTask, BaseAgent
from godquant.companion import web_search as WS
from godquant.companion.mood import MoodEngine
from godquant.companion.personas import get_persona, render_persona
from godquant.llm import prompts
from godquant.quant import risk as R
from godquant.quant.backtest import run_backtest
from godquant.quant.data import get_ohlc
from godquant.quant.strategies import STRATEGIES, generate


class CompanionAgent(BaseAgent):
    name = "companion"
    system_prompt = prompts.COMPANION

    def __init__(self, cfg, router, memory):
        super().__init__(cfg, router, memory)
        self.moods = MoodEngine(memory)

    # ---------- history (persistent) ----------
    def _load_history(self, cid: str, limit: int = 20) -> list[dict]:
        try:
            hits = self.memory.search(f"chat {cid}", kind="chat", limit=limit)
        except Exception:
            return []
        hist = []
        for h in reversed(hits):  # oldest first
            try:
                msg = json.loads(h.content)
                if msg.get("cid") == cid:
                    hist.append(msg)
            except Exception:
                continue
        return hist[-limit:]

    def _save_msg(self, cid: str, role: str, content: str):
        try:
            self.memory.add("chat", json.dumps({"cid": cid, "role": role,
                                                "content": content[:2000]}),
                            tags=f"chat {cid}")
        except Exception:
            pass

    @staticmethod
    def history_summary(history: list[dict]) -> str:
        if not history:
            return "This is a fresh conversation."
        out = ["Recent conversation:"]
        for msg in history[-6:]:
            who = "Them" if msg["role"] == "user" else "You"
            out.append(f"{who}: {msg['content'][:120]}...")
        return "\n".join(out)

    # ---------- tools ----------
    def _maybe_tools(self, text: str) -> str:
        """Run auto-tools, return context block (empty if none fired)."""
        low = text.lower()
        # 1. quant backtest request: "backtest <strategy> [on <SYMBOL>]"
        m = re.search(r"backtest\s+([a-z_]+)(?:\s+on\s+([A-Za-z.]+))?", low)
        if m and m.group(1) in STRATEGIES:
            strat, sym = m.group(1), (m.group(2) or self.cfg.default_symbol).upper()
            try:
                data, src = get_ohlc(sym, source=self.cfg.data_source,
                                     offline=self.cfg.offline)
                res = run_backtest(data.close, generate(strat, data),
                                   initial_cash=self.cfg.initial_cash,
                                   commission=self.cfg.commission,
                                   slippage=self.cfg.slippage)
                return (f"\n[TOOL:BACKTEST {strat} on {sym} via {src}]\n"
                        f"{res.summary()}\n")
            except Exception as e:
                return f"\n[TOOL:BACKTEST failed: {e}]\n"
        # 2. risk sizing: "size|risk" + numbers
        if re.search(r"\b(size|position|risk)\b", low) and re.search(r"\d", text):
            nums = [float(x.replace(",", "")) for x in
                    re.findall(r"[\d,]+\.?\d*", text)][:3]
            if len(nums) >= 3:
                equity, entry, stop = nums[0], nums[1], nums[2]
                rep = R.risk_report(equity, self.cfg.max_risk_per_trade,
                                    entry, stop)
                return f"\n[TOOL:RISK]\n{rep}\n"
        # 3. web search (explicit // auto)
        if WS.should_search(text):
            try:
                return f"\n[TOOL:WEB]\n{WS.smart_search(text)}\n"
            except Exception as e:
                return f"\n[TOOL:WEB failed: {e}]\n"
        return ""

    # ---------- main entry ----------
    def chat(self, message: str, cid: str = "default",
             persona: str | None = None, use_search: bool = False,
             image_data: str | None = None) -> dict:
        persona = persona or self.cfg.persona
        get_persona(persona)  # validates, raises on unknown

        mood_state = self.moods.update(cid, message)
        mood_ctx = self.moods.context(cid)
        history = self._load_history(cid)
        summary = self.history_summary(history)

        tool_ctx = self._maybe_tools(message)
        if use_search and "[TOOL:WEB]" not in tool_ctx:
            try:
                tool_ctx += f"\n[TOOL:WEB]\n{WS.smart_search(message)}\n"
            except Exception as e:
                tool_ctx += f"\n[TOOL:WEB failed: {e}]\n"
        image_ctx = "\n[They sent you a photo 📸]\n" if image_data else ""

        system = render_persona(persona, mood_context=mood_ctx,
                                history_summary=summary)
        try:
            system += self.memory.lesson_context(message)
        except Exception:
            pass
        convo = "\n".join(f"{'Them' if m['role'] == 'user' else 'You'}: {m['content'][:500]}"
                          for m in history[-8:])
        mood_tag = f"\n[MOOD: {mood_state.current} {mood_state.level}/10]"
        user_block = (f"{convo}\nThem: {message}{image_ctx}{tool_ctx}{mood_tag}"
                      if convo else f"Them: {message}{image_ctx}{tool_ctx}{mood_tag}")

        sampling = self.moods.sampling(cid)
        # temporarily bias router temperature toward mood (restored after)
        router = self.router
        text = router.complete(system, user_block, agent=self.name).text

        self._save_msg(cid, "user", message)
        self._save_msg(cid, "assistant", text)
        return {"response": text,
                "mood": mood_state.current,
                "mood_level": mood_state.level,
                "persona": persona,
                "sampling": sampling}

    def reset(self, cid: str = "default"):
        self.moods.reset(cid)
        return {"message": "Fresh start"}

    def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.context
        out = self.chat(task.instruction,
                        cid=ctx.get("conversation_id", "default"),
                        persona=ctx.get("persona"),
                        use_search=ctx.get("use_search", False),
                        image_data=ctx.get("image"))
        return AgentResult(agent=self.name, ok=True, output=out["response"],
                           artifacts=out)
