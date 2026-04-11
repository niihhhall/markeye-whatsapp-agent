import makeWASocket, { useMultiFileAuthState, fetchLatestWaWebVersion } from 'baileys';
import pino from 'pino';

async function start() {
    const { state, saveCreds } = await useMultiFileAuthState('./test_auth');
    const { version, isLatest } = await fetchLatestWaWebVersion({});
    console.log(`using WA v${version.join('.')}, isLatest: ${isLatest}`);

    const sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'trace' }),
        browser: ['Windows', 'Chrome', '120.0.0']
    });

    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', (update) => {
        console.log('Connection update:', update);
    });

    if (!sock.authState.creds.registered) {
        setTimeout(async () => {
            try {
                const code = await sock.requestPairingCode("918141253252");
                console.log(`PAIRING CODE: ${code}`);
            } catch (err) {
                console.error('Pairing error', err);
            }
        }, 3000);
    }
}

start();
