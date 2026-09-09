/* Shared owner-command core for the WhatsApp bridges (classic + Baileys).
 *
 * Pure logic + injected transport:
 *   const ownerCmd = require('./wa_owner');
 *   const out = await ownerCmd.handleOwnerCommand(text, {
 *       post: async (path, payload, timeoutMs) => parsedJson,
 *       channel: 'whatsapp',
 *       chatId: currentChatId,            // for .import
 *       fetchHistory: async (limit) => items | null
 *   });
 * Returns reply string, or null when `text` is not an owner command.
 */
const PERSONAS = ['devon', 'alex', 'companion', 'realistic', 'quant'];
const HELP = 'wa cmds: `.mission <goal>` `.code <task>` `.exec <shell>` `.tick` ' +
    '`.send <chat> <msg>` `.contacts` `.mood [chat]` `.reset [chat]` ' +
    '`.persona [chat] [name|clear]` `.bond [chat] [0-3|auto]` `.memory [chat]` ' +
    '`.forget <chat> [deep]` `.models` `.model <name>` `.stats` `.import [limit]` ' +
    '`.remind <when> <text>` `.reminders` `.cancel <id>` `.want <goal>` ' +
    '`.mind [done|drop <id>]` `.journal [chat]` `.note <save|get|list|del>` ' +
    '`.snooze <id> <when>` ' +
    '`.dream` `.brief` `.fetch <url>` `.help`';

function parseOwnerCommand(text) {
    const t = (text || '').trim();
    if (!t.startsWith('.') || t.length < 2) return null;
    const sp = t.indexOf(' ');
    if (sp < 0) return { cmd: t.slice(1).toLowerCase(), arg: '' };
    return { cmd: t.slice(1, sp).toLowerCase(), arg: t.slice(sp + 1).trim() };
}

function fmtDue(ts) {
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function parseWhen(s, nowMs) {
    // 'in 30m|2h|3d' | 'tomorrow 7:00' | 'every day 8:00' | 'HH:MM' → {due, repeat} | null
    const now = nowMs || Date.now();
    const t = (s || '').trim().toLowerCase();
    let m = t.match(/^in\s+(\d+)\s*([mhd])$/);
    if (m) {
        const mult = { m: 60, h: 3600, d: 86400 }[m[2]];
        return { due: now / 1000 + parseInt(m[1], 10) * mult, repeat: '' };
    }
    m = t.match(/^(tomorrow\s+)?(\d{1,2}):(\d{2})$/);
    if (m) {
        const d = new Date(now + (m[1] ? 86400000 : 0));
        d.setHours(parseInt(m[2], 10), parseInt(m[3], 10), 0, 0);
        let ts = d.getTime() / 1000;
        if (ts <= now / 1000 && !m[1]) ts += 86400;
        return { due: ts, repeat: '' };
    }
    m = t.match(/^every\s+day\s+(\d{1,2}):(\d{2})$/);
    if (m) {
        const d = new Date(now);
        d.setHours(parseInt(m[1], 10), parseInt(m[2], 10), 0, 0);
        let ts = d.getTime() / 1000;
        if (ts <= now / 1000) ts += 86400;
        return { due: ts, repeat: 'daily' };
    }
    return null;
}

async function handleOwnerCommand(text, ctx) {
    const parsed = parseOwnerCommand(text);
    if (!parsed) return null;
    const post = ctx.post;
    const channel = ctx.channel || 'whatsapp';
    const cmd = parsed.cmd;
    const arg = parsed.arg;
    if (cmd === 'help') return HELP;
    if (cmd === 'mission') {
        if (!arg) return 'usage: .mission <goal>';
        const r = await post('/mission', { goal: arg }, 300000);
        const parts = (r.results || []).map(x => `[${x.agent}] ${x.output}`);
        return parts.join('\n\n').slice(0, 4000) || 'done, no output';
    }
    if (cmd === 'code') {
        if (!arg) return 'usage: .code <coding task>';
        const r = await post('/code', { task: arg }, 300000);
        if (r.error) return 'code failed: ' + r.error;
        return ('💻 ' + (r.path || '?') + '\n' + (r.summary || '')).slice(0, 3500);
    }
    if (cmd === 'exec') {
        if (!arg) return 'usage: .exec <shell command>';
        const r = await post('/exec', { cmd: arg }, 60000);
        if (r.error) return 'exec failed: ' + r.error;
        return ('$ ' + arg + '\n' + (r.output || '(no output)')).slice(0, 3500);
    }
    if (cmd === 'tick') {
        const r = await post('/tick', {}, 180000);
        const q = r.queued || [];
        if (!q.length) return 'nothing due 😴';
        return 'queued ' + q.length + ': ' + q.map(m => m.channel + ':' + m.to).join('; ');
    }
    if (cmd === 'send') {
        const sp = arg.indexOf(' ');
        if (sp < 0) return 'usage: .send <chat> <message>';
        const r = await post('/send', { channel: channel, to: arg.slice(0, sp), message: arg.slice(sp + 1) });
        return 'queued #' + r.queued + ' → ' + arg.slice(0, sp) + ' ✉';
    }
    if (cmd === 'contacts') {
        const r = await post('/contacts', null);
        const lines = (r.contacts || []).map(c =>
            `${c.channel}:${c.chat_id} (${c.display}) ${c.enabled ? '🔔' : '🔕'}`);
        return lines.join('\n') || 'no contacts yet';
    }
    if (cmd === 'mood') {
        const m = await post('/mood?conversation_id=' + (arg || channel + ':me'), null);
        return `${m.current} (${m.level}/10)`;
    }
    if (cmd === 'reset') {
        await post('/reset', { conversation_id: arg || channel + ':me' });
        return 'reset ✨';
    }
    if (cmd === 'persona') {
        return await personaCmd(post, channel, arg);
    }
    if (cmd === 'bond') {
        const parts = arg.split(/\s+/).filter(Boolean);
        const target = parts[0] || 'me';
        if (parts.length > 1) {
            let lvl = parts[1].toLowerCase() === 'auto' ? 'auto' : parseInt(parts[1], 10);
            if (lvl !== 'auto' && !(lvl >= 0 && lvl <= 3)) return 'usage: .bond [chat] [0-3|auto]';
            const r = await post('/bond', { channel: channel, chat_id: target, level: lvl });
            return `bond[${target}] pinned → L${(r.bond || {}).level} 💾`;
        }
        const r = await post(`/bond?channel=${channel}&chat_id=${target}`, null);
        const b = r.bond || {};
        return `bond[${target}]: L${b.level} score ${b.score} fric ${b.friction} streak ${b.streak}d${b.manual ? ' 📌' : ''}`;
    }
    if (cmd === 'memory') {
        const target = arg || 'me';
        const r = await post(`/memory?channel=${channel}&chat_id=${target}`, null);
        const facts = r.facts || [];
        const b = r.bond || {};
        const head = `memory[${target}] L${b.level} score ${b.score} fric ${b.friction} streak ${b.streak}d`;
        if (!facts.length) return head + ' — no facts yet 🧠';
        return (head + ' | ' + facts.map(f => `${f.key}=${f.value}`).join(' | ')).slice(0, 3500);
    }
    if (cmd === 'forget') {
        const parts = arg.split(/\s+/).filter(Boolean);
        if (!parts.length) return 'usage: .forget <chat> [deep]';
        const r = await post('/forget', {
            channel: channel, chat_id: parts[0],
            deep: parts.length > 1 && parts[1].toLowerCase() === 'deep'
        });
        if (r.error) return 'forget failed: ' + r.error;
        return `forgot ${parts[0]} (${r.facts || 0} facts wiped${r.deep ? ', bond zeroed' : ''}) 🧠💨`;
    }
    if (cmd === 'models') {
        const r = await post('/models', null);
        const chain = r.chain || [];
        const lines = ['primary: ' + r.primary];
        chain.forEach(c => lines.push((c === r.primary ? '→ ' : '  ') + c));
        if (r.usage) lines.push(`calls: ${r.usage.llm_calls || 0} $${r.usage.spend_usd || 0}`);
        return lines.join('\n');
    }
    if (cmd === 'model') {
        if (!arg) return 'usage: .model <provider> (runtime switch, resets on restart)';
        const r = await post('/model', { primary: arg.toLowerCase() });
        if (r.error) return 'model failed: ' + r.error;
        return 'primary → ' + r.primary + ' ⚡';
    }
    if (cmd === 'stats') {
        const r = await post('/status', null);
        const mems = r.memories || {};
        let n = 0;
        Object.keys(mems).forEach(k => { n += mems[k]; });
        return `v${r.version} ${r.status} | mem: ${n} | calls: ${r.llm_calls || 0} $${r.spend_usd || 0} | persona: ${r.persona}`;
    }
    if (cmd === 'remind') {
        const m = arg.match(/^(in\s+\d+\s*[mhd]|tomorrow\s+\d{1,2}:\d{2}|every\s+day\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})\s+(.+)$/i);
        const w = m ? parseWhen(m[1]) : null;
        if (!m || !w) return 'usage: .remind <in 30m|2h|3d · tomorrow 7:00 · every day 8:00 · 19:30> <text>';
        const r = await post('/remind', { action: 'add', channel: channel, chat_id: ctx.chatId || 'me', text: m[2].trim(), due_ts: w.due, repeat: w.repeat });
        if (r.error) return 'remind failed: ' + r.error;
        return `⏰ #${r.id} ${fmtDue(w.due)}${w.repeat ? ' ↻daily' : ''} — ${m[2].trim().slice(0, 100)}`;
    }
    if (cmd === 'reminders') {
        const r = await post('/remind', { action: 'list' });
        const rs = r.reminders || [];
        if (!rs.length) return 'no reminders ⏰';
        return rs.map(x => `#${x.id} ${x.channel}:${x.chat_id} ${fmtDue(x.due_ts)}${x.repeat ? ' ↻' : ''} — ${x.text.slice(0, 80)}`).join('\n').slice(0, 3500);
    }
    if (cmd === 'cancel') {
        if (!/^\d+$/.test(arg)) return 'usage: .cancel <reminder id>';
        const r = await post('/remind', { action: 'cancel', id: parseInt(arg, 10) });
        return r.cancelled ? 'cancelled ✅' : 'no such reminder';
    }
    if (cmd === 'snooze') {
        const sp = arg.indexOf(' ');
        const w = sp > 0 && /^\d+$/.test(arg.slice(0, sp)) ? parseWhen(arg.slice(sp + 1)) : null;
        if (!w) return 'usage: .snooze <reminder id> <in 30m|2h · tomorrow 7:00 · 19:30>';
        const r = await post('/remind', { action: 'snooze', id: parseInt(arg.slice(0, sp), 10), due_ts: w.due });
        if (!r.snoozed) return 'no such reminder';
        return '⏰ snoozed → ' + fmtDue(w.due);
    }
    if (cmd === 'want') {
        if (!arg) return 'usage: .want <goal — she plans around it>';
        const r = await post('/want', { text: arg });
        return `intention #${r.id} noted 🎯`;
    }
    if (cmd === 'mind') {
        const parts = arg.split(/\s+/).filter(Boolean);
        if (parts.length === 2 && (parts[0] === 'done' || parts[0] === 'drop') && /^\d+$/.test(parts[1])) {
            const r = await post('/mind', { action: parts[0], id: parseInt(parts[1], 10) });
            return r.ok ? 'updated ✅' : 'no such intention';
        }
        const r = await post('/mind', null);
        const lines = [`🎯 ${(r.intentions || []).length} intentions`];
        (r.intentions || []).slice(0, 8).forEach(i => lines.push(`  #${i.id} [${i.kind}] ${i.text.slice(0, 70)}`));
        lines.push(`⏰ ${(r.reminders || []).length} reminders`);
        (r.reminders || []).slice(0, 8).forEach(x => lines.push(`  #${x.id} ${fmtDue(x.due_ts)} ${x.text.slice(0, 60)}`));
        if (r.dream && r.dream.chats !== undefined) lines.push(`🌙 dream: ${r.dream.chats} chats, ${r.dream.merged} merged`);
        return lines.join('\n').slice(0, 3500);
    }
    if (cmd === 'journal') {
        const target = arg || ctx.chatId || 'me';
        const r = await post(`/journal?channel=${channel}&chat_id=${target}&limit=3`, null);
        const es = r.entries || [];
        if (!es.length) return `no journal for ${target} yet 📓`;
        return es.map(e => `[${e.day}] ${e.entry}`).join('\n\n').slice(0, 3500);
    }
    if (cmd === 'note') {
        return await noteCmd(post, arg);
    }
    if (cmd === 'dream') {
        const r = await post('/dream', {}, 120000);
        let out = `🌙 dream: ${r.chats || 0} chats, ${r.merged || 0} merged, journal ${r.journal || 0}, check-ins ${(r.intentions || []).length}`;
        if (r.conflicts && r.conflicts.length) out += '\nconflicts: ' + r.conflicts.slice(0, 5).join('; ');
        return out;
    }
    if (cmd === 'brief') {
        const r = await post('/brief', null);
        return r.brief || '(no brief)';
    }
    if (cmd === 'fetch') {
        if (!arg) return 'usage: .fetch <url>';
        const r = await post('/fetch', { url: arg }, 60000);
        if (r.error) return 'fetch failed: ' + r.error;
        return ((r.title ? '📰 ' + r.title + '\n' : '') + (r.text || '').slice(0, 3000)) || '(empty page)';
    }
    if (cmd === 'import') {
        if (!ctx.fetchHistory) return 'import not supported on this bridge';
        let limit = 200;
        arg.split(/\s+/).forEach(p => {
            if (/^\d+$/.test(p)) limit = Math.min(300, parseInt(p, 10));
        });
        const items = await ctx.fetchHistory(limit);
        const r = await post('/import', {
            channel: channel, chat_id: ctx.chatId, messages: items
        }, 120000);
        const facts = r.facts || [];
        const head = `imported ${r.imported || 0} msgs, learned ${facts.length} facts`;
        return facts.length ? head + ': ' + facts.slice(0, 12).join(', ') : head;
    }
    return HELP;
}

async function personaCmd(post, channel, arg) {
    const parts = arg.split(/\s+/).filter(Boolean);
    if (!parts.length) {
        const r = await post('/persona', null);
        const ov = r.overrides || [];
        let head = 'persona: ' + r.default;
        if (!ov.length) return head + ' (no chat overrides)';
        return (head + ' | ' + ov.map(o => {
            const bits = o.split(':');
            return bits.length >= 3 ? `${bits[0]}:${bits[1]}=${bits.slice(2).join(':')}` : o;
        }).join(', ')).slice(0, 2000);
    }
    if (parts.length === 1) {
        if (PERSONAS.indexOf(parts[0].toLowerCase()) >= 0) {
            const r = await post('/persona', { persona: parts[0].toLowerCase() });
            if (r.error) return 'persona failed: ' + r.error;
            return 'persona → ' + r.default + ' 💕 (runtime; restart resets)';
        }
        const r = await post(`/persona?channel=${channel}&chat_id=${parts[0]}`, null);
        return `persona[${parts[0]}]: ${r.persona} ` + (r.override ? '(override)' : '(default)');
    }
    const name = parts[1].toLowerCase();
    if (PERSONAS.indexOf(name) < 0 && name !== 'clear') {
        return `unknown persona '${parts[1]}' (${PERSONAS.join('/')}|clear)`;
    }
    const r = await post('/persona', { channel: channel, chat_id: parts[0], persona: name });
    if (r.error) return 'persona failed: ' + r.error;
    if (name === 'clear') return `persona[${parts[0]}] cleared → default 💕`;
    return `persona[${parts[0]}] → ${name} 💕`;
}

async function noteCmd(post, arg) {
    const sp = arg.indexOf(' ');
    const sub = (sp < 0 ? arg : arg.slice(0, sp)).toLowerCase();
    const rest = sp < 0 ? '' : arg.slice(sp + 1).trim();
    if (!sub || sub === 'list') {
        const r = await post('/note', { action: 'list' });
        return (r.notes || []).length ? 'notes: ' + r.notes.join(', ') : 'no notes yet 📝';
    }
    if (sub === 'save') {
        const sp2 = rest.indexOf(' ');
        if (sp2 < 0) return 'usage: .note save <name> <text>';
        const r = await post('/note', { action: 'save', name: rest.slice(0, sp2), body: rest.slice(sp2 + 1) });
        return `noted [${r.saved}] 📝`;
    }
    if (sub === 'del' && rest) {
        const r = await post('/note', { action: 'del', name: rest });
        return r.deleted ? 'deleted ✅' : 'no such note';
    }
    const r = await post('/note', { action: 'get', name: sub });
    if (r.error) return 'no such note — `.note list` to see all';
    return (`📝 ${sub}:\n` + (r.body || '')).slice(0, 3200);
}

module.exports = { handleOwnerCommand, parseOwnerCommand, parseWhen };
