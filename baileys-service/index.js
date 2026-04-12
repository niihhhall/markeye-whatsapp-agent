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
}

async function startSock() {
    const { state, saveCreds } = await useMultiFileAuthState(ABS_SESSION_DIR);
    
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
}

initRedis().then(startSock).catch(console.error);
