import 'dotenv/config';
import express from 'express';
import makeWASocket, {
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestWaWebVersion,
    makeCacheableSignalKeyStore,
    Browsers,
    proto,
    delay
} from '@whiskeysockets/baileys';
import pino from 'pino';
import Redis from 'ioredis';
import fs from 'fs';
import path from 'path';

// ─────────────────────────────────────────────────────────────────────────────
// CONFIG & INITIALIZATION
// ─────────────────────────────────────────────────────────────────────────────

const logger = pino({ level: process.env.LOG_LEVEL || 'info' });
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const PORT = process.env.PORT || 3001;

const INBOUND_CHANNEL = process.env.WHATSAPP_INBOUND_CHANNEL || 'inbound';
const OUTBOUND_CHANNEL_PREFIX = 'outbound';

// Main Redis clients
const redis = new Redis(REDIS_URL);
const subscriber = new Redis(REDIS_URL);

// ─────────────────────────────────────────────────────────────────────────────
// REDIS AUTH STATE IMPLEMENTATION
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Custom Baileys auth state that persists to Redis.
 * Pattern: baileys:auth:{sessionId}:creds -> stringified JSON
 *          baileys:auth:{sessionId}:keys:{type} -> individual keys
 */
async function useRedisAuthState(sessionId) {
    const credsKey = `baileys:auth:${sessionId}:creds`;
    const keysPrefix = `baileys:auth:${sessionId}:keys:`;

    const writeData = async (data, key) => {
        await redis.set(key, JSON.stringify(data, (k, v) => (Buffer.isBuffer(v) ? v.toString('base64') : v)));
    };

    const readData = async (key) => {
        try {
            const data = await redis.get(key);
            if (!data) return null;
            return JSON.parse(data, (k, v) => (v && typeof v === 'object' && v.type === 'Buffer' ? Buffer.from(v.data) : (typeof v === 'string' && /^[A-Za-z0-9+/=]+$/.test(v) && v.length > 20 ? Buffer.from(v, 'base64') : v)));
        } catch (e) {
            return null;
        }
    };

    const removeData = async (key) => {
        await redis.del(key);
    };

    // Initialize creds
    let creds = await readData(credsKey);
    if (!creds) {
        creds = (await useMultiFileAuthState('./temp')).state.creds; // Generator
        await writeData(creds, credsKey);
    }

    return {
        state: {
            creds,
            keys: {
                get: async (type, ids) => {
                    const data = {};
                    await Promise.all(
                        ids.map(async (id) => {
                            let value = await readData(`${keysPrefix}${type}:${id}`);
                            if (type === 'app-state-sync-key' && value) {
                                value = proto.Message.AppStateSyncKeyData.fromObject(value);
                            }
                            data[id] = value;
                        })
                    );
                    return data;
                },
                set: async (data) => {
                    const tasks = [];
                    for (const type in data) {
                        for (const id in data[type]) {
                            const value = data[type][id];
                            const key = `${keysPrefix}${type}:${id}`;
                            tasks.push(value ? writeData(value, key) : removeData(key));
                        }
                    }
                    await Promise.all(tasks);
                },
            },
        },
        saveCreds: async () => {
            await writeData(creds, credsKey);
        },
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// SESSION MANAGER
// ─────────────────────────────────────────────────────────────────────────────

class SessionManager {
    constructor() {
        this.sessions = new Map(); // sessionId -> { sock, status, qr }
    }

    async initSession(sessionId, pairingPhone = null) {
        if (this.sessions.has(sessionId)) {
            logger.info(`[SessionManager] Session ${sessionId} already exists. Skipping.`);
            return;
        }

        logger.info(`[SessionManager] Initializing session: ${sessionId}`);
        const { state, saveCreds } = await useRedisAuthState(sessionId);
        const { version } = await fetchLatestWaWebVersion();

        const sock = makeWASocket({
            version,
            printQRInTerminal: false,
            auth: {
                creds: state.creds,
                keys: makeCacheableSignalKeyStore(state.keys, logger),
            },
            logger,
            browser: Browsers.ubuntu('Chrome'),
            syncFullHistory: false,
            markOnlineOnConnect: true,
        });

        this.sessions.set(sessionId, { sock, status: 'connecting', qr: null });

        // Pairing Code Support
        if (!sock.authState.creds.registered && pairingPhone) {
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(pairingPhone);
                    logger.info(`[SessionManager] Pairing Code for ${sessionId}: ${code}`);
                    // Save pairing code to redis for dashboard visibility
                    await redis.set(`baileys:pairing_code:${sessionId}`, code, 'EX', 300);
                } catch (err) {
                    logger.error(`[SessionManager] Pairing fail for ${sessionId}: ${err.message}`);
                }
            }, 5000);
        }

        // --- Event Listeners ---

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;
            const sessionData = this.sessions.get(sessionId);

            if (qr) {
                logger.info(`[SessionManager] QR Generated for ${sessionId}`);
                sessionData.qr = qr;
                await redis.set(`baileys:qr:${sessionId}`, qr, 'EX', 60);
            }

            if (connection === 'close') {
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
                sessionData.status = 'disconnected';
                
                if (shouldReconnect) {
                    logger.info(`[SessionManager] Session ${sessionId} disconnected. Reconnecting...`);
                    this.sessions.delete(sessionId);
                    setTimeout(() => this.initSession(sessionId), 5000);
                } else {
                    logger.info(`[SessionManager] Session ${sessionId} logged out.`);
                    this.sessions.delete(sessionId);
                    await this.cleanupSession(sessionId);
                }
            } else if (connection === 'open') {
                logger.info(`[SessionManager] 🚀 Session ${sessionId} CONNECTED`);
                sessionData.status = 'connected';
                sessionData.qr = null;
                await redis.set(`baileys:session_active:${sessionId}`, 'true');
                await redis.sadd('baileys:active_sessions', sessionId);
            }
        });

        sock.ev.on('messages.upsert', async ({ messages, type }) => {
            if (type !== 'notify') return;

            for (const msg of messages) {
                if (!msg.message || msg.key.fromMe) continue;

                const text = msg.message.conversation || 
                             msg.message.extendedTextMessage?.text || 
                             msg.message.buttonsResponseMessage?.selectedButtonId ||
                             msg.message.listResponseMessage?.singleSelectReply?.selectedRowId;

                if (!text) continue;

                const payload = {
                    sessionId: sessionId, // CRITICAL: Tag with sessionId
                    from: msg.key.remoteJid,
                    message: text,
                    pushName: msg.pushName || 'User',
                    timestamp: msg.messageTimestamp,
                    messageId: msg.key.id
                };

                await redis.publish(INBOUND_CHANNEL, JSON.stringify(payload));
                logger.debug(`[Inbound] ${sessionId} <- ${payload.from}: ${text.substring(0, 30)}`);
            }
        });
    }

    async cleanupSession(sessionId) {
        await redis.del(`baileys:auth:${sessionId}:creds`);
        const keys = await redis.keys(`baileys:auth:${sessionId}:keys:*`);
        if (keys.length) await redis.del(...keys);
        await redis.del(`baileys:session_active:${sessionId}`);
        await redis.srem('baileys:active_sessions', sessionId);
    }

    async stopSession(sessionId) {
        const session = this.sessions.get(sessionId);
        if (session && session.sock) {
            await session.sock.logout();
            session.sock.ws.close();
        }
        this.sessions.delete(sessionId);
    }
}

const manager = new SessionManager();

// ─────────────────────────────────────────────────────────────────────────────
// OUTBOUND ROUTING (Redis Pub/Sub)
// ─────────────────────────────────────────────────────────────────────────────

subscriber.psubscribe('outbound*').catch(err => logger.error('[RedisSub] Error:', err));

subscriber.on('pmessage', async (pattern, channel, message) => {
    try {
        const data = JSON.parse(message);
        const sessionId = data.sessionId; // Resolve session from payload

        if (!sessionId) {
            logger.warn(`[Outbound] Received message on ${channel} with no sessionId. Ignoring.`);
            return;
        }

        const session = manager.sessions.get(sessionId);
        if (!session || session.status !== 'connected') {
            logger.warn(`[Outbound] Session ${sessionId} not active. Status: ${session?.status || 'N/A'}`);
            return;
        }

        const sock = session.sock;

        if (channel === 'outbound') {
            await sock.sendMessage(data.to, { text: data.message });
        } else if (channel === 'outbound:media') {
            const mediaType = data.type === 'document' ? 'document' : data.type === 'image' ? 'image' : 'audio';
            const payload = {};
            payload[mediaType] = { url: data.url };
            if (data.type === 'document') payload.mimetype = 'application/pdf';
            if (data.caption) payload.caption = data.caption;
            await sock.sendMessage(data.to, payload);
        } else if (channel === 'outbound:poll') {
            await sock.sendMessage(data.to, { poll: { name: data.question, values: data.options, selectableCount: 1 } });
        } else if (channel === 'outbound:mark_read') {
            await sock.readMessages([{ remoteJid: data.to, id: data.messageId }]);
        } else if (channel === 'outbound:typing') {
            await sock.sendPresenceUpdate('composing', data.to);
        }
    } catch (err) {
        logger.error(`[Outbound] Error routing message:`, err.message);
    }
});

// ─────────────────────────────────────────────────────────────────────────────
// CONTROL API (Express)
// ─────────────────────────────────────────────────────────────────────────────

const app = express();
app.use(express.json());

app.post('/sessions/start', async (req, res) => {
    const { sessionId, phoneNumber } = req.body;
    if (!sessionId) return res.status(400).json({ error: 'sessionId required' });

    await manager.initSession(sessionId, phoneNumber);
    res.json({ status: 'initializing', sessionId });
});

app.get('/sessions/status', async (req, res) => {
    const statuses = [];
    for (const [id, data] of manager.sessions.entries()) {
        statuses.push({ sessionId: id, status: data.status, hasQR: !!data.qr });
    }
    res.json(statuses);
});

app.get('/sessions/:id/qr', async (req, res) => {
    const session = manager.sessions.get(req.params.id);
    if (!session) return res.status(404).json({ error: 'Session not found' });
    res.json({ qr: session.qr });
});

app.delete('/sessions/:id', async (req, res) => {
    await manager.stopSession(req.params.id);
    res.json({ status: 'stopped', sessionId: req.params.id });
});

app.listen(PORT, () => {
    logger.info(`[BaileysAPI] Server running on port ${PORT}`);
});

// ─────────────────────────────────────────────────────────────────────────────
// COLD START — Recover active sessions
// ─────────────────────────────────────────────────────────────────────────────

async function coldStart() {
    // ONE-TIME EMERGENCY WIPE (Remove this after successful link)
    const SESSION_ID = "eb89a504-7a6d-453f-89cd-3c95ed2a22f1";
    logger.info(`[ColdStart] Wiping session ${SESSION_ID} to clear connection loop...`);
    await redis.del(`baileys:auth:${SESSION_ID}:creds`);
    const keys = await redis.keys(`baileys:auth:${SESSION_ID}:keys:*`);
    if (keys.length) await redis.del(...keys);
    await redis.del(`baileys:session_active:${SESSION_ID}`);
    await redis.srem('baileys:active_sessions', SESSION_ID);

    const active = await redis.smembers('baileys:active_sessions');
    logger.info(`[ColdStart] Found ${active.length} active sessions in Redis to recover.`);
    for (const sid of active) {
        manager.initSession(sid).catch(e => logger.error(`[ColdStart] Fail for ${sid}:`, e));
    }
}

coldStart();
