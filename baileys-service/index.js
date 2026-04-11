/*
 * REDIS CHANNELS:
 * 
 * INBOUND (Baileys → Python):
 *   "inbound"              - Text messages from leads
 *   "inbound:poll_response" - Poll answer from leads
 *   "inbound:media"        - Media messages from leads (with Supabase URL)
 * 
 * OUTBOUND (Python → Baileys):
 *   "outbound"             - Send text message (with auto typing & reactions)
 *   "outbound:media"       - Send image/PDF/voice note (ptt: true)
 *   "outbound:poll"        - Send poll question
 *   "outbound:edit"        - Edit sent message
 *   "outbound:delete"      - Delete sent message
 *   "outbound:forward"     - Forward message
 *   "outbound:contact"     - Send contact card
 *   "outbound:reaction"    - React to message with emoji
 * 
 * SYSTEM:
 *   "pairing_code"         - Pairing code for auth
 *   "connection_status"    - Connection state updates
 */

import pkg from '@whiskeysockets/baileys';
const { 
    default: makeWASocket, 
    useMultiFileAuthState, 
    DisconnectReason, 
    delay,
    jidNormalizedUser,
    downloadMediaMessage
} = pkg;
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
const MEDIA_TEMP_DIR = '/tmp/media';
const SALES_PHONE_NUMBER = process.env.SALES_PHONE_NUMBER;
const AUTH_MODE = process.env.AUTH_MODE || 'qr';
const PAIRING_PHONE_NUMBER = process.env.PAIRING_PHONE_NUMBER;

// Ensure directories exist
if (!fs.existsSync(SESSION_DIR)) fs.mkdirSync(SESSION_DIR, { recursive: true });
if (!fs.existsSync(MEDIA_TEMP_DIR)) fs.mkdirSync(MEDIA_TEMP_DIR, { recursive: true });

// Initialize Supabase
const supabase = createClient(
    process.env.SUPABASE_URL,
    process.env.SUPABASE_SERVICE_ROLE_KEY
);

const logger = pino({ level: 'info' });
console.log(`[Config] Connecting to Redis: ${REDIS_URL.split('@').pop()}`);
const redisPub = new Redis(REDIS_URL, { maxRetriesPerRequest: null });
const redisSub = new Redis(REDIS_URL, { maxRetriesPerRequest: null });
const redisSubControl = new Redis(REDIS_URL, { maxRetriesPerRequest: null });

async function connectToWhatsApp() {
    // TODO: Multi-session Baileys support - load all clients and start a session for each
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    const sock = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        browser: ['Markeye Mark', 'Chrome', '120.0'],
        printQRInTerminal: AUTH_MODE === 'qr'
    });

    // Request Pairing Code if needed
    if (AUTH_MODE === 'pairing' && !sock.authState.creds.registered) {
        if (!PAIRING_PHONE_NUMBER) {
            console.error('[Pairing] ERROR: PAIRING_PHONE_NUMBER is required for pairing mode');
        } else {
            setTimeout(async () => {
                try {
                    const code = await sock.requestPairingCode(PAIRING_PHONE_NUMBER);
                    console.log('----------------------------');
                    console.log(`PAIRING CODE: ${code}`);
                    console.log('----------------------------');
                    await redisPub.publish('pairing_code', JSON.stringify({ code, phone: PAIRING_PHONE_NUMBER }));
                } catch (err) {
                    console.error('[Pairing] Error requesting code:', err.message);
                }
            }, 5000);
        }
    }

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr && AUTH_MODE === 'qr') {
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect.error instanceof Boom) 
                ? lastDisconnect.error.output.statusCode !== DisconnectReason.loggedOut 
                : true;
            console.log('Connection closed. Reconnecting:', shouldReconnect);
            if (shouldReconnect) {
                connectToWhatsApp();
            }
        } else if (connection === 'open') {
            console.log('Mark is connected to WhatsApp');
        }
    });

    // Handle Inbound Messages
    sock.ev.on('messages.upsert', async ({ messages }) => {
        for (const msg of messages) {
            if (msg.key.fromMe) continue;

            const from = msg.key.remoteJid;
            const messageType = Object.keys(msg.message || {})[0];
            let text = msg.message?.conversation || 
                       msg.message?.extendedTextMessage?.text || 
                       '';
            let message_type = 'text';
            let mediaUrl = null;

            // Handle Media
            if (['imageMessage', 'documentMessage', 'audioMessage'].includes(messageType)) {
                try {
                    console.log(`[Media] Inbound ${messageType} from ${from}`);
                    const buffer = await downloadMediaMessage(msg, 'buffer', {});
                    const ext = mime.extension(msg.message[messageType].mimetype) || 'bin';
                    const fileName = `${msg.key.id}.${ext}`;
                    const filePath = path.join(MEDIA_TEMP_DIR, fileName);
                    
                    fs.writeFileSync(filePath, buffer);

                    // Upload to Supabase: media/{phone}/{messageId}.{ext}
                    const phone = from.split('@')[0];
                    const storagePath = `media/${phone}/${fileName}`;
                    
                    const { data, error } = await supabase.storage
                        .from('media')
                        .upload(storagePath, buffer, {
                            contentType: msg.message[messageType].mimetype,
                            upsert: true
                        });

                    if (error) throw error;

                    const { data: { publicUrl } } = supabase.storage
                        .from('media')
                        .getPublicUrl(storagePath);
                    
                    mediaUrl = publicUrl;
                    message_type = messageType.replace('Message', '');
                    if (!text) text = `[Sent ${message_type}]`;

                    // Cleanup
                    fs.unlinkSync(filePath);
                } catch (err) {
                    console.error('[Media Ingest Error]', err.message);
                }
            }

            if (!text && !mediaUrl) continue;

            console.log(`[Inbound] From: ${from}, Type: ${message_type}, Msg: ${text}`);

            // Publish to Redis "inbound" channel
            const payload = JSON.stringify({
                from,
                message: text,
                messageId: msg.key.id,
                timestamp: msg.messageTimestamp,
                pushName: msg.pushName || 'there',
                message_type,
                media_url: mediaUrl
            });
            await redisPub.publish('inbound', payload);
        }
    });

    // Handle Poll Responses
    sock.ev.on('messages.update', async (updates) => {
        for (const { key, update } of updates) {
            if (update.pollUpdates) {
                const pollUpdate = update.pollUpdates[0];
                if (pollUpdate) {
                    // Note: decryption in Baileys for poll is automatic in recent versions
                    // but we just need the selection index vs names. 
                    // For simplicity, we'll notify Python to check the state.
                    await redisPub.publish('inbound:poll_response', JSON.stringify({
                        from: key.remoteJid,
                        pollId: key.id,
                        voter: pollUpdate.voter,
                        timestamp: Date.now()
                    }));
                }
            }
        }
    });

    // --- Outbound Text ---
    redisSub.subscribe('outbound', (err) => {
        if (err) logger.error('Failed to subscribe to outbound: %s', err.message);
        else console.log('Subscribed to Redis "outbound" channel');
    });

    redisSub.on('message', async (channel, message) => {
        if (channel !== 'outbound') return;
        const { to, response, replyToMessageId } = JSON.parse(message);
        
        try {
            // Humant touch: 30% chance to react first
            if (replyToMessageId && Math.random() < 0.3) {
                await sock.sendMessage(to, { 
                    react: { text: '👍', key: { remoteJid: to, id: replyToMessageId, fromMe: false } } 
                });
                await delay(1000);
            }

            // Human simulation sequence
            await delay(1000);

            if (replyToMessageId) {
                await sock.readMessages([{ remoteJid: to, id: replyToMessageId }]);
            }

            await delay(1000);
            await sock.sendPresenceUpdate('composing', to);

            const typingDelay = Math.min(response.length * 30, 8000);
            await delay(typingDelay);

            // Rich Preview Detection
            const urlRegex = /(https?:\/\/[^\s]+)/g;
            const matchedUrls = response.match(urlRegex);
            const messageOptions = { text: response };
            
            if (matchedUrls) {
                // Attach link preview for the first URL
                messageOptions.linkPreview = {
                    "matchedText": matchedUrls[0],
                    "canonicalUrl": matchedUrls[0],
                    "title": "Markeye Discovery Call",
                    "description": "Book your 15-min strategy session"
                };
            }

            if (response.length > 500) {
                // Split by sentences and group into chunks under 500 chars (Reaction already happened)
                const sentences = response.match(/[^.!?]+[.!?]+(?:\s|$)/g) || [response];
                let currentChunk = "";

                for (let i = 0; i < sentences.length; i++) {
                    const sentence = sentences[i];
                    if ((currentChunk + sentence).length > 500 && currentChunk) {
                        await sock.sendMessage(to, { text: currentChunk.trim() });
                        await delay(1500);
                        await sock.sendPresenceUpdate('composing', to);
                        currentChunk = sentence;
                    } else {
                        currentChunk += sentence;
                    }
                }
                if (currentChunk) {
                    await sock.sendMessage(to, { text: currentChunk.trim() });
                }
            } else {
                await sock.sendMessage(to, messageOptions);
            }

            await sock.sendPresenceUpdate('paused', to);

        } catch (err) {
            logger.error('[Outbound Error] %s', err.message);
        }
    });

    // --- Control Channels ---
    const controlChannels = [
        'outbound:media', 
        'outbound:reaction', 
        'outbound:poll', 
        'outbound:edit', 
        'outbound:delete', 
        'outbound:forward', 
        'outbound:contact'
    ];
    
    controlChannels.forEach(ch => {
        redisSubControl.subscribe(ch, (err) => {
            if (!err) console.log(`Subscribed to Redis "${ch}" channel`);
        });
    });

    redisSubControl.on('message', async (channel, message) => {
        const data = JSON.parse(message);
        const { to } = data;

        try {
            if (channel === 'outbound:media') {
                const { type, url, caption } = data;
                console.log(`[Media] Sending ${type} to ${to}`);
                if (type === 'image') await sock.sendMessage(to, { image: { url }, caption });
                if (type === 'document') await sock.sendMessage(to, { document: { url }, mimetype: 'application/pdf', fileName: caption });
                if (type === 'audio') await sock.sendMessage(to, { audio: { url }, mimetype: 'audio/ogg; codecs=opus', ptt: true });
            }

            if (channel === 'outbound:reaction') {
                const { emoji, originalMessageKey } = data;
                await sock.sendMessage(to, { react: { text: emoji, key: originalMessageKey } });
            }

            if (channel === 'outbound:poll') {
                const { question, options } = data;
                console.log(`[Poll] Sending to ${to}: ${question}`);
                await sock.sendMessage(to, {
                    poll: {
                        name: question,
                        values: options,
                        selectableCount: 1
                    }
                });
            }

            if (channel === 'outbound:edit') {
                const { messageId, newText } = data;
                await sock.sendMessage(to, {
                    text: newText,
                    edit: { remoteJid: to, id: messageId, participant: undefined }
                });
            }

            if (channel === 'outbound:delete') {
                const { messageId } = data;
                await sock.sendMessage(to, {
                    delete: { remoteJid: to, id: messageId, participant: undefined }
                });
            }

            if (channel === 'outbound:forward') {
                const { originalMessageKey, forwardTo } = data;
                await sock.sendMessage(forwardTo, { forward: originalMessageKey });
            }

            if (channel === 'outbound:contact') {
                const { contactName, contactPhone } = data;
                const vcard = 'BEGIN:VCARD\n'
                    + 'VERSION:3.0\n'
                    + `FN:${contactName}\n`
                    + `TEL;type=CELL;type=VOICE;waid=${contactPhone.replace('+','')}:${contactPhone}\n`
                    + 'END:VCARD';
                await sock.sendMessage(to, {
                    contacts: {
                        displayName: contactName,
                        contacts: [{ vcard }]
                    }
                });
            }

        } catch (err) {
            logger.error(`[Control Error] Channel ${channel}: ${err.message}`);
        }
    });

    return sock;
}

connectToWhatsApp().catch(err => console.error('Unexpected error: ', err));
