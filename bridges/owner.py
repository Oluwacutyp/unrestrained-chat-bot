"""Shared owner-command core for the Python bridges (Telegram + Discord).

One systematic implementation, injected `api` + `channel`. Bridges keep only
their client-specific bits (.import needs the live client) and delegate here.
"""
from __future__ import annotations

PERSONAS = ("devon", "alex", "companion", "realistic", "quant")

HELP = {
    "telegram": ("userbot cmds: `.mission <goal>` `.code <task>` `.exec <shell>` `.tick` "
                 "`.send <chat> <msg>` `.contacts` `.mood [chat]` `.reset [chat]` "
                 "`.persona [chat] [name|clear]` `.bond [chat] [0-3|auto]` "
                 "`.memory [chat]` `.forget <chat> [deep]` `.models` `.model <name>` "
                 "`.stats` `.import <chat>` `.help`"),
    "discord": ("discord cmds: `.mission <goal>` `.code <task>` `.exec <shell>` `.tick` "
                "`.send <id> <msg>` `.contacts` `.mood [chat]` `.reset [chat]` "
                "`.persona [chat] [name|clear]` `.bond [chat] [0-3|auto]` "
                "`.memory [chat]` `.forget <chat> [deep]` `.models` `.model <name>` "
                "`.stats` `.import [limit]` `.help`"),
}


async def run_owner_command(api, channel: str, cmd: str, arg: str) -> str:
    """Run an owner command against the brain. `api` is the bridge's caller."""
    cmd = (cmd or "").lower()
    if cmd == "help":
        return HELP.get(channel, HELP["telegram"])
    if cmd == "mission":
        if not arg:
            return "usage: .mission <goal>"
        r = await api("/mission", {"goal": arg}, timeout=300)
        parts = [f"[{x['agent']}] {x['output']}" for x in r.get("results", [])]
        return "\n\n".join(parts)[:4000] or "done, no output"
    if cmd == "code":
        if not arg:
            return "usage: .code <coding task>"
        r = await api("/code", {"task": arg}, timeout=300)
        if r.get("error"):
            return f"code failed: {r['error']}"
        return f"💻 {r.get('path', '?')}\n{(r.get('summary') or '')[:3000]}"
    if cmd == "exec":
        if not arg:
            return "usage: .exec <shell command>"
        r = await api("/exec", {"cmd": arg}, timeout=60)
        if r.get("error"):
            return f"exec failed: {r['error']}"
        return f"$ {arg}\n{(r.get('output') or '(no output)')[:3000]}"
    if cmd == "tick":
        r = await api("/tick", {}, timeout=180)
        q = r.get("queued", [])
        return f"queued {len(q)}: " + "; ".join(
            f"{m['channel']}:{m['to']}" for m in q) if q else "nothing due 😴"
    if cmd == "send":
        to, _, msg = arg.partition(" ")
        if not to or not msg:
            return "usage: .send <chat_id|me> <message>"
        r = await api("/send", {"channel": channel, "to": to, "message": msg})
        return f"queued #{r.get('queued')} → {to} ✉"
    if cmd == "contacts":
        r = await api("/contacts")
        lines = [f"{c['channel']}:{c['chat_id']} ({c['display']}) "
                 f"{'🔔' if c['enabled'] else '🔕'}" for c in r.get("contacts", [])]
        return "\n".join(lines) or "no contacts yet"
    if cmd == "mood":
        m = await api(f"/mood?conversation_id={arg or channel + ':me'}")
        return f"{m.get('current')} ({m.get('level')}/10)"
    if cmd == "reset":
        await api("/reset", {"conversation_id": arg or channel + ":me"})
        return "reset ✨"
    if cmd == "persona":
        return await _persona(api, channel, arg)
    if cmd == "bond":
        parts = arg.split()
        target = parts[0] if parts else "me"
        if len(parts) > 1:
            lvl = parts[1] if parts[1].lower() == "auto" else None
            if lvl is None:
                try:
                    lvl = int(parts[1])
                except ValueError:
                    return "usage: .bond [chat] [0-3|auto]"
                if not 0 <= lvl <= 3:
                    return "usage: .bond [chat] [0-3|auto]"
            r = await api("/bond", {"channel": channel, "chat_id": target,
                                    "level": lvl})
            b = r.get("bond", {})
            return f"bond[{target}] pinned → L{b.get('level')} 💾"
        r = await api(f"/bond?channel={channel}&chat_id={target}")
        b = r.get("bond", {})
        pin = " 📌" if b.get("manual") else ""
        return (f"bond[{target}]: L{b.get('level')} score {b.get('score')} "
                f"fric {b.get('friction')} streak {b.get('streak')}d{pin}")
    if cmd == "memory":
        target = arg.strip() or "me"
        r = await api(f"/memory?channel={channel}&chat_id={target}")
        facts = r.get("facts", [])
        b = r.get("bond", {})
        head = (f"memory[{target}] L{b.get('level')} score {b.get('score')} "
                f"fric {b.get('friction')} streak {b.get('streak')}d")
        if not facts:
            return head + " — no facts yet 🧠"
        return (head + " | " +
                " | ".join(f"{f['key']}={f['value']}" for f in facts))[:3500]
    if cmd == "forget":
        parts = arg.split()
        if not parts:
            return "usage: .forget <chat> [deep]"
        r = await api("/forget", {"channel": channel, "chat_id": parts[0],
                                  "deep": len(parts) > 1 and
                                  parts[1].lower() == "deep"})
        if r.get("error"):
            return f"forget failed: {r['error']}"
        return (f"forgot {parts[0]} ({r.get('facts', 0)} facts wiped"
                f"{', bond zeroed' if r.get('deep') else ''}) 🧠💨")
    if cmd == "models":
        r = await api("/models")
        chain = r.get("chain", [])
        lines = [f"primary: {r.get('primary')}"]
        lines += [f"{'→' if c == r.get('primary') else ' '} {c}"
                  for c in chain]
        use = r.get("usage") or {}
        if use:
            lines.append(f"calls: {use.get('llm_calls', 0)} "
                         f"${use.get('spend_usd', 0)}")
        return "\n".join(lines)
    if cmd == "model":
        if not arg:
            return "usage: .model <provider> (runtime switch, resets on restart)"
        r = await api("/model", {"primary": arg.strip().lower()})
        if r.get("error"):
            return f"model failed: {r['error']}"
        return f"primary → {r.get('primary')} ⚡"
    if cmd == "stats":
        r = await api("/status")
        mems = r.get("memories", {})
        return (f"v{r.get('version')} {r.get('status')} | "
                f"mem: {sum(mems.values()) if mems else 0} | "
                f"calls: {r.get('llm_calls', 0)} ${r.get('spend_usd', 0)} | "
                f"persona: {r.get('persona')}")
    return HELP.get(channel, HELP["telegram"])


async def _persona(api, channel: str, arg: str) -> str:
    parts = arg.split()
    if not parts:  # show global default + overrides
        r = await api("/persona")
        ov = r.get("overrides", {})
        head = f"persona: {r.get('default')}"
        if not ov:
            return head + " (no chat overrides)"
        return head + " | " + ", ".join(f"{c}:{i}={p}"
                                        for c, i, p in
                                        [tuple(x.split(":", 2)) if x.count(":") >= 2
                                         else ("?", "?", x) for x in ov])[:2000]
    if len(parts) == 1:
        if parts[0].lower() in PERSONAS:  # set global default (runtime)
            r = await api("/persona", {"persona": parts[0].lower()})
            if r.get("error"):
                return f"persona failed: {r['error']}"
            return f"persona → {r.get('default')} 💕 (runtime; restart resets)"
        r = await api(f"/persona?channel={channel}&chat_id={parts[0]}")  # show chat's
        return f"persona[{parts[0]}]: {r.get('persona')} " + \
            ("(override)" if r.get("override") else "(default)")
    chat, name = parts[0], parts[1].lower()  # set/clear chat override
    if name not in PERSONAS and name != "clear":
        return f"unknown persona '{parts[1]}' ({'/'.join(PERSONAS)}|clear)"
    r = await api("/persona", {"channel": channel, "chat_id": chat,
                               "persona": name})
    if r.get("error"):
        return f"persona failed: {r['error']}"
    if name == "clear":
        return f"persona[{chat}] cleared → default 💕"
    return f"persona[{chat}] → {name} 💕"
