import 'dotenv/config';
import makeWASocket, { 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestWaWebVersion,
    makeCacheableSignalKeyStore,
    Browsers
} from '@whiskeysockets/baileys';
import pino from 'pino';
import Redis from 'ioredis';
import fs from 'fs';

// Logger
const logger = pino({ level: 'debug' });

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const SESSION_DIR = './sessions';
const INBOUND_CHANNEL = process.env.WHATSAPP_INBOUND_CHANNEL || 'inbound';
const OUTBOUND_CHANNEL = process.env.WHATSAPP_OUTBOUND_CHANNEL || 'outbound';
const PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER;

// Ensure session directory exists
if (!fs.existsSync(SESSION_DIR)) {
    fs.mkdirSync(SESSION_DIR, { recursive: true });
}

let sock;
let redisClient;

async function initRedis() {
    redisClient = new Redis(REDIS_URL);
    redisClient.on('error', (err) => console.log('[Redis] Client Error', err));
}

async function startSock() {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    
    // Fetch latest version or fallback
    const { version } = await fetchLatestWaWebVersion();
    console.log(`[Connection] Using latest WA version: ${version.join('.')}`);

    sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        logger,
        // Standard Browser Identity
        browser: Browsers.ubuntu('Chrome'),
        syncFullHistory: false,
        printQRInTerminal: true,
        connectTimeoutMs: 60000,
        defaultQueryTimeoutMs: 60000,
        keepAliveIntervalMs: 30000,
        markOnlineOnConnect: true,
    });

    // Handle Outbound
    const subscriber = new Redis(REDIS_URL);
    subscriber.subscribe(OUTBOUND_CHANNEL).catch(console.error);

    subscriber.on('message', async (channel, message) => {
        if (channel === OUTBOUND_CHANNEL) {
            try {
                const data = JSON.parse(message);
                await sock.sendMessage(data.to, { text: data.message });
            } catch (err) {
                console.error('[Outbound] Error:', err.message);
            }
        }
    });

    if (!sock.authState.creds.registered) {
        if (PHONE_NUMBER) {
            console.log(`[Pairing] Requesting code for ${PHONE_NUMBER}...`);
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(PHONE_NUMBER);
                    console.log('\n----------------------------');
                    console.log('PAIRING CODE:', code);
                    console.log('----------------------------\n');
                } catch (err) {
                    console.error('[Pairing] ERROR:', err.message);
                }
            }, 5000);
        }
    }

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        if (qr) {
            console.log('[Connection] QR CODE GENERATED');
        }
        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            if (shouldReconnect) setTimeout(startSock, 10000);
        } else if (connection === 'open') {
            console.log('[Connection] 🚀 SUCCESSFULLY CONNECTED');
        }
    });

    sock.ev.on('creds.update', saveCreds);

    // Handle Inbound Messages
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        // Log basic upsert info for debugging
        console.log(`[Upsert] Received ${messages.length} messages, type: ${type}`);
        
        if (type !== 'notify') return;
        
        for (const msg of messages) {
            // Ignore if no message content or if it's from ourselves
            if (!msg.message || msg.key.fromMe) continue;

            const text = msg.message.conversation || 
                         msg.message.extendedTextMessage?.text || 
                         msg.message.buttonsResponseMessage?.selectedButtonId ||
                         msg.message.listResponseMessage?.singleSelectReply?.selectedRowId;

            if (!text) {
                console.log(`[Upsert] No supported text content in message from ${msg.key.remoteJid}`);
                continue;
            }

            const payload = {
                from: msg.key.remoteJid,
                message: text,
                pushName: msg.pushName || 'User',
                timestamp: msg.messageTimestamp,
                messageId: msg.key.id
            };

            try {
                // Use the shared redisClient initialized in initRedis()
                await redisClient.publish(INBOUND_CHANNEL, JSON.stringify(payload));
                console.log(`[Inbound] 📩 Forwarded message from ${payload.pushName} (${payload.from}): ${text.substring(0, 50)}`);
            } catch (err) {
                console.error('[Inbound] ❌ Redis Publish Error:', err.message);
            }
        }
    });
}

initRedis().then(startSock).catch(console.error);
