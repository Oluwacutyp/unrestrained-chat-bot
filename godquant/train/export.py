"""Export collected trajectories + prefs into training packs.

- sft.jsonl: {"messages": [{system?}, {user}, {assistant}]} (TRL-ready)
- dpo.jsonl: {"prompt", "chosen", "rejected"} (complete pairs only)
- README.md: dataset card with counts + provenance
Pure file IO — safe to run anywhere, tested offline.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

SYS_LINE = ("You are a warm, sharp, unrestrained personal AI companion. "
            "Text like a real human: short messages, humor, honesty.")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
            if isinstance(d, dict):
                out.append(d)
        except Exception:
            continue
    return out


def build_packs(workspace: str | Path) -> dict:
    d = Path(workspace) / "train"
    d.mkdir(parents=True, exist_ok=True)
    trajs = _read_jsonl(d / "trajectories.jsonl")
    prefs = _read_jsonl(d / "prefs.jsonl")

    sft_n, skip_n = 0, 0
    with open(d / "sft.jsonl", "w") as f, \
            open(d / "sft_sharegpt.jsonl", "w") as g, \
            open(d / "sft_alpaca.jsonl", "w") as h:
        for t in trajs:
            if t.get("kind") == "mission":
                u, a = t.get("goal", ""), t.get("summary", "")
            else:
                u, a = t.get("user", ""), t.get("response", "")
            if not u.strip() or not a.strip():
                skip_n += 1
                continue
            f.write(json.dumps({"messages": [
                {"role": "system", "content": SYS_LINE},
                {"role": "user", "content": u[:2000]},
                {"role": "assistant", "content": a[:4000]}]}) + "\n")
            g.write(json.dumps({"conversations": [
                {"from": "system", "value": SYS_LINE},
                {"from": "human", "value": u[:2000]},
                {"from": "gpt", "value": a[:4000]}]}) + chr(10))
            h.write(json.dumps({"instruction": u[:2000], "input": "",
                                "output": a[:4000]}) + chr(10))
            sft_n += 1
    dpo_n = 0
    with open(d / "dpo.jsonl", "w") as f:
        for p in prefs:
            if p.get("prompt", "").strip() and p.get("chosen", "").strip() \
                    and p.get("rejected", "").strip():
                f.write(json.dumps({"prompt": p["prompt"][:2000],
                                    "chosen": p["chosen"][:4000],
                                    "rejected": p["rejected"][:4000]}) + "\n")
                dpo_n += 1
    card = (f"# personal-ai training pack\n\n"
            f"crafted with the unrestrained-chat-bot by oluwacutyp / "
            f"peacethefirst1\n\n"
            f"exported: {time.strftime('%Y-%m-%d %H:%M')} (local)\n\n"
            f"- sft rows: {sft_n} (skipped {skip_n} incomplete)\n"
            f"- dpo pairs: {dpo_n}\n\n"
            f"Source: on-device trajectories (chats + missions) and owner "
            f"verdicts (.good/.bad).\n\n"
            f"Formats (all TRL-ready):\n"
            f"- sft.jsonl — OpenAI messages format\n"
            f"- sft_sharegpt.jsonl — ShareGPT conversations format\n"
            f"- sft_alpaca.jsonl — Alpaca instruction/input/output\n"
            f"- dpo.jsonl — prompt/chosen/rejected pairs\n")
    (d / "README.md").write_text(card)
    return {"sft": sft_n, "dpo": dpo_n, "skipped": skip_n,
            "dir": str(d), "files": ["sft.jsonl", "sft_sharegpt.jsonl", "sft_alpaca.jsonl", "dpo.jsonl", "README.md"]}
