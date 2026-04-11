import WebSocket from 'ws';

const ws = new WebSocket('wss://web.whatsapp.com/ws/chat');

ws.on('open', () => {
    console.log('SUCCESS: Connected to WhatsApp WebSocket');
    ws.close();
});

ws.on('error', (err) => {
    console.error('FAILURE: Could not connect to WhatsApp WebSocket:', err.message);
});

ws.on('close', () => {
    console.log('Connection closed');
});
