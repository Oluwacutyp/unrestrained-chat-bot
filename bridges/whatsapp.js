/* God Quant WhatsApp bridge (from the original whatsapp.js handler).
 *
 * whatsapp-web.js → God Quant unified server (/chat contract unchanged).
 * Run:  npm install whatsapp-web.js qrcode-terminal axios
 *       python gq.py serve &   (or any partner_original server on :5000)
 *       AI_SERVER_URL=http://localhost:5000 WA_PERSONA=alex WA_SEARCH=1 node bridges/whatsapp.js
 *
 * Env: AI_SERVER_URL (default localhost:5000), ALLOWED_NUMBERS (csv, empty = all),
 *      WA_SEARCH=1 (web search on replies), WA_PERSONA=alex|companion|realistic|quant,
 *      WA_MISSION_PREFIX=! (messages starting with ! run full agent missions)
 *      WA_HUMANIZE=1 (0 = instant sends), HUMAN_WPM=45, HUMAN_SPLIT=1,
 *      WA_GROUP_OPEN=1 (reply to every group msg; default = mentions/replies only)
 */
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

console.log('\n' + '='.repeat(60));
console.log('🤖 WhatsApp AI Partner Bot Starting...');
console.log('='.repeat(60) + '\n');

// Configuration
const AI_SERVER_URL = process.env.AI_SERVER_URL || 'http://localhost:5000';  // Your Python server
const CONVERSATION_ID = 'whatsapp_session';
// Allowed numbers (your number or numbers you want bot to respond to)
// Format: countrycode + number without +, spaces, or dashes
// Example: USA number +1 234-567-8900 becomes '12345678900'
// Tip: or set ALLOWED_NUMBERS="2348012345678,2349087654321" env (comma-separated)
const ALLOWED_NUMBERS = (process.env.ALLOWED_NUMBERS || '')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean);
// const ALLOWED_NUMBERS = [
//     // '2348012345678',  // Add your number here
//     // '2349087654321',  // Add other numbers if needed
// ];
// If empty, bot responds to EVERYONE (not recommended)
const RESPOND_TO_ALL = ALLOWED_NUMBERS.length === 0;

// Initialize WhatsApp client with authentication
const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'ai-partner-bot'
    }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--no-first-run',
            '--no-zygote',
            '--disable-gpu'
        ]
    }
});

// Store active conversations
const activeChats = new Set();
const lastMessageTime = new Map();

// QR Code generation (for first-time login)
client.on('qr', (qr) => {
    console.log('\n' + '='.repeat(60));
    console.log('📱 SCAN THIS QR CODE WITH YOUR WHATSAPP');
    console.log('='.repeat(60));
    console.log('\n1. Open WhatsApp on your phone');
    console.log('2. Tap Menu ( ⋮ ) → Linked Devices');
    console.log('3. Tap "Link a Device"');
    console.log('4. Point your phone at this QR code:\n');

    qrcode.generate(qr, { small: true });

    console.log('\n⏱ QR Code expires in 60 seconds - scan quickly!\n');
});

// Ready event
client.on('ready', () => {
    console.log('\n' + '='.repeat(60));
    console.log('✅ WhatsApp AI Partner Bot is ONLINE!');
    console.log('='.repeat(60));
    console.log('\n💕 Your AI partner is now connected to WhatsApp');
    console.log('📱 Bot will respond to incoming messages automatically');
    console.log('🌐 Web interface still available at: http://localhost:5000');
    console.log('\n💡 Press Ctrl+C to stop the bot\n');
});

// Authentication success
client.on('authenticated', () => {
    console.log('✅ Authentication successful!');
});

// Authentication failure
client.on('auth_failure', (msg) => {
    console.error('❌ Authentication failed:', msg);
    console.log('\nTry deleting the .wwebjs_auth folder and scanning QR again');
});

// Disconnected
client.on('disconnected', (reason) => {
    console.log('⚠ WhatsApp disconnected:', reason);
    console.log('Bot will attempt to reconnect...');
});

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
// Send like a human: read pause → typing scaled to length → bubbles.
// Re-fires sendStateTyping in a loop (WA drops the indicator after ~5s).
async function humanSend(chat, message, text) {
    const bubbles = splitBubbles(text);
    if (!HUMANIZE) {
        for (const b of bubbles) await chat.sendMessage(b);
        return;
    }
    await sleep(readDelay(text));
    for (let i = 0; i < bubbles.length; i++) {
        if (i) await sleep(900 + Math.random() * 1400);
        const total = typingDelay(bubbles[i]);
        const t0 = Date.now();
        try {
            while (Date.now() - t0 < total) {
                await chat.sendStateTyping();
                await sleep(Math.min(4000, Math.max(50, total - (Date.now() - t0))));
            }
        } catch (e) { await sleep(total); }
        if (message && i === 0) await message.reply(bubbles[i]);
        else await chat.sendMessage(bubbles[i]);
    }
    try { await chat.clearState(); } catch (e) {}
}

// Function to call AI backend
async function getAIResponse(message, chatId, senderName, isGroup, bondId) {
    try {
        // Mission mode: "!backtest RSI on BTC" runs the full multi-agent swarm
        const prefix = process.env.WA_MISSION_PREFIX || '!';
        if (message.startsWith(prefix)) {
            const response = await axios.post(`${AI_SERVER_URL}/mission`, {
                goal: message.slice(prefix.length).trim()
            }, { timeout: 300000 });
            const parts = (response.data.results || []).map(r => `[${r.agent}] ${r.output}`);
            return parts.join('\n\n').slice(0, 4000) || "mission done, no output 🤔";
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
        }, {
            timeout: 120000  // 120 second timeout (local models can be slow)
        });
        if (response.data && response.data.response) {
            return response.data.response;  // clean — mood stays server-side
        } else {
            return "hey baby, something's not working on my end rn 😅";
        }
    } catch (error) {
        console.error('❌ Error calling AI:', error.message);

        if (error.code === 'ECONNREFUSED') {
            return "baby my brain isn't connected rn, make sure the Python server is running 😔";
        }

        return "ugh having connection issues, give me a sec 💔";
    }
}

// Check if should respond to this number
function shouldRespond(number) {
    if (RESPOND_TO_ALL) {
        return true;
    }

    // Remove any @ symbols and clean the number
    const cleanNumber = number.replace('@c.us', '').replace(/[^0-9]/g, '');

    return ALLOWED_NUMBERS.some(allowed => {
        const cleanAllowed = allowed.replace(/[^0-9]/g, '');
        return cleanNumber === cleanAllowed || cleanNumber.endsWith(cleanAllowed);
    });
}

// Anti-spam: Check if message is too soon
function isSpamming(chatId) {
    const now = Date.now();
    const lastTime = lastMessageTime.get(chatId) || 0;
    const timeDiff = now - lastTime;

    // Allow message every 2 seconds minimum
    if (timeDiff < 2000) {
        return true;
    }

    lastMessageTime.set(chatId, now);
    return false;
}

// Message handler
client.on('message', async (message) => {
    try {
        // Get chat info
        const chat = await message.getChat();
        const contact = await message.getContact();
        const isGroup = chat.isGroup;

        // Groups: respond on mention/reply (or everything with WA_GROUP_OPEN=1)
        if (isGroup && process.env.WA_GROUP_OPEN !== '1') {
            const botId = (client.info && client.info.wid) ? client.info.wid._serialized : '';
            let mentioned = (message.mentionedIds || []).includes(botId);
            if (!mentioned && message.hasQuotedMsg) {
                try { mentioned = (await message.getQuotedMessage()).fromMe; }
                catch (e) { mentioned = false; }
            }
            if (!mentioned) return;
        }

        // Ignore own messages
        if (message.fromMe) {
            return;
        }

        // Check if should respond to this number
        if (!shouldRespond(contact.number)) {
            console.log(`⏭ Ignored message from ${contact.pushname || contact.number} (not in allowed list)`);
            return;
        }

        // Anti-spam check
        if (isSpamming(chat.id._serialized)) {
            console.log('⏭ Rate limited - message too soon');
            return;
        }

        // Get message text
        const messageText = message.body.trim();

        if (!messageText) {
            return;  // Ignore empty messages
        }

        console.log(`\n📨 Message from ${contact.pushname || contact.number}:`);
        console.log(`   "${messageText}"`);

        // Add to active chats
        activeChats.add(chat.id._serialized);
        registerContact(chat.id._serialized, contact.pushname || contact.number);

        // Get AI response (groups share one context; bonds stay per-sender)
        const senderName = contact.pushname || contact.number;
        const bondId = 'whatsapp:' + (contact.number || chat.id._serialized);
        console.log('🤖 Generating AI response...');
        const aiResponse = await getAIResponse(messageText, chat.id._serialized,
                                               senderName, isGroup, bondId);

        console.log(`💬 AI Response: "${aiResponse}"\n`);

        // Human send: read pause → typing scaled to length → bubbles
        await humanSend(chat, message, aiResponse);

        console.log('✅ Response sent!\n');

    } catch (error) {
        console.error('❌ Error handling message:', error.message);

        try {
            await message.reply("hey baby, something went wrong on my end 😔 try again?");
        } catch (replyError) {
            console.error('Failed to send error message:', replyError.message);
        }
    }
});

// Handle incoming calls (optional - auto-reject)
client.on('call', async (call) => {
    console.log(`📞 Incoming call from ${call.from} - Auto-rejecting`);
    await call.reject();

    // Optionally send a message
    try {
        const chat = await client.getChatById(call.from);
        await chat.sendMessage("hey baby, can't take calls rn! text me instead 💕");
    } catch (error) {
        console.error('Error sending call rejection message:', error);
    }
});

// Register a sighting so proactive texting + silence tracking work
async function registerContact(chatId, display) {
    try {
        await axios.post(`${AI_SERVER_URL}/contacts`, {
            channel: 'whatsapp', chat_id: chatId, display: display || ''
        }, { timeout: 10000 });
    } catch (e) { /* server will catch up later */ }
}

// Outbox delivery loop — this is how the bot TEXTS FIRST (incl. texting itself:
// queue `to` = your own 1234@c.us chat id via POST /send or the proactive ticker)
const OUTBOX_POLL = parseInt(process.env.WA_POLL || '15', 10) * 1000;
async function pollOutbox() {
    try {
        const { data } = await axios.get(`${AI_SERVER_URL}/outbox?channel=whatsapp`, { timeout: 15000 });
        for (const item of (data.pending || [])) {
            try {
                try {
                    const outChat = await client.getChatById(item.to);
                    await humanSend(outChat, null, item.message);
                } catch (e2) {
                    await client.sendMessage(item.to, item.message);
                }
                await axios.post(`${AI_SERVER_URL}/ack`, { id: item.id, ok: true }, { timeout: 10000 });
                console.log(`✉ sent → ${item.to}`);
            } catch (e) {
                console.error(`send → ${item.to} failed:`, e.message);
                try { await axios.post(`${AI_SERVER_URL}/ack`, { id: item.id, ok: false }); } catch (_) {}
            }
        }
    } catch (e) { /* server down — retry next poll */ }
    setTimeout(pollOutbox, OUTBOX_POLL);
}
client.on('ready', () => { setTimeout(pollOutbox, 5000); });

// Initialize client
console.log('🔄 Initializing WhatsApp client...');
console.log('⏱ This may take 30-60 seconds...\n');
client.initialize();

// Graceful shutdown
process.on('SIGINT', async () => {
    console.log('\n\n⚠ Shutting down gracefully...');
    await client.destroy();
    console.log('✅ WhatsApp bot stopped\n');
    process.exit(0);
});

// Error handling
process.on('unhandledRejection', (error) => {
    console.error('❌ Unhandled error:', error);
});
