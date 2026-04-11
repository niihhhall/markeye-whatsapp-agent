/*
 * REDIS CHANNELS:
 * 
 * INBOUND (Baileys → Python):
 *   "inbound"              - Text messages from leads
 *   "inbound:poll_response" - Poll answer from leads
 *   "inbound:media"        - Media messages from leads (with Supabase URL)
 */

import makeWASocket, { 
    useMultiFileAuthState, 
    DisconnectReason, 
    delay,
    downloadMediaMessage,
    jidNormalizedUser,
    fetchLatestWaWebVersion,
    Browsers
} from 'baileys';
import pino from 'pino';
import Redis from 'ioredis';
import qrcode from 'qrcode-terminal';
import { Boom } from '@hapi/boom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';
import { createClient } from '@supabase/supabase-js';
import mime from 'mime-types';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load environment variables from the project root
dotenv.config({ path: path.join(__dirname, '../.env') });

// Configuration
const REDIS_URL = process.env.REDIS_URL || 'redis://localhost:6379';
const SESSION_DIR = path.join(__dirname, 'sessions', 'default');
const MEDIA_TEMP_DIR = path.join(__dirname, 'temp_media');
const AUTH_MODE = process.env.AUTH_MODE || 'qr';
const PAIRING_PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER;

// Ensure directories exist
if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });
if (!fs.existsSync(MEDIA_TEMP_DIR)) fs.mkdirSync(MEDIA_TEMP_DIR, { recursive: true });

// Initialize Supabase
const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_KEY
);

const logger = pino({ level: 'info' });
console.log(`[Config] Redis: ${REDIS_URL.split('@').pop()}`);

const redisPub = new Redis(REDIS_URL, { maxRetriesPerRequest: null });
const redisSub = new Redis(REDIS_URL, { maxRetriesPerRequest: null });
const redisSubControl = new Redis(REDIS_URL, { maxRetriesPerRequest: null });

let sock = null;

async function connectToWhatsApp() {
    console.log('[Connection] Fetching latest WhatsApp version...');
    const { version, isLatest } = await fetchLatestWaWebVersion({});
    console.log(`[Connection] WA version: ${version.join('.')} (Latest: ${isLatest})`);

    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    sock = makeWASocket({
        version,
        auth: state,
        logger: pino({ level: 'silent' }),
        browser: Browsers.windows('Desktop')
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        // ALWAYS print QR Code if it's emitted, regardless of AUTH_MODE
        if (qr) {
            console.log('\n--- NEW QR CODE RECEIVED ---');
            qrcode.generate(qr, { small: true });
            console.log('You can scan the QR code above OR use the Pairing Code below.\n');
        }

        if (connection === 'close') {
            const statusCode = (lastDisconnect?.error instanceof Boom) 
                ? lastDisconnect.error.output.statusCode 
                : lastDisconnect?.error?.statusCode;
            
            const message = lastDisconnect?.error?.message || 'Unknown error';
            console.log(`[Connection] Closed. Reason: ${statusCode} (${message})`);

            if (statusCode === DisconnectReason.loggedOut || statusCode === 401) {
                console.log('[Connection] Unauthorized/Logged Out. Clearing session for fresh restart...');
                try {
                    fs.rmSync(SESSION_DIR, { recursive: true, force: true });
                } catch (e) {}
                setTimeout(connectToWhatsApp, 8000);
            } else {
                console.log('[Connection] Reconnecting in 5s...');
                setTimeout(connectToWhatsApp, 5000);
            }
        } else if (connection === 'open') {
            console.log('[Connection] SUCCESSFULLY CONNECTED');
            redisPub.publish('connection_status', JSON.stringify({ status: 'connected' }));
        }
    });

    sock.ev.on('messages.upsert', async ({ messages }) => {
        for (const msg of messages) {
            if (msg.key.fromMe) continue;
            const from = msg.key.remoteJid;
            const text = msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
            if (!text) continue;

            console.log(`[Inbound] From: ${from}, Msg: ${text}`);
            await redisPub.publish('inbound', JSON.stringify({
                from,
                message: text,
                messageId: msg.key.id,
                timestamp: msg.messageTimestamp,
                pushName: msg.pushName || 'there'
            }));
        }
    });

    // ALWAYS request Pairing Code if a number is configured
    if (PAIRING_PHONE_NUMBER && !sock.authState.creds.registered) {
        setTimeout(async () => {
            try {
                const code = await sock.requestPairingCode(PAIRING_PHONE_NUMBER);
                console.log('\n----------------------------');
                console.log(`PAIRING CODE: ${code}`);
                console.log('----------------------------\n');
                await redisPub.publish('pairing_code', JSON.stringify({ code, phone: PAIRING_PHONE_NUMBER }));
            } catch (err) {
                console.error('[Pairing] Error:', err.message);
            }
        }, 3000);
    }
}


function setupRedisListeners() {
    redisSub.subscribe('outbound', (err) => {
        if (!err) console.log('Subscribed to "outbound"');
    });

    redisSub.on('message', async (channel, message) => {
        if (channel === 'outbound' && sock) {
            const { to, response } = JSON.parse(message);
            try {
                await sock.sendPresenceUpdate('composing', to);
                await delay(1000 + (response.length * 20));
                await sock.sendMessage(to, { text: response });
                await sock.sendPresenceUpdate('paused', to);
            } catch (err) {
                console.error('[Outbound Error]', err.message);
            }
        }
    });
}

setupRedisListeners();
connectToWhatsApp().catch(err => console.error('Bootstrap Error:', err));
