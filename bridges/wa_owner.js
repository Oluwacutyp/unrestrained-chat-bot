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
    '`.forget <chat> [deep]` `.models` `.model <name>` `.stats` `.import [limit]` `.help`';

function parseOwnerCommand(text) {
    const t = (text || '').trim();
    if (!t.startsWith('.') || t.length < 2) return null;
    const sp = t.indexOf(' ');
    if (sp < 0) return { cmd: t.slice(1).toLowerCase(), arg: '' };
    return { cmd: t.slice(1, sp).toLowerCase(), arg: t.slice(sp + 1).trim() };
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

module.exports = { handleOwnerCommand, parseOwnerCommand };
