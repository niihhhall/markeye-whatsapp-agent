const { 
    default: makeWASocket, 
    useMultiFileAuthState, 
    DisconnectReason, 
    fetchLatestWaWebVersion,
    makeCacheableSignalKeyStore,
    PHONENUMBER_MCC
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const redis = require('redis');
const fs = require('fs');
const path = require('path');

// Logger - Silent for production but readable for errors
const logger = pino({ level: 'info' }); // Switch to info for debug during stabilization

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
    redisClient = redis.createClient({ url: REDIS_URL });
    redisClient.on('error', (err) => console.log('[Redis] Client Error', err));
    await redisClient.connect();
    console.log('[Config] Redis:', REDIS_URL.split('@').pop());
}

async function startSock() {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    
    // Fetch latest version or fallback
    let version;
    try {
        const result = await fetchLatestWaWebVersion();
        version = result.version;
        console.log(`[Connection] WA version: ${version.join('.')} (Latest: ${result.isLatest})`);
    } catch (err) {
        console.log('[Connection] Version fetch failed, using fallback.');
        version = [2, 3000, 1015901307];
    }

    sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            /** caching makes the store faster to send/receive messages */
            keys: makeCacheableSignalKeyStore(state.keys, logger),
        },
        logger: pino({ level: 'silent' }),
        browser: ["Markeye Agent", "Chrome", "1.0.0"],
        syncFullHistory: false,
        printQRInTerminal: false,
        
        // Stabilizing Timeouts for $6 droplet
        connectTimeoutMs: 60000,       // 60s timeout for handshake
        defaultQueryTimeoutMs: 60000,  // 60s for server queries
        keepAliveIntervalMs: 30000,
        retryRequestDelayMs: 5000 + Math.random() * 2000, // Jittered retries
        
        // Mobile pairing
        markOnlineOnConnect: true,
    });

    // 1. Handle Outbound Messages
    const subscriber = redisClient.duplicate();
    await subscriber.connect();
    await subscriber.subscribe(OUTBOUND_CHANNEL, async (message) => {
        try {
            const data = JSON.parse(message);
            console.log(`[Outbound] Sending to ${data.to}: ${data.message.substring(0, 50)}...`);
            
            await sock.sendMessage(data.to, { text: data.message });
        } catch (err) {
            console.error('[Outbound] Error:', err.message);
        }
    });
    console.log(`Subscribed to "${OUTBOUND_CHANNEL}"`);

    // 2. Pair with code if not authenticated
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
            }, 5000); // Wait 5s for socket to be ready
        } else {
            console.log('[Pairing] No PAIRING_PHONE_NUMBER found in .env');
        }
    }

    // 3. Connection Events
    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr && !PHONE_NUMBER) {
            console.log('[Connection] QR Code generated (Scan or set PAIRING_PHONE_NUMBER)');
        }

        if (connection === 'close') {
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            
            console.log(`[Connection] Closed. Reason: ${statusCode} (Status: ${shouldReconnect ? 'Reconnecting' : 'Logged out'})`);
            
            if (shouldReconnect) {
                const delay = statusCode === 503 ? 15000 : 10000;
                console.log(`[Connection] Reconnecting in ${delay/1000}s...`);
                setTimeout(startSock, delay);
            } else {
                console.log('[Connection] Logged out. Manual intervention required.');
            }
        } else if (connection === 'open') {
            console.log('[Connection] 🚀 SUCCESSFULLY CONNECTED');
        }
    });

    // 4. Inbound Messages
    sock.ev.on('messages.upsert', async (m) => {
        if (m.type === 'notify') {
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    const content = msg.message.conversation || 
                                  msg.message.extendedTextMessage?.text || 
                                  (msg.message.audioMessage ? "[Audio]" : "");
                    
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

initRedis().then(startSock);
