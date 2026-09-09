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
    eq(ownerCmd.parseOwnerCommand('.').cmd, '', 'dot-only → help');
    let p = ownerCmd.parseOwnerCommand('.BOND 42 3');
    eq(p.cmd, 'bond', 'cmd lower');
    eq(p.arg, '42 3', 'arg keep');

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
        if (path === '/model') return payload.primary === 'nope' ? { error: 'unknown' } : { primary: payload.primary };
        if (path === '/persona' && payload && payload.persona) return { default: payload.persona };
        if (path === '/persona') return { default: 'devon', overrides: [] };
        if (path.startsWith('/persona?')) return { persona: 'alex', override: true };
        if (path === '/bond') return { bond: { level: 3 } };
        if (path.startsWith('/bond?')) return { bond: { level: 1, score: 20, friction: 0, streak: 2, manual: false } };
        if (path.startsWith('/memory')) return { facts: [{ key: 'like', value: 'suya' }], bond: { level: 1 } };
        if (path === '/forget') return { facts: 4, deep: !!payload.deep };
        if (path === '/import') return { imported: 50, facts: ['name=Mary'] };
        if (path === '/status') return { version: '3.6.0', status: 'online', memories: { chat: 5 }, llm_calls: 9, spend_usd: 0, persona: 'devon' };
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

    console.log(`wa_owner.js OK (${n} asserts)`);
}

main().catch(e => { console.error('FATAL', e); process.exit(1); });
