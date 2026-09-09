"""CompanionAgent — the 7th agent. A Partner-bot soul with God Quant tools.

Pipeline per message (mirrors the originals, upgraded):
  mood.update → bond.note (relationship) → history+summary
  → auto tools (search/backtest/risk) → persona+relationship render
  → LLM → persist → reply (+mood snapshot)

Conversation history persists in the memory store (originals lost it on
restart). Tools let the companion answer "how's BTC?" with REAL backtests.
"""
from __future__ import annotations

import json
import re
import time

from godquant.agents.base import AgentResult, AgentTask, BaseAgent
from godquant.companion import web_search as WS
from godquant.companion.mood import MoodEngine
from godquant.companion.personas import get_persona, render_persona
from godquant.companion.memory_engine import (clean_reply, dossier_text,
                                             extract_facts, fallback_line)
from godquant.companion.learner import (extract_facts_llm,
                                          style_lessons)
from godquant.companion.relationship import (BondStore, addressing,
                                            parse_bond_id, vibe_context)
from godquant.memory.mind import Mind
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
        self.bonds = BondStore(cfg.resolved_memory_db())
        self.mind = Mind(memory, self.bonds)

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

    def recall(self, cid: str, query: str) -> str:
        """Older-than-history memories (rolling summaries for this chat)."""
        try:
            hits = self.memory.search("summary", kind="summary", limit=10)
        except Exception:
            return ""
        for m in hits:
            if cid in (m.tags or ""):
                return ("\n[OLDER MEMORIES — things from earlier chats, still true]\n"
                        f"{m.content[:900]}\n")
        return ""

    def summarize_if_due(self, cid: str, history: list[dict]):
        """Rolling memory: every ~25 msgs, compress older chat to a summary."""
        if self.cfg.offline or len(history) < 30:
            return
        try:
            have = [m for m in self.memory.search("summary", kind="summary",
                                                 limit=20)
                    if cid in (m.tags or "")]
        except Exception:
            return
        if len(history) < 30 + len(have) * 25:
            return
        chunk = "\n".join(
            f"{'Them' if m['role'] == 'user' else 'You'}: {m['content'][:300]}"
            for m in history[:20])
        try:
            text = self.router.complete(
                "Compress this chat into durable memories: names, facts, promises, "
                "fights, inside jokes, feelings. Terse bullet lines, no fluff. "
                "End with a line LESSON: <one terse lesson about THEM or how to "
                "handle them, or the word none>.",
                chunk, agent=self.name).text.strip()
        except Exception:
            return
        lines = [ln.strip() for ln in text.split("\n")]
        lesson = ""
        if lines and lines[-1].upper().startswith("LESSON:"):
            lesson = lines[-1][7:].strip()
            text = "\n".join(lines[:-1]).strip()
        if text:
            try:
                self.memory.add("summary", text[:1500], tags=f"summary {cid}")
            except Exception:
                pass
        if lesson and lesson.lower() != "none":
            try:
                self.memory.add_lesson(lesson[:300], tags=f"lesson {cid}")
            except Exception:
                pass

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
             image_data: str | None = None, sender_name: str | None = None,
             is_group: bool = False, bond_id: str | None = None) -> dict:
        persona = persona or self.cfg.persona
        get_persona(persona)  # validates, raises on unknown

        mood_state = self.moods.update(cid, message)
        mood_ctx = self.moods.context(cid)
        history = self._load_history(cid)
        summary = self.history_summary(history)

        # relationship: closeness gates pet names (never "babe" for strangers)
        bch, bwho = parse_bond_id(bond_id or cid)
        prev = self.bonds.get(bch, bwho)
        gap = (time.time() - prev["last_ts"]) if prev["last_ts"] else None
        bond = self.bonds.note_message(bch, bwho, message)
        if bond["spam"] and bond["friction"] >= 30 and \
                mood_state.current in ("neutral", "happy", "needy"):
            mood_state = self.moods.nudge(cid, "annoyed", +1)
            mood_ctx = self.moods.context(cid)
        rel_block = addressing(bond["level"], sender_name or bwho, is_group,
                               bond)
        vibe_block = vibe_context(message, gap)
        rx = extract_facts(message)
        if not rx and bond["substance"] >= 0.35 and not bond["spam"] \
                and not self.cfg.offline:
            try:  # natural phrasing ("I'm Chidi btw") — let the model read it
                rx = extract_facts_llm(self.router, message)
            except Exception:
                rx = []
        for _k, _v in rx:
            try:
                self.bonds.add_fact(bch, bwho, _k, _v)
            except Exception:
                pass
        if bond["substance"] >= 0.5 and not bond["spam"]:
            try:  # weighty moments become episodic memory
                self.mind.log_episode(f"{bch}:{bwho}", sender_name or bwho,
                                      message[:300], bond["substance"])
            except Exception:
                pass
        if len(history) >= 6:  # learn texting style from every chat
            try:
                for _k, _v in style_lessons(history):
                    self.bonds.add_fact(bch, bwho, _k, _v)
            except Exception:
                pass
        _facts = self.bonds.get_facts(bch, bwho)
        try:  # pinned + important facts first in the dossier
            _meta = {(m["key"], m["value"]): (m["pinned"], m["importance"])
                     for m in self.bonds.facts_meta(bch, bwho)}
            _facts = sorted(_facts, key=lambda kv: _meta.get(kv, (False, 0.5)),
                            reverse=True)
        except Exception:
            pass
        dossier_block = dossier_text(_facts, sender_name or bwho)

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
        system += "\n" + rel_block
        if vibe_block:
            system += "\n" + vibe_block
        if dossier_block:
            system += "\n" + dossier_block
        mem_block = self.recall(cid, message)
        if mem_block:
            system += mem_block
        try:  # unified recall: global + this chat's knowledge
            system += self.mind.recall_block(message, scope=f"{bch}:{bwho}")
        except Exception:
            pass
        convo = "\n".join(f"{'Them' if m['role'] == 'user' else 'You'}: {m['content'][:500]}"
                          for m in history[-8:])
        mood_tag = f"\n[MOOD: {mood_state.current} {mood_state.level}/10]"
        user_block = (f"{convo}\nThem: {message}{image_ctx}{tool_ctx}{mood_tag}"
                      if convo else f"Them: {message}{image_ctx}{tool_ctx}{mood_tag}")

        sampling = self.moods.sampling(cid)
        text = self.router.complete(system, user_block, agent=self.name).text
        text = clean_reply(text or "", message)
        if not text.strip():  # empty reply: nudge once, never placeholder
            text = self.router.complete(
                system, user_block + "\n[SYSTEM: your reply came back empty — "
                "you MUST send a real in-character text now.]",
                agent=self.name).text
            text = clean_reply(text or "", message)
        if not text.strip():
            text = fallback_line(persona, message)

        self._save_msg(cid, "user", message)
        self._save_msg(cid, "assistant", text)
        self.summarize_if_due(cid, history)
        return {"response": text,
                "mood": mood_state.current,
                "mood_level": mood_state.level,
                "persona": persona,
                "sampling": sampling,
                "bond": bond["level"],
                "bond_count": bond["count"],
                "bond_score": bond["score"],
                "friction": bond["friction"],
                "streak": bond["streak"],
                "substance": bond["substance"],
                "warmth": bond["warmth"]}

    def proactive_opener(self, cid: str, persona: str | None = None,
                         silence_s: int = 3600, display: str | None = None) -> str:
        """Generate a TEXT-FIRST opener (they've been silent). Saved as our msg."""
        persona = persona or self.cfg.persona
        get_persona(persona)
        mood_ctx = self.moods.context(cid)
        history = self._load_history(cid)
        summary = self.history_summary(history)
        bch, bwho = parse_bond_id(cid)
        bond = self.bonds.get(bch, bwho)
        rel_block = addressing(bond["level"], display or bwho, False, bond)
        system = render_persona(persona, mood_context=mood_ctx,
                                history_summary=summary)
        system += "\n" + rel_block
        hrs = silence_s / 3600
        if hrs < 1.5:
            situ = "They went quiet ~an hour ago. Send ONE short check-in text."
        elif hrs < 5:
            situ = (f"They've been silent {hrs:.0f} hours. You're noticing. "
                    f"Text them first — feelings depend on your CURRENT MOOD.")
        else:
            situ = (f"They've ignored you for {hrs:.0f} hours. React in character "
                    f"to your CURRENT MOOD (worried? mad? cold? needy?).")
        nudge = (f"[YOU text THEM first — this is YOUR outbound message, not a reply. "
                 f"{situ} Keep it to 1-2 short texts max, in your texting style. "
                 f"NEVER narrate or explain, just the text itself.]")
        try:
            system += self.memory.lesson_context("proactive texting opener")
        except Exception:
            pass
        text = self.router.complete(system, nudge, agent=self.name).text.strip()
        # strip any accidental narration/prefix the model adds
        for prefix in ("suggested text:", "message:", "text:"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip().strip('"')
        self._save_msg(cid, "assistant", text)
        return text

    def reset(self, cid: str = "default"):
        self.moods.reset(cid)
        return {"message": "Fresh start"}

    def execute(self, task: AgentTask) -> AgentResult:
        ctx = task.context
        out = self.chat(task.instruction,
                        cid=ctx.get("conversation_id", "default"),
                        persona=ctx.get("persona"),
                        use_search=ctx.get("use_search", False),
                        image_data=ctx.get("image"),
                        sender_name=ctx.get("sender_name"),
                        is_group=ctx.get("is_group", False),
                        bond_id=ctx.get("bond_id"))
        return AgentResult(agent=self.name, ok=True, output=out["response"],
                           artifacts=out)
