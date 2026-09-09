"""Persona packs: personas as data, not code.

Built-in prompts stay as the reference fallback. A pack at
personas/<name>.md adds live sections; personas/<name>.learned.md holds
voice notes the bot writes about itself from owner verdicts (voice_drift).
Loader merges: built-in < pack < learned.

Pack format:
    blurb: one line
    ---
    # identity
    ...
    # voice
    ...
    # taboos
    ...
Only # voice / # taboos / # identity sections are read; the rest ignored.
"""
from __future__ import annotations

from pathlib import Path

MAX_LEARNED_LINES = 40
_SECTIONS = ("identity", "voice", "taboos")


def pack_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "personas"


def load_pack(name: str) -> dict | None:
    p = pack_dir() / f"{name.lower()}.md"
    if not p.exists():
        return None
    try:
        text = p.read_text()
    except Exception:
        return None
    head, _, body = text.partition("\n---\n")
    pack: dict = {"blurb": "", "identity": "", "voice": "", "taboos": ""}
    for line in head.splitlines():
        k, _, v = line.partition(":")
        if k.strip().lower() == "blurb":
            pack["blurb"] = v.strip()
    cur: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        low = line.strip().lower()
        if low in ("# identity", "# voice", "# taboos"):
            if cur:
                pack[cur] = "\n".join(buf).strip()[:3000]
            cur = low[2:]
            buf = []
        elif cur:
            buf.append(line)
    if cur:
        pack[cur] = "\n".join(buf).strip()[:3000]
    return pack


def learned_path(name: str) -> Path:
    return pack_dir() / f"{name.lower()}.learned.md"


def read_learned(name: str) -> str:
    try:
        return learned_path(name).read_text().strip()[:2000]
    except Exception:
        return ""


def voice_drift(name: str, note: str) -> bool:
    """Append one self-observation (deduped, capped). Returns True if new."""
    note = " ".join((note or "").split())[:200]
    if not note:
        return False
    lp = learned_path(name)
    try:
        lines = lp.read_text().splitlines() if lp.exists() else []
    except Exception:
        lines = []
    if note in (ln.strip() for ln in lines):
        return False
    lines.append(note)
    try:
        pack_dir().mkdir(parents=True, exist_ok=True)
        lp.write_text("\n".join(lines[-MAX_LEARNED_LINES:]) + "\n")
    except Exception:
        return False
    return True


def render_pack(name: str, base_prompt: str) -> str:
    pack = load_pack(name)
    if not pack:
        return base_prompt
    out = base_prompt
    if pack["identity"]:
        out += f"\n[LIVE IDENTITY]\n{pack['identity']}"
    if pack["voice"]:
        out += f"\n[VOICE PACK]\n{pack['voice']}"
    if pack["taboos"]:
        out += f"\n[TABOOS]\n{pack['taboos']}"
    learned = read_learned(name)
    if learned:
        out += f"\n[SELF-LEARNED VOICE]\n{learned}"
    return out


def scaffold(name: str, blurb: str = "") -> Path:
    p = pack_dir() / f"{name.lower()}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"blurb: {blurb or name}\n---\n\n# identity\n\n"
                 f"# voice\n\n# taboos\n")
    return p
