"""Training-data collection: trajectories + preferences as local JSONL.

The personal-model asset lives on the phone first: every chat and mission
can append a trajectory; `.good`/`.bad` bank preference pairs. Nothing
leaves the device except via explicit export (`export.build_packs`) or
push (`hf_pipe.push_files`). Toggle with GQ_COLLECT=0.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class TrajectoryLogger:
    def __init__(self, workspace: str | Path):
        self.dir = Path(workspace) / "train"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.traj = self.dir / "trajectories.jsonl"
        self.prefs = self.dir / "prefs.jsonl"

    def _append(self, path: Path, record: dict):
        try:
            with open(path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass

    def log_chat(self, user: str, response: str, persona: str = "",
                 cid: str = "", mood: str = "", bond: int = 0,
                 provider: str = "", model: str = "", pt: int = 0,
                 ct: int = 0, cost: float = 0.0):
        self._append(self.traj, {"ts": time.time(), "kind": "chat",
                                 "cid": cid, "persona": persona,
                                 "user": (user or "")[:2000],
                                 "response": (response or "")[:4000],
                                 "mood": mood, "bond": bond,
                                 "provider": provider, "model": model,
                                 "pt": pt, "ct": ct, "cost": cost})

    def log_mission(self, goal: str, status: str, summary: str):
        self._append(self.traj, {"ts": time.time(), "kind": "mission",
                                 "goal": (goal or "")[:500],
                                 "status": status,
                                 "summary": (summary or "")[:4000]})

    def log_pref(self, prompt: str, verdict: str, reply: str):
        """verdict good → chosen=reply; bad → rejected=reply."""
        v = (verdict or "").lower()
        if v not in ("good", "bad"):
            return
        self._append(self.prefs, {"ts": time.time(), "prompt": prompt[:2000],
                                  "chosen": reply[:4000] if v == "good" else "",
                                  "rejected": reply[:4000] if v == "bad" else ""})

    def log_history(self, user: str, response: str, chat: str = "",
                    chat_type: str = ""):
        self._append(self.traj, {"ts": time.time(), "kind": "history",
                                 "chat": (chat or "")[:120],
                                 "chat_type": chat_type,
                                 "user": (user or "")[:2000],
                                 "response": (response or "")[:4000]})

    def stats(self) -> dict:
        out = {"trajectories": 0, "prefs": 0, "pairs": 0, "bytes": 0}
        try:
            if self.traj.exists():
                out["bytes"] += self.traj.stat().st_size
                for ln in self.traj.read_text().splitlines():
                    if ln.strip():
                        out["trajectories"] += 1
            if self.prefs.exists():
                out["bytes"] += self.prefs.stat().st_size
                for ln in self.prefs.read_text().splitlines():
                    if not ln.strip():
                        continue
                    out["prefs"] += 1
                    try:
                        d = json.loads(ln)
                        if d.get("chosen") and d.get("rejected"):
                            out["pairs"] += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return out
