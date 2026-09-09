/* God Quant WhatsApp bridge #2 — Baileys edition (NO BROWSER, Termux ✅).
 *
 * Pure JS — no Chrome, no Puppeteer, no Selenium (none of those run on
 * Android; Selenium needs browser binaries too, so it can't save Termux).
 * Login via pairing code (headless-friendly) or QR in terminal.
 *
 * Run:  npm install @whiskeysockets/baileys qrcode-terminal axios pino
 *       python gq.py serve &
 *       AI_SERVER_URL=http://localhost:5000 WA_PAIR_NUMBER=2348012345678 node bridges/whatsapp_baileys.js
 *       # enter the printed pairing code on your phone: WhatsApp → Linked Devices → Link with code
 *       # (or omit WA_PAIR_NUMBER to print a QR instead)
 *
 * Env: AI_SERVER_URL, WA_PERSONA, WA_SEARCH=1, WA_HUMANIZE=1, HUMAN_WPM=45,
 *      HUMAN_SPLIT=1, WA_GROUP_OPEN=1, ALLOWED_NUMBERS (csv, empty = all),
 *      WA_PAIR_NUMBER=234..., WA_AUTH=wa_auth_baileys
 */
const baileys = require('@whiskeysockets/baileys');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const pino = require('pino');

console.log('\n' + '='.repeat(60));
console.log('🤖 WhatsApp Baileys bridge (no browser) starting...');
console.log('='.repeat(60) + '\n');

const AI_SERVER_URL = process.env.AI_SERVER_URL || 'http://localhost:5000';
const AUTH_DIR = process.env.WA_AUTH || 'wa_auth_baileys';
const PAIR_NUMBER = (process.env.WA_PAIR_NUMBER || '').replace(/[^0-9]/g, '');
const ALLOWED_NUMBERS = (process.env.ALLOWED_NUMBERS || '')
  .split(',').map(s => s.trim()).filter(Boolean);
const RESPOND_TO_ALL = ALLOWED_NUMBERS.length === 0;
const GROUP_OPEN = process.env.WA_GROUP_OPEN === '1';

let SELF = '';  // own JID, learned on connect

// ---------- humanizer (mirrors godquant/companion/humanize.py) ----------
const HUMANIZE = process.env.WA_HUMANIZE !== '0';
const HUMAN_WPM = parseInt(process.env.HUMAN_WPM || '45', 10);
const HUMAN_SPLIT = process.env.HUMAN_SPLIT !== '0';

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
function readDelay(text) {
    return Math.min(7000, 600 + (text || '').length * 8);
}
function typingDelay(text) {
    const cps = HUMAN_WPM * 5 / 60;
    return Math.min(22000, 900 + (text || '').length / cps * 1000);
}
function splitBubbles(text) {
    const t = (text || '').trim();
    if (!t) return [];
    if (!HUMAN_SPLIT) return [t];
    const paras = t.split(/\n{2,}/).map(s => s.trim()).filter(Boolean);
    if (paras.length > 1) return paras.slice(0, 3);
    if (t.length <= 320) return [t];
    const out = [];
    let rest = t;
    while (rest.length > 320 && out.length < 2) {
        let cut = rest.lastIndexOf('. ', 320);
        if (cut < 120) cut = rest.lastIndexOf(' ', 320);
        if (cut < 120) cut = 320;
        out.push(rest.slice(0, cut + 1).trim());
        rest = rest.slice(cut + 1).trim();
    }
    if (rest) out.push(rest);
    return out.slice(0, 3);
}
async function humanSend(sock, jid, text, quoteM) {
    const bubbles = splitBubbles(text);
    if (!HUMANIZE) {
        for (const b of bubbles) await sock.sendMessage(jid, { text: b });
        return;
    }
    await sleep(readDelay(text));
    for (let i = 0; i < bubbles.length; i++) {
        if (i) await sleep(900 + Math.random() * 1400);
        const total = typingDelay(bubbles[i]);
        const t0 = Date.now();
        try {
            while (Date.now() - t0 < total) {
                await sock.sendPresenceUpdate('composing', jid);
                await sleep(Math.min(4000, Math.max(50, total - (Date.now() - t0))));
            }
        } catch (e) { await sleep(total); }
        try { await sock.sendPresenceUpdate('paused', jid); } catch (e2) {}
        const opts = (quoteM && i === 0) ? { quoted: quoteM } : {};
        await sock.sendMessage(jid, { text: bubbles[i] }, opts);
    }
}

// ---------- self alerts ----------
const lastWarn = {};
async function warnSelf(kind, msg) {
    const now = Date.now();
    if (now - (lastWarn[kind] || 0) < 300000) return;
    lastWarn[kind] = now;
    try {
        await axios.post(`${AI_SERVER_URL}/warn`, {
            channel: 'whatsapp', kind: kind,
            message: ('' + msg).slice(0, 400)
        }, { timeout: 10000 });
    } catch (e) { /* brain down — nothing to do */ }
}

// ---------- brain ----------
async function getAIResponse(message, chatId, senderName, isGroup, bondId) {
    try {
        const prefix = process.env.WA_MISSION_PREFIX || '!';
        if (message.startsWith(prefix)) {
            const response = await axios.post(`${AI_SERVER_URL}/mission`, {
                goal: message.slice(prefix.length).trim()
            }, { timeout: 300000 });
            const parts = (response.data.results || []).map(r => `[${r.agent}] ${r.output}`);
            return parts.join('\n\n').slice(0, 4000) || 'mission done, no output 🤔';
        }
        const response = await axios.post(`${AI_SERVER_URL}/chat`, {
            message: message,
            conversation_id: chatId,
            channel: 'whatsapp',
            chat_id: chatId,
            display: senderName || '',
            sender_name: senderName || '',
            is_group: !!isGroup,
            bond_id: bondId || chatId,
            use_search: process.env.WA_SEARCH === '1',
            persona: process.env.WA_PERSONA || undefined
        }, { timeout: 120000 });
        if (response.data && response.data.response) {
            return response.data.response;  // clean — mood stays server-side
        }
        return "hey baby, something's not working on my end rn 😅";
    } catch (error) {
        console.error('❌ Error calling AI:', error.message);
        await warnSelf('brain', 'AI backend unreachable: ' + error.message);
        if (error.code === 'ECONNREFUSED') {
            return "baby my brain isn't connected rn, make sure the Python server is running 😔";
        }
        return 'ugh having connection issues, give me a sec 💔';
    }
}

async function registerContact(chatId, display) {
    try {
        await axios.post(`${AI_SERVER_URL}/contacts`, {
            channel: 'whatsapp', chat_id: chatId, display: display || ''
        }, { timeout: 10000 });
    } catch (e) { /* server will catch up later */ }
}

function normJid(j) {
    return (j || '').split(':')[0].split('@')[0];
}

function shouldRespond(number) {
    if (RESPOND_TO_ALL) return true;
    return ALLOWED_NUMBERS.some(allowed => {
        const a = allowed.replace(/[^0-9]/g, '');
        return number === a || number.endsWith(a) || a.endsWith(number);
    });
}

const lastMessageTime = new Map();
function isSpamming(chatId) {
    const now = Date.now();
    const last = lastMessageTime.get(chatId) || 0;
    if (now - last < 3000) return true;
    lastMessageTime.set(chatId, now);
    return false;
}

async function handleMessage(sock, m) {
    if (!m.message || m.key.fromMe) return;
    const jid = m.key.remoteJid || '';
    if (!jid) return;
    const msg = m.message;
    const ctx = (msg.extendedTextMessage && msg.extendedTextMessage.contextInfo) || {};
    const text = (msg.conversation || (msg.extendedTextMessage && msg.extendedTextMessage.text) ||
                  (msg.imageMessage && msg.imageMessage.caption) || '').trim();
    if (!text) return;
    const isGroup = jid.endsWith('@g.us');
    const sender = m.key.participant || jid;
    const number = normJid(sender);
    const name = m.pushName || number;

    // groups: mention/reply only unless WA_GROUP_OPEN=1
    if (isGroup && !GROUP_OPEN) {
        const mentionedIds = ctx.mentionedJid || [];
        let mentioned = mentionedIds.some(x => normJid(x) === normJid(SELF));
        if (!mentioned && ctx.quotedMessage) {
            mentioned = normJid(ctx.participant) === normJid(SELF);
        }
        if (!mentioned) return;
    }
    if (!shouldRespond(number)) {
        console.log(`⏭ Ignored ${name} (${number}) — not in allowed list`);
        return;
    }
    if (isSpamming(jid)) {
        console.log('⏭ Rate limited — message too soon');
        return;
    }
    console.log(`\n📨 Message from ${name}:`);
    console.log(`   "${text}"`);
    registerContact(isGroup ? sender : jid, name);
    if (isGroup) registerContact(jid, jid);
    const bondId = 'whatsapp:' + number;
    console.log('🤖 Generating AI response...');
    const aiResponse = await getAIResponse(text, jid, name, isGroup, bondId);
    console.log(`💬 AI Response: "${aiResponse}"\n`);
    try {
        await humanSend(sock, jid, aiResponse, m);
        console.log('✅ Response sent!\n');
    } catch (e) {
        console.error('❌ Send failed:', e.message);
        await warnSelf('send', 'send to ' + jid + ' failed: ' + e.message);
    }
}

const OUTBOX_POLL = parseInt(process.env.WA_POLL || '15', 10) * 1000;
async function pollOutbox(sock) {
    try {
        const { data } = await axios.get(`${AI_SERVER_URL}/outbox?channel=whatsapp`, { timeout: 15000 });
        for (const item of (data.pending || [])) {
            try {
                await humanSend(sock, item.to, item.message, null);
                await axios.post(`${AI_SERVER_URL}/ack`, { id: item.id, ok: true }, { timeout: 10000 });
                console.log(`✉ sent → ${item.to}`);
            } catch (e) {
                console.error(`send → ${item.to} failed:`, e.message);
                await warnSelf('send', 'outbox send to ' + item.to + ' failed: ' + e.message);
                try { await axios.post(`${AI_SERVER_URL}/ack`, { id: item.id, ok: false }); } catch (_) {}
            }
        }
    } catch (e) { /* server down — retry next poll */ }
}

async function startSock() {
    const { state, saveCreds } = await baileys.useMultiFileAuthState(AUTH_DIR);
    const { version } = await baileys.fetchLatestBaileysVersion();
    const sock = baileys.default({
        version: version,
        auth: state,
        logger: pino({ level: 'silent' }),
        browser: ['GodQuant', 'Termux', '1.0'],
        syncFullHistory: false
    });
    sock.ev.on('creds.update', saveCreds);
    if (!sock.authState.creds.registered && PAIR_NUMBER) {
        await sleep(3000);
        try {
            const code = await sock.requestPairingCode(PAIR_NUMBER);
            console.log('\n🔑 PAIRING CODE:', code);
            console.log('   WhatsApp → Linked Devices → Link with phone number → enter code\n');
        } catch (e) {
            console.error('pairing code failed:', e.message);
        }
    }
    sock.ev.on('connection.update', async (u) => {
        const connection = u.connection;
        const qr = u.qr;
        if (qr && !PAIR_NUMBER) {
            qrcode.generate(qr, { small: true });
        }
        if (connection === 'open') {
            SELF = (sock.user && sock.user.id) || '';
            console.log('✅ WhatsApp connected as', SELF);
            console.log('   (set GQ_OWNER_WA to your own chat JID for self alerts)');
            setTimeout(function poll() {
                pollOutbox(sock).catch(() => {});
                setTimeout(poll, OUTBOX_POLL);
            }, 5000);
        }
        if (connection === 'close') {
            const err = u.lastDisconnect && u.lastDisconnect.error;
            const code = err && err.output && err.output.statusCode;
            const loggedOut = code === baileys.DisconnectReason.loggedOut;
            console.log('⚠ connection closed' + (loggedOut ? ' (LOGGED OUT)' : ', reconnecting...'));
            if (!loggedOut) {
                await sleep(3000);
                startSock().catch(e => console.error(e.message));
            } else {
                console.log(`delete ${AUTH_DIR}/ and re-run to re-link`);
            }
        }
    });
    sock.ev.on('messages.upsert', async (up) => {
        if (up.type !== 'notify') return;
        for (const m of (up.messages || [])) {
            try {
                await handleMessage(sock, m);
            } catch (e) {
                console.error('❌ Error handling message:', e.message);
                await warnSelf('reply', 'handler crashed: ' + e.message);
            }
        }
    });
    return sock;
}

startSock().catch(e => {
    console.error('fatal:', e.message);
    process.exit(1);
});
