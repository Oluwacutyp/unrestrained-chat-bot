"""Shared owner-command core for the Python bridges (Telegram + Discord).

One systematic implementation, injected `api` + `channel`. Bridges keep only
their client-specific bits (.import needs the live client) and delegate here.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from urllib.parse import quote

PERSONAS = ("devon", "alex", "companion", "realistic", "quant")

HELP = {
    "telegram": ("userbot cmds: `.mission <goal>` `.code <task>` `.exec <shell>` `.tick` "
                 "`.send <chat> <msg>` `.contacts` `.mood [chat]` `.reset [chat]` "
                 "`.persona [chat] [name|clear]` `.bond [chat] [0-3|auto]` "
                 "`.memory [chat]` `.forget <chat> [deep]` `.models` `.model <name>` "
                 "`.stats` `.import <chat>` `.remind <when> <text>` `.reminders` `.cancel <id>` `.snooze <id> <when>` `.want <goal>` `.mind [done|drop <id>]` `.journal [chat]` `.note <save|get|list|del>` `.dream` `.brief` `.fetch <url>` `.recall <q>` `.mem …` `.project <goal>` `.projects` `.resume <id>` `.train …` `.good` `.bad` `.cal …` `.spend …` `.ledger` `.health …` `.bible …` `.model …` `.help`"),
    "discord": ("discord cmds: `.mission <goal>` `.code <task>` `.exec <shell>` `.tick` "
                "`.send <id> <msg>` `.contacts` `.mood [chat]` `.reset [chat]` "
                "`.persona [chat] [name|clear]` `.bond [chat] [0-3|auto]` "
                "`.memory [chat]` `.forget <chat> [deep]` `.models` `.model <name>` "
                "`.stats` `.import [limit]` `.remind <when> <text>` `.reminders` `.cancel <id>` `.snooze <id> <when>` `.want <goal>` `.mind [done|drop <id>]` `.journal [chat]` `.note <save|get|list|del>` `.dream` `.brief` `.fetch <url>` `.recall <q>` `.mem …` `.project <goal>` `.projects` `.resume <id>` `.train …` `.good` `.bad` `.cal …` `.spend …` `.ledger` `.health …` `.bible …` `.model …` `.help`"),
}


async def run_owner_command(api, channel: str, cmd: str, arg: str,
                            chat: str = "me") -> str:
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
        if r.get("model"):
            lines.append(f"model: {r['model']}")
        if r.get("gguf"):
            lines.append(f"gguf: {r['gguf']}")
        lines += [f"{'→' if c == r.get('primary') else ' '} {c}"
                  for c in chain]
        use = r.get("usage") or {}
        if use:
            lines.append(f"calls: {use.get('llm_calls', 0)} "
                         f"${use.get('spend_usd', 0)}")
        return "\n".join(lines)
    if cmd == "model":
        toks = arg.split()
        if not toks:
            return ("usage: .model <provider> | .model load "
                    "<user/model|path.gguf> [provider] | .model list")
        if toks[0].lower() == "list":
            r = await api("/models")
            chain = r.get("chain", [])
            lines = [f"primary: {r.get('primary')}"]
            if r.get("model"):
                lines.append(f"model: {r['model']}")
            if r.get("gguf"):
                lines.append(f"gguf: {r['gguf']}")
            lines += [f"{'→' if c == r.get('primary') else ' '} {c}"
                      for c in chain]
            return "\n".join(lines)
        if toks[0].lower() == "load" and len(toks) > 1:
            target = toks[1]
            if target.lower().endswith(".gguf"):
                payload = {"gguf": target}
            else:
                payload = {"model": target,
                           "primary": (toks[2] if len(toks) > 2
                                       else "huggingface").lower()}
            r = await api("/model", payload)
            if r.get("error"):
                return f"model failed: {r['error']}"
            return (f"loaded → {r.get('model') or r.get('gguf')} ⚡ "
                    f"(saved, survives restart)")
        r = await api("/model", {"primary": toks[0].lower()})
        if r.get("error"):
            return f"model failed: {r['error']}"
        return f"primary → {r.get('primary')} ⚡"
    if cmd == "remind":
        return await _remind(api, channel, chat, arg)
    if cmd == "reminders":
        r = await api("/remind", {"action": "list"})
        rs = r.get("reminders", [])
        if not rs:
            return "no reminders ⏰"
        return "\n".join(
            f"#{x['id']} {x['channel']}:{x['chat_id']} " +
            datetime.fromtimestamp(x["due_ts"]).strftime("%m-%d %H:%M") +
            (" ↻" if x["repeat"] else "") + f" — {x['text'][:80]}"
            for x in rs)[:3500]
    if cmd == "cancel":
        if not arg.strip().isdigit():
            return "usage: .cancel <reminder id>"
        r = await api("/remind", {"action": "cancel",
                                  "id": int(arg.strip())})
        return "cancelled ✅" if r.get("cancelled") else "no such reminder"
    if cmd == "snooze":
        rid, _, when = arg.partition(" ")
        due = parse_when(when.strip()) if rid.strip().isdigit() else None
        if not due:
            return "usage: .snooze <reminder id> <in 30m|2h · tomorrow 7:00 · 19:30>"
        r = await api("/remind", {"action": "snooze", "id": int(rid),
                                  "due_ts": due[0]})
        if not r.get("snoozed"):
            return "no such reminder"
        return ("⏰ snoozed → " +
                datetime.fromtimestamp(due[0]).strftime("%m-%d %H:%M"))
    if cmd == "want":
        if not arg.strip():
            return "usage: .want <goal — she plans around it>"
        r = await api("/want", {"text": arg.strip()})
        return f"intention #{r.get('id')} noted 🎯"
    if cmd == "mind":
        parts = arg.split()
        if len(parts) == 2 and parts[0] in ("done", "drop") \
                and parts[1].isdigit():
            r = await api("/mind", {"action": parts[0], "id": int(parts[1])})
            return "updated ✅" if r.get("ok") else "no such intention"
        r = await api("/mind")
        ins, rems, dr = (r.get("intentions", []), r.get("reminders", []),
                         r.get("dream", {}))
        lines = [f"🎯 {len(ins)} intentions"]
        lines += [f"  #{i['id']} [{i['kind']}] {i['text'][:70]}"
                  for i in ins[:8]]
        lines.append(f"⏰ {len(rems)} reminders")
        lines += [f"  #{x['id']} " +
                  datetime.fromtimestamp(x["due_ts"]).strftime("%m-%d %H:%M") +
                  f" {x['text'][:60]}" for x in rems[:8]]
        if dr:
            lines.append(f"🌙 dream: {dr.get('chats', 0)} chats, "
                         f"{dr.get('merged', 0)} merged")
        return "\n".join(lines)[:3500]
    if cmd == "journal":
        target = arg.strip() or chat
        r = await api(f"/journal?channel={channel}&chat_id={target}&limit=3")
        es = r.get("entries", [])
        if not es:
            return f"no journal for {target} yet 📓"
        return "\n\n".join(f"[{e['day']}] {e['entry']}" for e in es)[:3500]
    if cmd == "note":
        return await _note(api, arg)
    if cmd == "dream":
        r = await api("/dream", {}, timeout=120)
        out = (f"🌙 dream: {r.get('chats', 0)} chats, "
               f"{r.get('merged', 0)} merged, journal {r.get('journal', 0)}, "
               f"check-ins {len(r.get('intentions', []))}")
        if r.get("conflicts"):
            out += "\nconflicts: " + "; ".join(r["conflicts"][:5])
        return out
    if cmd == "brief":
        r = await api("/brief")
        return r.get("brief", "(no brief)")
    if cmd == "fetch":
        if not arg.strip():
            return "usage: .fetch <url>"
        r = await api("/fetch", {"url": arg.strip()}, timeout=60)
        if r.get("error"):
            return f"fetch failed: {r['error']}"
        head = f"📰 {r['title']}\n" if r.get("title") else ""
        return (head + (r.get("text") or "")[:3000]) or "(empty page)"
    if cmd == "recall":
        if not arg.strip():
            return "usage: .recall <query>"
        r = await api(f"/recall?q={quote(arg.strip())}&scope=*&limit=6")
        hits = r.get("hits", [])
        if not hits:
            return "nothing recalled 🧠"
        return "\n".join(
            f"#{h['id']} [{h['layer']}/{h['scope']}] {h['content'][:150]}" +
            (" 📌" if h["pinned"] else "") for h in hits)[:3500]
    if cmd == "mem":
        parts = arg.split(None, 2)
        if not parts or parts[0].lower() == "list":
            scope = parts[1] if len(parts) > 1 else ""
            r = await api(f"/memories?scope={quote(scope)}&limit=15")
            ms = r.get("memories", [])
            if not ms:
                return "no memories stored 🧠"
            return "\n".join(
                f"#{m['id']} [{m['layer']}/{m['scope']}] {m['content'][:120]}" +
                (" 📌" if m["pinned"] else "") for m in ms)[:3500]
        sub = parts[0].lower()
        if sub in ("pin", "unpin", "del", "delete") and len(parts) > 1 \
                and parts[1].isdigit():
            act = "delete" if sub in ("del", "delete") else sub
            r = await api("/memories", {"action": act, "id": int(parts[1])})
            return "done ✅" if r.get("ok") else "no such memory"
        if sub == "edit" and len(parts) > 2 and parts[1].isdigit():
            r = await api("/memories", {"action": "edit", "id": int(parts[1]),
                                        "content": parts[2]})
            return "edited ✅" if r.get("ok") else "no such memory"
        return "usage: .mem list [scope] | pin|unpin|del <id> | edit <id> <text>"
    if cmd == "project":
        if not arg.strip():
            return "usage: .project <goal> — runs in background, reports here"
        r = await api("/missions", {"action": "create", "goal": arg.strip(),
                                    "report_to": f"{channel}:{chat}"})
        if r.get("started"):
            return "project started 🚀 (progress lands here)"
        return f"project failed: {r.get('error')}"
    if cmd == "projects":
        r = await api("/missions", {"action": "list"})
        ms = r.get("missions", [])
        if not ms:
            return "no projects yet 🚧"
        return "\n".join(f"#{m['id']} [{m['status']}] {m['goal'][:100]}"
                          for m in ms)[:3000]
    if cmd == "resume":
        if not arg.strip().isdigit():
            return "usage: .resume <project id>"
        r = await api("/missions", {"action": "resume", "id": int(arg.strip())})
        if r.get("started"):
            return "resumed 🚀"
        return f"resume failed: {r.get('error')}"
    if cmd in ("good", "bad"):
        r = await api("/pref", {"cid": f"{channel}:{chat}", "verdict": cmd})
        if r.get("error"):
            return f"pref failed: {r['error']}"
        return ("noted 👍 — training data banked" if cmd == "good"
                else "noted 👎 — won't do that again")
    if cmd == "train":
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else "status"
        if sub == "status":
            r = await api("/train/status")
            return (f"🧬 trajectories: {r.get('trajectories', 0)} | prefs: "
                    f"{r.get('prefs', 0)} (pairs {r.get('pairs', 0)}) | "
                    f"{r.get('bytes', 0)} bytes")
        if sub == "export":
            r = await api("/train/export", {})
            if r.get("error"):
                return f"export failed: {r['error']}"
            return (f"📦 sft={r.get('sft')} dpo={r.get('dpo')} "
                    f"skipped={r.get('skipped')} → {r.get('dir')}")
        if sub == "push" and len(parts) > 1:
            r = await api("/train/push", {"repo": parts[1].strip()},
                          timeout=300)
            if not r.get("ok"):
                return f"push failed: {r.get('error')}"
            return f"☁️ pushed to {r.get('repo')}: " + ", ".join(r.get("pushed", []))
        if sub == "script":
            out = parts[1].strip() if len(parts) > 1 else "user/personal-devon"
            r = await api("/train/script?out=" + quote(out))
            return (r.get("script") or "")[:3500]
        return "usage: .train status|export|push <user/repo>|script [out]"
    if cmd == "cal":
        parts = arg.split(None, 1)
        sub = parts[0].lower() if parts else "list"
        if sub == "add" and len(parts) > 1:
            m = _WHEN_RE.match(parts[1].strip())
            parsed = parse_when(m.group(1)) if m else None
            if not m or not parsed:
                return ("usage: .cal add <in 2h · tomorrow 7:00 · "
                        "every day 8:00 · 19:30> <title>")
            due, repeat = parsed
            r = await api("/cal", {"action": "add",
                                   "title": m.group(2).strip(), "ts": due,
                                   "repeat": repeat, "channel": channel,
                                   "chat_id": chat})
            return (f"📅 #{r.get('id')} " +
                    datetime.fromtimestamp(due).strftime("%m-%d %H:%M") +
                    (f" ↻{repeat}" if repeat else ""))
        if sub == "list" or not parts:
            r = await api("/cal", {"action": "list"})
            es = r.get("events", [])
            if not es:
                return "no upcoming events 📅"
            return "\n".join(
                f"#{e['id']} " +
                datetime.fromtimestamp(e["ts"]).strftime("%m-%d %H:%M") +
                (" ↻" if e["repeat"] else "") +
                (" ✅" if e["done"] else "") + f" — {e['title'][:80]}"
                for e in es)[:3000]
        if sub in ("done", "del") and len(parts) > 1 \
                and parts[1].strip().isdigit():
            r = await api("/cal", {"action": sub, "id": int(parts[1].strip())})
            return "done ✅" if r.get("ok") else "no such event"
        return "usage: .cal add <when> <title> | .cal | .cal done|del <id>"
    if cmd == "spend":
        toks = arg.split()
        if not toks:
            return "usage: .spend <amount> [CUR] <cat> [note...]"
        try:
            amt = float(toks[0].replace(",", ""))
        except ValueError:
            return "usage: .spend <amount> [CUR] <cat> [note...]"
        rest = toks[1:]
        cur = ""
        if rest and re.match(r"^[A-Za-z]{3}$", rest[0]):
            cur = rest.pop(0).upper()
        if not rest:
            return "usage: .spend <amount> [CUR] <cat> [note...]"
        cat, note = rest[0].lower(), " ".join(rest[1:])
        r = await api("/ledger", {"action": "add", "amount": amt,
                                  "currency": cur, "cat": cat, "note": note})
        return (f"💸 #{r.get('id')} {amt:g} {cur} [{cat}]" +
                (f" {note[:60]}" if note else ""))
    if cmd == "ledger":
        cat = arg.strip().lower()
        r = await api("/ledger", {"action": "list", "cat": cat})
        es = r.get("entries", [])
        t = await api("/ledger", {"action": "total", "cat": cat})
        if not es:
            return "ledger empty 💸"
        lines = [f"#{e['id']} {e['amount']:g} {e['currency']} [{e['cat']}] "
                 f"{(e['note'] or '')[:50]}".rstrip() for e in es[:15]]
        lines.append(f"Σ {t.get('total', 0):g}" +
                     (f" [{cat}]" if cat else ""))
        return "\n".join(lines)[:3000]
    if cmd == "health":
        met, _, val = arg.partition(" ")
        if not met or not val.strip():
            return "usage: .health <metric> <value>"
        await api("/health", {"action": "log", "metric": met,
                              "value": val.strip()})
        return f"❤️ logged {met.lower()} = {val.strip()[:60]}"
    if cmd == "healthlog":
        r = await api("/health", {"action": "list", "metric": arg.strip()})
        es = r.get("entries", [])
        if not es:
            return "no health logs ❤️"
        return "\n".join(f"{e['metric']}: {e['value'][:60]}"
                          for e in es[:15])[:2000]
    if cmd == "bible":
        return await _bible(api, arg)
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

_WHEN_RE = re.compile(
    r"^(in\s+\d+\s*[mhd]|tomorrow\s+\d{1,2}:\d{2}|"
    r"every\s+day\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})\s+(.+)$", re.I)


def parse_when(s: str, now: float | None = None):
    """'in 30m|2h|3d' | 'tomorrow 7:00' | 'every day 8:00' | 'HH:MM'
    → (due_ts, repeat) or None."""
    now = time.time() if now is None else now
    t = (s or "").strip().lower()
    m = re.match(r"in\s+(\d+)\s*([mhd])$", t)
    if m:
        return now + int(m.group(1)) * {"m": 60, "h": 3600,
                                        "d": 86400}[m.group(2)], ""
    m = re.match(r"(tomorrow\s+)?(\d{1,2}):(\d{2})$", t)
    if m:
        base = datetime.now() + timedelta(days=1 if m.group(1) else 0)
        dt = base.replace(hour=int(m.group(2)), minute=int(m.group(3)),
                          second=0, microsecond=0)
        ts = dt.timestamp()
        if ts <= now and not m.group(1):
            ts += 86400
        return ts, ""
    m = re.match(r"every\s+day\s+(\d{1,2}):(\d{2})$", t)
    if m:
        dt = datetime.now().replace(hour=int(m.group(1)),
                                    minute=int(m.group(2)),
                                    second=0, microsecond=0)
        ts = dt.timestamp()
        if ts <= now:
            ts += 86400
        return ts, "daily"
    return None


async def _remind(api, channel: str, chat: str, arg: str) -> str:
    m = _WHEN_RE.match((arg or "").strip())
    if not m or not parse_when(m.group(1)):
        return ("usage: .remind <in 30m|2h|3d · tomorrow 7:00 · "
                "every day 8:00 · 19:30> <text>")
    due, repeat = parse_when(m.group(1))
    r = await api("/remind", {"action": "add", "channel": channel,
                              "chat_id": chat, "text": m.group(2).strip(),
                              "due_ts": due, "repeat": repeat})
    if r.get("error"):
        return f"remind failed: {r['error']}"
    when = datetime.fromtimestamp(due).strftime("%m-%d %H:%M")
    return (f"⏰ #{r.get('id')} {when}"
            f"{' ↻daily' if repeat else ''} — {m.group(2).strip()[:100]}")


async def _note(api, arg: str) -> str:
    parts = (arg or "").split(None, 1)
    if not parts or parts[0].lower() == "list":
        r = await api("/note", {"action": "list"})
        ns = r.get("notes", [])
        return "notes: " + ", ".join(ns) if ns else "no notes yet 📝"
    sub = parts[0].lower()
    if sub == "save":
        rest = parts[1] if len(parts) > 1 else ""
        name, _, body = rest.partition(" ")
        if not name or not body.strip():
            return "usage: .note save <name> <text>"
        r = await api("/note", {"action": "save", "name": name,
                                "body": body.strip()})
        return f"noted [{r.get('saved')}] 📝"
    if sub == "del" and len(parts) > 1:
        r = await api("/note", {"action": "del", "name": parts[1].strip()})
        return "deleted ✅" if r.get("deleted") else "no such note"
    r = await api("/note", {"action": "get", "name": parts[0]})
    if r.get("error"):
        return "no such note — `.note list` to see all"
    return f"📝 {parts[0].lower()}:\n{(r.get('body') or '')[:3000]}"

async def _bible(api, arg: str) -> str:
    parts = (arg or "").split(None, 2)
    if not parts or parts[0].lower() == "list":
        r = await api("/bible", {"action": "list"})
        bs = r.get("bibles", [])
        return "bibles: " + ", ".join(bs) if bs else "no bibles yet 📖"
    sub = parts[0].lower()
    if sub in ("save", "new") and len(parts) > 2:
        r = await api("/bible", {"action": sub, "name": parts[1],
                                 "text": parts[2]})
        if r.get("ok"):
            return (f"bible [{parts[1].lower()}] "
                    f"{'appended' if sub == 'save' else 'rewritten'} 📖")
        return "bible failed"
    if sub == "del" and len(parts) > 1:
        r = await api("/bible", {"action": "del", "name": parts[1]})
        return "deleted ✅" if r.get("ok") else "no such bible"
    r = await api("/bible", {"action": "get", "name": parts[0]})
    if r.get("error"):
        return "no such bible — `.bible list` to see all"
    return f"📖 {parts[0].lower()}:\n{(r.get('body') or '')[:3000]}"
