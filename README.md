# After5 WhatsApp AI Agent

This is a complete WhatsApp AI sales agent project for After5 Digital.

## Features
1. Receives incoming WhatsApp messages via Twilio webhook.
2. Manages conversation state (Opening → Discovery → Qualification → Booking → Escalation).
3. Calls an LLM via OpenRouter to generate natural responses.
4. Sends replies back through Twilio.
5. Extracts BANT scores (Budget, Authority, Need, Timeline) in the background.
6. Tracks everything in Supabase.
7. Uses Redis for session state and conversation memory.
8. Supports message chunking (splitting long replies into multiple WhatsApp messages with delays).
9. Supports input buffering (waits for user to stop typing before replying).
10. Fires typing indicator before every reply.

## Tech Stack
- Python 3.11
- FastAPI (async)
- Twilio WhatsApp API
- OpenRouter for LLM calls (supports Claude, GPT-4o, Gemini, etc.)
- Redis for session state, conversation history, dedup, and input buffering
- Supabase for persistent storage (leads, messages, status)
- Helicone for LLM observability (optional proxy)
- Deployed on Railway

## Quick Start
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`.
3. Set your environment variables (see `.env.example`).
4. Run the app: `uvicorn main:app --reload`.

## Environment Variables
Check `.env.example` for the full list of required variables.

## API Endpoints
- `GET /`: Health check.
- `POST /webhook`: Twilio webhook handler.
- `POST /send-outbound`: Outbound message sender.
- `POST /form-webhook`: For n8n/website form submissions.

## Architecture Overview
1.  **Webhook**: Incoming message is received.
2.  **Buffering**: Input is buffered in Redis to handle multiple messages.
3.  **Processing**: Conversation engine is triggered after buffer timeout.
4.  **LLM Call**: Context is built and OpenRouter is called.
5.  **Chunking**: Response is split into multiple messages if needed.
6.  **Delivery**: Messages are sent via Twilio with typing indicators.
7.  **BANT Extraction**: Qualification signals are extracted in the background.
8.  **Logging**: Everything is saved to Supabase for persistent tracking.
