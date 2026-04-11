import makeWASocket, { 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestWaWebVersion,
    makeCacheableSignalKeyStore
} from '@whiskeysockets/baileys';
import pino from 'pino';
import Redis from 'ioredis';
import fs from 'fs';

// Logger
const logger = pino({ level: 'debug' });

const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const SESSION_DIR = '/app/sessions';
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
    console.log('[Config] Redis initialized with ioredis.');
}

async function startSock() {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    
    let version;
    try {
        const result = await fetchLatestWaWebVersion();
        version = result.version;
        console.log(`[Connection] WA version: ${version.join('.')} (Latest: ${result.isLatest})`);
    } catch (err) {
        console.log('[Connection] Version fallback.');
        version = [2, 3000, 1015901307];
    }

    sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        logger: pino({ level: 'silent' }),
        browser: ["Ubuntu", "Chrome", "110.0.5563.147"],
        syncFullHistory: false,
        printQRInTerminal: false,
        connectTimeoutMs: 90000,
        defaultQueryTimeoutMs: 90000,
        keepAliveIntervalMs: 30000,
        retryRequestDelayMs: 10000,
        markOnlineOnConnect: true,
    });

    // Handle Outbound
    const subscriber = new Redis(REDIS_URL);
    subscriber.subscribe(OUTBOUND_CHANNEL, (err, count) => {
        if (err) console.error('[Redis] Subscribe error:', err.message);
    });

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
            console.log(`[Pairing] Requesting code for ${PHONE_NUMBER}... (Wait 15s)`);
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(PHONE_NUMBER);
                    console.log('\n----------------------------');
                    console.log('PAIRING CODE:', code);
                    console.log('----------------------------\n');
                } catch (err) {
                    console.error('[Pairing] ERROR REASON:', err.message);
                }
            }, 15000);
        }
    }

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect } = update;
        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            if (shouldReconnect) setTimeout(startSock, 10000);
        } else if (connection === 'open') {
            console.log('[Connection] 🚀 SUCCESSFULLY CONNECTED');
        }
    });

    sock.ev.on('messages.upsert', async (m) => {
        if (m.type === 'notify') {
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    const content = msg.message.conversation || msg.message.extendedTextMessage?.text;
                    if (!content) continue;
                    const payload = {
                        from: msg.key.remoteJid,
                        pushName: msg.pushName || 'User',
                        message: content,
                        messageId: msg.key.id,
                        timestamp: msg.messageTimestamp,
                    };
                    await redisClient.publish(INBOUND_CHANNEL, JSON.stringify(payload));
                }
            }
        }
    });

    sock.ev.on('creds.update', saveCreds);
}

initRedis().then(startSock).catch(console.error);
