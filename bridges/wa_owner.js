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
    '`.dream` `.brief` `.fetch <url>` `.recall <q>` `.mem …` ' +
    '`.project <goal>` `.projects` `.resume <id>` `.train …` ' +
    '`.good` `.bad` `.cal …` `.spend …` `.ledger` `.health …` ' +
    '`.bible …` `.help`';

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
        if (r.model) lines.push('model: ' + r.model);
        if (r.gguf) lines.push('gguf: ' + r.gguf);
        chain.forEach(c => lines.push((c === r.primary ? '→ ' : '  ') + c));
        if (r.usage) lines.push(`calls: ${r.usage.llm_calls || 0} $${r.usage.spend_usd || 0}`);
        return lines.join('\n');
    }
    if (cmd === 'model') {
        const toks = arg.split(/\s+/).filter(Boolean);
        if (!toks.length) return 'usage: .model <provider> | .model load <user/model|path.gguf> [provider] | .model list';
        if (toks[0].toLowerCase() === 'list') {
            const r = await post('/models', null);
            const chain = r.chain || [];
            const lines = ['primary: ' + r.primary];
            if (r.model) lines.push('model: ' + r.model);
            if (r.gguf) lines.push('gguf: ' + r.gguf);
            chain.forEach(c => lines.push((c === r.primary ? '→ ' : '  ') + c));
            return lines.join('\n');
        }
        if (toks[0].toLowerCase() === 'load' && toks.length > 1) {
            const t = toks[1];
            const payload = t.toLowerCase().endsWith('.gguf')
                ? { gguf: t }
                : { model: t, primary: (toks[2] || 'huggingface').toLowerCase() };
            const r = await post('/model', payload);
            if (r.error) return 'model failed: ' + r.error;
            return 'loaded → ' + (r.model || r.gguf) + ' ⚡ (saved, survives restart)';
        }
        const r = await post('/model', { primary: toks[0].toLowerCase() });
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
    if (cmd === 'recall') {
        if (!arg) return 'usage: .recall <query>';
        const r = await post('/recall?q=' + encodeURIComponent(arg) + '&scope=*&limit=6', null);
        const hits = r.hits || [];
        if (!hits.length) return 'nothing recalled 🧠';
        return hits.map(h => `#${h.id} [${h.layer}/${h.scope}] ${h.content.slice(0, 150)}${h.pinned ? ' 📌' : ''}`).join('\n').slice(0, 3500);
    }
    if (cmd === 'mem') {
        const parts = arg.split(/\s+/).filter(Boolean);
        if (!parts.length || parts[0].toLowerCase() === 'list') {
            const scope = parts.length > 1 ? parts[1] : '';
            const r = await post('/memories?scope=' + encodeURIComponent(scope) + '&limit=15', null);
            const ms = r.memories || [];
            if (!ms.length) return 'no memories stored 🧠';
            return ms.map(m => `#${m.id} [${m.layer}/${m.scope}] ${m.content.slice(0, 120)}${m.pinned ? ' 📌' : ''}`).join('\n').slice(0, 3500);
        }
        const sub = parts[0].toLowerCase();
        if ((sub === 'pin' || sub === 'unpin' || sub === 'del' || sub === 'delete') && parts.length > 1 && /^\d+$/.test(parts[1])) {
            const act = (sub === 'del' || sub === 'delete') ? 'delete' : sub;
            const r = await post('/memories', { action: act, id: parseInt(parts[1], 10) });
            return r.ok ? 'done ✅' : 'no such memory';
        }
        if (sub === 'edit' && parts.length > 2 && /^\d+$/.test(parts[1])) {
            const r = await post('/memories', { action: 'edit', id: parseInt(parts[1], 10), content: parts.slice(2).join(' ') });
            return r.ok ? 'edited ✅' : 'no such memory';
        }
        return 'usage: .mem list [scope] | pin|unpin|del <id> | edit <id> <text>';
    }
    if (cmd === 'project') {
        if (!arg) return 'usage: .project <goal> — runs in background, reports here';
        const r = await post('/missions', { action: 'create', goal: arg, report_to: channel + ':' + (ctx.chatId || 'me') });
        return r.started ? 'project started 🚀 (progress lands here)' : 'project failed: ' + r.error;
    }
    if (cmd === 'projects') {
        const r = await post('/missions', { action: 'list' });
        const ms = r.missions || [];
        if (!ms.length) return 'no projects yet 🚧';
        return ms.map(m => `#${m.id} [${m.status}] ${m.goal.slice(0, 100)}`).join('\n').slice(0, 3000);
    }
    if (cmd === 'resume') {
        if (!/^\d+$/.test(arg)) return 'usage: .resume <project id>';
        const r = await post('/missions', { action: 'resume', id: parseInt(arg, 10) });
        return r.started ? 'resumed 🚀' : 'resume failed: ' + r.error;
    }
    if (cmd === 'good' || cmd === 'bad') {
        const r = await post('/pref', { cid: channel + ':' + (ctx.chatId || 'me'), verdict: cmd });
        if (r.error) return 'pref failed: ' + r.error;
        return cmd === 'good' ? 'noted 👍 — training data banked' : "noted 👎 — won't do that again";
    }
    if (cmd === 'train') {
        const sp = arg.indexOf(' ');
        const sub = ((sp < 0 ? arg : arg.slice(0, sp)) || 'status').toLowerCase();
        const rest = sp < 0 ? '' : arg.slice(sp + 1).trim();
        if (sub === 'status') {
            const r = await post('/train/status', null);
            return `🧬 trajectories: ${r.trajectories || 0} | prefs: ${r.prefs || 0} (pairs ${r.pairs || 0}) | ${r.bytes || 0} bytes`;
        }
        if (sub === 'export') {
            const r = await post('/train/export', {});
            if (r.error) return 'export failed: ' + r.error;
            return `📦 sft=${r.sft} dpo=${r.dpo} skipped=${r.skipped} → ${r.dir}`;
        }
        if (sub === 'push' && rest) {
            const r = await post('/train/push', { repo: rest }, 300000);
            if (!r.ok) return 'push failed: ' + r.error;
            return `☁️ pushed to ${r.repo}: ` + (r.pushed || []).join(', ');
        }
        if (sub === 'script') {
            const r = await post('/train/script?out=' + encodeURIComponent(rest || 'user/personal-devon'), null);
            return (r.script || '').slice(0, 3500);
        }
        return 'usage: .train status|export|push <user/repo>|script [out]';
    }
    if (cmd === 'cal') {
        const sp = arg.indexOf(' ');
        const sub = (sp < 0 ? arg : arg.slice(0, sp)).toLowerCase() || 'list';
        const rest = sp < 0 ? '' : arg.slice(sp + 1).trim();
        if (sub === 'add' && rest) {
            const m = rest.match(/^(in\s+\d+\s*[mhd]|tomorrow\s+\d{1,2}:\d{2}|every\s+day\s+\d{1,2}:\d{2}|\d{1,2}:\d{2})\s+(.+)$/i);
            const w = m ? parseWhen(m[1]) : null;
            if (!m || !w) return 'usage: .cal add <in 2h · tomorrow 7:00 · every day 8:00 · 19:30> <title>';
            const r = await post('/cal', { action: 'add', title: m[2].trim(), ts: w.due, repeat: w.repeat, channel: channel, chat_id: ctx.chatId || 'me' });
            return `📅 #${r.id} ${fmtDue(w.due)}${w.repeat ? ' ↻' + w.repeat : ''}`;
        }
        if (sub === 'list' || !arg) {
            const r = await post('/cal', { action: 'list' });
            const es = r.events || [];
            if (!es.length) return 'no upcoming events 📅';
            return es.map(e => `#${e.id} ${fmtDue(e.ts)}${e.repeat ? ' ↻' : ''}${e.done ? ' ✅' : ''} — ${e.title.slice(0, 80)}`).join('\n').slice(0, 3000);
        }
        if ((sub === 'done' || sub === 'del') && /^\d+$/.test(rest)) {
            const r = await post('/cal', { action: sub, id: parseInt(rest, 10) });
            return r.ok ? 'done ✅' : 'no such event';
        }
        return 'usage: .cal add <when> <title> | .cal | .cal done|del <id>';
    }
    if (cmd === 'spend') {
        const toks = arg.split(/\s+/).filter(Boolean);
        const amt = toks.length ? parseFloat(toks[0].replace(/,/g, '')) : NaN;
        if (!toks.length || isNaN(amt)) return 'usage: .spend <amount> [CUR] <cat> [note...]';
        const rest = toks.slice(1);
        let cur = '';
        if (rest.length && /^[A-Za-z]{3}$/.test(rest[0])) cur = rest.shift().toUpperCase();
        if (!rest.length) return 'usage: .spend <amount> [CUR] <cat> [note...]';
        const cat = rest.shift().toLowerCase();
        const note = rest.join(' ');
        const r = await post('/ledger', { action: 'add', amount: amt, currency: cur, cat: cat, note: note });
        return `💸 #${r.id} ${amt} ${cur} [${cat}]` + (note ? ' ' + note.slice(0, 60) : '');
    }
    if (cmd === 'ledger') {
        const cat = arg.trim().toLowerCase();
        const r = await post('/ledger', { action: 'list', cat: cat });
        const es = r.entries || [];
        const t = await post('/ledger', { action: 'total', cat: cat });
        if (!es.length) return 'ledger empty 💸';
        const lines = es.slice(0, 15).map(e => `#${e.id} ${e.amount} ${e.currency} [${e.cat}] ${(e.note || '').slice(0, 50)}`.trim());
        lines.push(`Σ ${t.total || 0}` + (cat ? ` [${cat}]` : ''));
        return lines.join('\n').slice(0, 3000);
    }
    if (cmd === 'health') {
        const sp = arg.indexOf(' ');
        if (sp < 0) return 'usage: .health <metric> <value>';
        await post('/health', { action: 'log', metric: arg.slice(0, sp), value: arg.slice(sp + 1).trim() });
        return `❤️ logged ${arg.slice(0, sp).toLowerCase()} = ${arg.slice(sp + 1).trim().slice(0, 60)}`;
    }
    if (cmd === 'healthlog') {
        const r = await post('/health', { action: 'list', metric: arg.trim() });
        const es = r.entries || [];
        if (!es.length) return 'no health logs ❤️';
        return es.slice(0, 15).map(e => `${e.metric}: ${e.value.slice(0, 60)}`).join('\n').slice(0, 2000);
    }
    if (cmd === 'bible') {
        return await bibleCmd(post, arg);
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

async function bibleCmd(post, arg) {
    const parts = arg.split(/\s+/).filter(Boolean);
    if (!parts.length || parts[0].toLowerCase() === 'list') {
        const r = await post('/bible', { action: 'list' });
        return (r.bibles || []).length ? 'bibles: ' + r.bibles.join(', ') : 'no bibles yet 📖';
    }
    const sub = parts[0].toLowerCase();
    if ((sub === 'save' || sub === 'new') && parts.length > 2) {
        const r = await post('/bible', { action: sub, name: parts[1], text: parts.slice(2).join(' ') });
        if (r.ok) return `bible [${parts[1].toLowerCase()}] ${sub === 'save' ? 'appended' : 'rewritten'} 📖`;
        return 'bible failed';
    }
    if (sub === 'del' && parts.length > 1) {
        const r = await post('/bible', { action: 'del', name: parts[1] });
        return r.deleted || r.ok ? 'deleted ✅' : 'no such bible';
    }
    const r = await post('/bible', { action: 'get', name: parts[0] });
    if (r.error) return 'no such bible — `.bible list` to see all';
    return (`📖 ${parts[0].toLowerCase()}:\n` + (r.body || '')).slice(0, 3200);
}

module.exports = { handleOwnerCommand, parseOwnerCommand, parseWhen };
