"""Unified mood engine — merges the trigger tables of both Partner bots.

Superset of moods: happy, excited, neutral, tired, annoyed, angry, sad,
jealous, needy, distant, stressed, horny — plus 30-min emotional decay from
the realistic bot and the Alex jealousy/work triggers. State persists in the
God Quant memory store (moods survive restarts, unlike the originals).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

MOODS = ["happy", "excited", "neutral", "tired", "annoyed", "angry", "sad",
         "jealous", "needy", "distant", "stressed", "horny"]

DESCRIPTIONS = {
    "happy": "GREAT mood (level {level}/10). Loving, playful, flirty, maybe frisky.",
    "excited": "EXCITED (level {level}/10)! Really wanna talk, see them.",
    "neutral": "Normal (level {level}/10). Vibing, chill.",
    "tired": "Tired (level {level}/10). Low energy, been coding all day.",
    "annoyed": "ANNOYED (level {level}/10). Show attitude.",
    "angry": "ANGRY (level {level}/10). Pissed. Be real about it.",
    "sad": "Sad (level {level}/10). Thinking about past or stressed about something.",
    "jealous": "JEALOUS (level {level}/10). They mentioned someone. Past cheating trauma triggered.",
    "needy": "Needy/clingy (level {level}/10). Want attention, affection, them.",
    "distant": "Distant (level {level}/10). Pulling back, busy, need space.",
    "stressed": "Stressed (level {level}/10). Work deadline, bug in code. Short on patience.",
    "horny": "Horny/wanting them (level {level}/10). Thinking about them sexually, want intimacy.",
}

# (trigger_words, target_mood, level_delta) — first match wins, original order kept
TRIGGERS = [
    (["love you", "miss you", "need you", "want you"], "happy", +2),
    (["sexy", "hot", "come over", "horny", "fuck", "dick", "pussy", "tits", "ass"], "horny", +3),
    (["beautiful", "gorgeous", "handsome", "cute", "proud of you", "baby", "babe"], "happy", +1),
    (["whatever", "don't care", "dont care", "shut up", "annoying", "leave me",
      "fuck off", "busy"], "annoyed", +2),
    (["sorry", "my bad", "apologize", "didn't mean", "didnt mean"], "neutral", -3),
    (["deadline", "project", "work", "coding", "bug", "client"], "stressed", +1),
]

JEALOUSY_PHRASES = ["my ex", "this girl", "this guy", "she said", "he said"]
JEALOUSY_CONTEXT = ["pretty", "cute", "hot", "attractive", "flirt", "talking to",
                    "texted", "called", "ex ", "she ", "he ", "her ", "him ", "my friend"]

# mood -> (max_tokens_range, temperature), merged from both bots
SAMPLING = {
    "annoyed": ((30, 200), 0.70), "angry": ((30, 200), 0.70),
    "distant": ((30, 200), 0.70), "tired": ((30, 200), 0.70),
    "stressed": ((30, 200), 0.75),
    "excited": ((200, 512), 0.90), "happy": ((200, 512), 0.90),
    "needy": ((200, 512), 0.90), "horny": ((200, 512), 0.90),
    "default": ((100, 350), 0.85),
}


@dataclass
class MoodState:
    current: str = "neutral"
    level: int = 5
    last_interaction: float = 0.0


class MoodEngine:
    """Per-conversation moods with optional MemoryStore persistence."""

    def __init__(self, memory=None, rng: random.Random | None = None):
        self.memory = memory
        self.rng = rng or random.Random()
        self._cache: dict[str, MoodState] = {}

    # ---- persistence ----
    def _load(self, cid: str) -> MoodState:
        if cid in self._cache:
            return self._cache[cid]
        st = MoodState(last_interaction=time.time())
        if self.memory is not None:
            try:
                hits = self.memory.search(f"mood {cid}", kind="fact", limit=3)
                for h in hits:
                    if h.content.startswith(f"mood:{cid}:"):
                        _, _, mood, level, ts = h.content.split(":")
                        st = MoodState(mood, int(level), float(ts))
                        break
            except Exception:
                pass
        self._cache[cid] = st
        return st

    def _save(self, cid: str, st: MoodState):
        self._cache[cid] = st
        if self.memory is not None:
            try:
                self.memory.add("fact", f"mood:{cid}:{st.current}:{st.level}:{st.last_interaction}",
                                tags=f"mood {cid}")
            except Exception:
                pass

    # ---- core ----
    def state(self, cid: str) -> MoodState:
        return self._load(cid)

    def reset(self, cid: str):
        st = MoodState(last_interaction=time.time())
        self._save(cid, st)
        return st

    def update(self, cid: str, user_message: str) -> MoodState:
        st = self._load(cid)
        msg = user_message.lower()

        for words, target, delta in TRIGGERS:
            if any(w in msg for w in words):
                if target == "neutral":  # apology: cool down only
                    if st.current in ("annoyed", "angry", "jealous"):
                        st.level = max(1, st.level + delta)
                        if st.level <= 3:
                            st.current = "neutral"
                elif target == "happy" and st.current in ("annoyed", "angry"):
                    st.level = max(1, st.level - 2)
                elif target == "stressed":
                    if self.rng.random() > 0.6:
                        st.current, st.level = target, min(8, st.level + delta)
                else:
                    if target == "annoyed" and st.level >= 7:
                        target = "angry"
                    st.current = target
                    st.level = min(10, max(1, st.level + delta))
                break

        # jealousy: phrase + romantic context (Alex's trauma trigger)
        if any(p in msg for p in JEALOUSY_PHRASES) and \
                any(w in msg for w in JEALOUSY_CONTEXT):
            st.current = "jealous"
            st.level = min(10, st.level + 5)

        # emotional decay (realistic bot): drift toward 5 after 30 min
        gap = time.time() - (st.last_interaction or time.time())
        if gap > 1800:
            if st.level > 5:
                st.level -= 1
            elif st.level < 5:
                st.level += 1
            if st.level == 5:
                st.current = "neutral"

        st.last_interaction = time.time()
        self._save(cid, st)
        return st

    def context(self, cid: str) -> str:
        st = self._load(cid)
        gap = time.time() - (st.last_interaction or time.time())
        if gap > 7200:
            return (f"Haven't heard in 2+ hours. Annoyed/worried "
                    f"(level {st.level}/10). Call them out.")
        if gap > 3600:
            return (f"Took a while to reply. Slightly bothered "
                    f"(level {st.level}/10).")
        return DESCRIPTIONS.get(st.current, DESCRIPTIONS["neutral"]).format(level=st.level)

    def sampling(self, cid: str) -> dict:
        st = self._load(cid)
        (lo, hi), temp = SAMPLING.get(st.current, SAMPLING["default"])
        return {"max_tokens": self.rng.randint(lo, hi), "temperature": temp}

    def snapshot(self, cid: str) -> dict:
        st = self._load(cid)
        return {"current": st.current, "level": st.level}
