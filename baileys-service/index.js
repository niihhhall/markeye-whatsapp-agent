import makeWASocket, { 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestWaWebVersion,
    makeCacheableSignalKeyStore
} from '@whiskeysockets/baileys';
import pino from 'pino';
import { createClient } from 'redis';
import fs from 'fs';

// Logger
const logger = pino({ level: 'info' });

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
    redisClient = createClient({ url: REDIS_URL });
    redisClient.on('error', (err) => console.log('[Redis] Client Error', err));
    await redisClient.connect();
    console.log('[Config] Redis connected.');
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
        browser: ["Markeye Agent", "Chrome", "1.0.0"],
        syncFullHistory: false,
        printQRInTerminal: false,
        connectTimeoutMs: 60000,
        defaultQueryTimeoutMs: 60000,
        keepAliveIntervalMs: 30000,
        retryRequestDelayMs: 5000 + Math.random() * 2000,
        markOnlineOnConnect: true,
    });

    const subscriber = redisClient.duplicate();
    await subscriber.connect();
    await subscriber.subscribe(OUTBOUND_CHANNEL, async (message) => {
        try {
            const data = JSON.parse(message);
            await sock.sendMessage(data.to, { text: data.message });
        } catch (err) {
            console.error('[Outbound] Error:', err.message);
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
                    console.error('[Pairing] Error:', err.message);
                }
            }, 5000);
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
