# Markeye Baileys WhatsApp Service

This is the messaging sidecar for the Markeye agent. It handles direct WhatsApp connectivity using the Baileys library and bridges communication with the Python backend via Redis.

## Features
- Direct WhatsApp connection (No Cloud API required)
- Human-mimetic typing simulation & chunking
- Media & Reaction support
- Redis Pub/Sub integration

## Quick Setup
1. **Navigate to the directory**:
   ```bash
   cd baileys-service
   ```
2. **Install dependencies**:
   ```bash
   npm install
   ```
3. **Configure Environment**:
   Ensure `REDIS_URL` is set in the project root `.env` file.
4. **Run the service**:
   ```bash
   npm start
   ```
5. **Authenticate**:
   Scan the QR code that appears in the terminal with your mobile WhatsApp app.

## Channels used:
- `inbound`: Agent receives messages from users
- `outbound`: Agent sends text messages to users
- `outbound:media`: Agent sends images/documents/audio
- `outbound:reaction`: Agent reacts to messages
