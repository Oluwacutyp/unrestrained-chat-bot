/* Node-side tests for bridges/wa_owner.js (run via pytest subprocess). */
const ownerCmd = require('../bridges/wa_owner');

let n = 0;
function eq(a, b, msg) {
    n++;
    if (a !== b) {
        console.error(`FAIL#${n} ${msg}: got ${JSON.stringify(a)} want ${JSON.stringify(b)}`);
        process.exit(1);
    }
}
function has(a, b, msg) {
    n++;
    if (!String(a).includes(b)) {
        console.error(`FAIL#${n} ${msg}: ${JSON.stringify(a)} lacks ${JSON.stringify(b)}`);
        process.exit(1);
    }
}

async function main() {
    // parse
    eq(ownerCmd.parseOwnerCommand('hello'), null, 'non-cmd');
    eq(ownerCmd.parseOwnerCommand('.'), null, 'dot-only');
    let p = ownerCmd.parseOwnerCommand('.BOND 42 3');
    eq(p.cmd, 'bond', 'cmd lower');
    eq(p.arg, '42 3', 'arg keep');
    let w = ownerCmd.parseWhen('in 30m', 1700000000000);
    eq(w.due, 1700000000 + 1800, 'when in');
    eq(w.repeat, '', 'when repeat blank');
    eq(ownerCmd.parseWhen('every day 08:00', 1700000000 - 0).repeat, 'daily', 'when daily');
    eq(ownerCmd.parseWhen('tomorrow 07:00', 1700000000000).due > 1700000000, true, 'when tomorrow');
    eq(ownerCmd.parseWhen('someday'), null, 'when bad');

    // fake transport
    const seen = [];
    async function post(path, payload, ms) {
        seen.push([path, payload]);
        if (path === '/tick') return { queued: [] };
        if (path === '/send') return { queued: 7 };
        if (path === '/contacts') return { contacts: [{ channel: 'whatsapp', chat_id: '1', display: 'Zed', enabled: true }] };
        if (path.startsWith('/mood')) return { current: 'happy', level: 8 };
        if (path === '/reset') return { message: 'Fresh start' };
        if (path === '/mission') return { results: [{ agent: 'a', output: 'did it' }] };
        if (path === '/code') return { path: '/w/code/x.py', summary: 'ok' };
        if (path === '/exec') return { exit: 0, output: 'hi\n' };
        if (path === '/models') return { primary: 'groq', chain: ['groq', 'heuristic'], usage: { llm_calls: 3, spend_usd: 0 } };
        if (path === '/model') return payload.primary === 'nope' ? { error: 'unknown' } : { primary: payload.primary || 'llamacpp', model: payload.model, gguf: payload.gguf };
        if (path === '/persona' && payload && payload.pack_action) return payload.pack_action === 'list' ? { packs: ['zed'] } : payload.pack_action === 'create' ? { created: payload.name } : { name: payload.name, pack: { blurb: 'b', voice: 'soft' }, learned: 'loved short' };
        if (path === '/persona' && payload && payload.persona) return { default: payload.persona };
        if (path === '/persona') return { default: 'devon', overrides: [] };
        if (path.startsWith('/persona?')) return { persona: 'alex', override: true };
        if (path === '/bond') return { bond: { level: 3 } };
        if (path.startsWith('/bond?')) return { bond: { level: 1, score: 20, friction: 0, streak: 2, manual: false } };
        if (path.startsWith('/memory')) return { facts: [{ key: 'like', value: 'suya' }], bond: { level: 1 } };
        if (path === '/forget') return { facts: 4, deep: !!payload.deep };
        if (path === '/import') return { imported: 50, facts: ['name=Mary'] };
        if (path === '/status') return { version: '3.6.0', status: 'online', memories: { chat: 5 }, llm_calls: 9, spend_usd: 0, persona: 'devon' };
        if (path === '/remind' && payload && payload.action === 'add') return { id: 5 };
        if (path === '/remind' && payload && payload.action === 'snooze') return { snoozed: payload.id === 5 };
        if (path === '/remind') return payload && payload.action === 'cancel'
            ? { cancelled: payload.id === 5 }
            : { reminders: [{ id: 5, channel: 'whatsapp', chat_id: '1@c.us', text: 'call mom', repeat: '', due_ts: 2000000000 }] };
        if (path === '/want') return { id: 9 };
        if (path === '/mind') return payload ? { ok: true } : { intentions: [{ id: 9, kind: 'custom', text: 'learn drums' }], reminders: [], dream: { chats: 2, merged: 1 } };
        if (path.startsWith('/journal')) return { entries: [{ day: '2026-09-08', entry: 'met Zed' }] };
        if (path === '/note' && payload.action === 'save') return { saved: payload.name };
        if (path === '/note' && payload.action === 'get') return payload.name === 'wifi' ? { body: 'pw is jollof' } : { error: 'x' };
        if (path === '/note') return payload.action === 'list' ? { notes: ['wifi'] } : { deleted: true };
        if (path === '/dream') return { chats: 2, merged: 1, journal: 2, intentions: [1], conflicts: ['a b → c'] };
        if (path === '/brief') return { brief: '☀️ brief — today' };
        if (path === '/fetch') return payload.url === 'bad' ? { error: 'nope' } : { title: 'T', text: 'hello world' };
        if (path.startsWith('/recall')) return { hits: [{ id: 3, layer: 'semantic', scope: 'global', content: 'vault code', pinned: true }] };
        if (path.startsWith('/memories?')) return { memories: [{ id: 3, layer: 'semantic', scope: 'global', content: 'vault code', pinned: false }] };
        if (path === '/memories') return { ok: payload.id === 3 };
        if (path === '/missions' && payload.action === 'create') return { started: true };
        if (path === '/missions' && payload.action === 'resume') return payload.id === 1 ? { started: true } : { error: 'x' };
        if (path === '/missions') return { missions: [{ id: 1, status: 'done', goal: 'research lizards' }] };
        if (path === '/cal' && payload.action === 'add') return { id: 3 };
        if (path === '/cal') return (payload.action === 'done' || payload.action === 'del') ? { ok: true } : { events: [{ id: 3, title: 'dentist', ts: 2000000000, repeat: '', done: false }] };
        if (path === '/ledger' && payload.action === 'add') return { id: 4 };
        if (path === '/ledger' && payload.action === 'total') return { total: 2500 };
        if (path === '/ledger') return { entries: [{ id: 4, amount: 2500, currency: 'NGN', cat: 'food', note: 'lunch' }] };
        if (path === '/health' && payload.action === 'log') return { id: 5 };
        if (path === '/health') return { entries: [{ metric: 'sleep', value: '7h' }] };
        if (path === '/bible' && (payload.action === 'save' || payload.action === 'new')) return { ok: true };
        if (path === '/bible' && payload.action === 'get') return payload.name === 'midgard' ? { body: 'CANON' } : { error: 'x' };
        if (path === '/bible') return { bibles: ['midgard'] };
        if (path === '/research') return payload.query ? { brief: 'BRIEF-BODY' } : { error: 'x' };
        if (path === '/pref') return payload.cid === 'whatsapp:nobody' ? { error: 'x' } : { logged: payload.verdict };
        if (path === '/train/status') return { trajectories: 10, prefs: 2, pairs: 1, bytes: 99 };
        if (path === '/train/export') return { sft: 9, dpo: 1, skipped: 1, dir: '/w/train' };
        if (path === '/train/push') return { ok: true, repo: 'u/d', pushed: ['sft.jsonl'] };
        if (path.startsWith('/train/script')) return { script: 'SFTTrainer...' };
        throw new Error('unexpected ' + path);
    }
    const ctx = {
        post, channel: 'whatsapp', chatId: '100@c.us',
        fetchHistory: async (limit) => [{ role: 'user', text: 'hi', ts: 1, sender: '100' }]
    };
    const run = (t, c) => ownerCmd.handleOwnerCommand(t, c || ctx);

    eq(await run('not a cmd'), null, 'null passthrough');
    has(await run('.help'), 'wa cmds', 'help');
    has(await run('.frobnicate'), 'wa cmds', 'unknown→help');
    eq(await run('.tick'), 'nothing due 😴', 'tick');
    has(await run('.send 1@c.us yo'), '#7', 'send');
    has(await run('.send x'), 'usage', 'send usage');
    has(await run('.contacts'), 'Zed', 'contacts');
    has(await run('.mood'), 'happy', 'mood');
    eq(await run('.reset'), 'reset ✨', 'reset');
    has(await run('.mission'), 'usage', 'mission usage');
    has(await run('.mission do stuff'), '[a] did it', 'mission');
    has(await run('.code fizz'), 'x.py', 'code');
    has(await run('.exec echo hi'), 'hi', 'exec');
    has(await run('.models'), 'groq', 'models');
    has(await run('.model groq'), 'groq', 'model ok');
    has(await run('.model nope'), 'failed', 'model bad');
    has(await run('.model list'), 'groq', 'model list');
    has(await run('.model load u/m'), 'u/m', 'model load');
    has(await run('.model load /x/y.gguf'), 'y.gguf', 'model load gguf');
    has(await run('.persona'), 'devon', 'persona show');
    has(await run('.persona alex'), 'alex', 'persona global');
    has(await run('.persona 42'), 'override', 'persona chat show');
    has(await run('.persona 42 quant'), 'quant', 'persona chat set');
    has(await run('.persona 42 clear'), 'cleared', 'persona clear');
    has(await run('.persona 42 xxx'), 'unknown persona', 'persona bad');
    has(await run('.bond 42'), 'bond[42]', 'bond view');
    has(await run('.bond 42 3'), 'pinned', 'bond pin');
    has(await run('.bond 42 9'), 'usage', 'bond bad');
    has(await run('.memory 42'), 'suya', 'memory');
    has(await run('.forget 42'), '4 facts', 'forget');
    has(await run('.forget 42 deep'), 'zeroed', 'forget deep');
    has(await run('.stats'), '3.6.0', 'stats');
    has(await run('.import'), 'imported 50', 'import ok');
    const nohist = Object.assign({}, ctx, { fetchHistory: null });
    has(await run('.import', nohist), 'not supported', 'import unsupported');

    has(await run('.remind in 2h call mom'), '#5', 'remind');
    has(await run('.remind someday x'), 'usage', 'remind usage');
    has(await run('.reminders'), 'call mom', 'reminders');
    has(await run('.cancel 5'), 'cancelled', 'cancel');
    has(await run('.cancel x'), 'usage', 'cancel usage');
    has(await run('.want learn drums'), '#9', 'want');
    has(await run('.mind'), 'learn drums', 'mind');
    has(await run('.mind done 9'), 'updated', 'mind done');
    has(await run('.journal'), 'met Zed', 'journal');
    has(await run('.note save wifi pw is jollof'), '[wifi]', 'note save');
    has(await run('.note wifi'), 'jollof', 'note get');
    has(await run('.note list'), 'wifi', 'note list');
    has(await run('.dream'), 'conflicts', 'dream');
    has(await run('.brief'), 'brief', 'brief');
    has(await run('.fetch https://x'), 'hello', 'fetch');
    has(await run('.fetch'), 'usage', 'fetch usage');
    has(await run('.snooze 5 in 2h'), 'snoozed', 'snooze');
    has(await run('.snooze x'), 'usage', 'snooze usage');
    has(await run('.snooze 9 in 2h'), 'no such', 'snooze missing');
    has(await run('.recall vault'), '#3', 'recall');
    has(await run('.recall'), 'usage', 'recall usage');
    has(await run('.mem list'), '#3', 'mem list');
    has(await run('.mem pin 3'), 'done', 'mem pin');
    has(await run('.mem del 9'), 'no such', 'mem missing');
    has(await run('.mem edit 3 new text'), 'edited', 'mem edit');
    has(await run('.project research lizards'), 'started', 'project');
    has(await run('.project'), 'usage', 'project usage');
    has(await run('.projects'), '#1', 'projects');
    has(await run('.resume 1'), 'resumed', 'resume');
    has(await run('.good'), 'banked', 'good');
    has(await run('.bad'), "won't", 'bad');
    has(await run('.train'), 'trajectories: 10', 'train status');
    has(await run('.train export'), 'sft=9', 'train export');
    has(await run('.train push u/d'), 'u/d', 'train push');
    has(await run('.train script'), 'SFTTrainer', 'train script');
    has(await run('.cal add in 2h dentist'), '#3', 'cal add');
    has(await run('.cal'), 'dentist', 'cal list');
    has(await run('.cal done 3'), 'done', 'cal done');
    has(await run('.spend 2500 NGN food lunch'), '#4', 'spend');
    has(await run('.ledger'), '2500', 'ledger');
    has(await run('.health sleep 7h'), 'logged', 'health');
    has(await run('.healthlog'), 'sleep', 'healthlog');
    has(await run('.bible save midgard CANON'), 'appended', 'bible save');
    has(await run('.bible midgard'), 'CANON', 'bible get');
    has(await run('.bible list'), 'midgard', 'bible list');
    has(await run('.research lagos weather'), 'BRIEF', 'research');
    has(await run('.research'), 'usage', 'research usage');
    has(await run('.persona create zed'), 'created', 'persona create');
    has(await run('.persona show zed'), 'soft', 'persona show');
    has(await run('.persona list'), 'zed', 'persona list');
    console.log(`wa_owner.js OK (${n} asserts)`);
}

main().catch(e => { console.error('FATAL', e); process.exit(1); });
