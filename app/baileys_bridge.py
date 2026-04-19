import asyncio
import json
import logging
import time
from app.config import settings
from app.redis_client import redis_client
from app.webhook import (
    delayed_buffer_process, 
    hard_max_check, 
    background_tracker_log
)

logger = logging.getLogger(__name__)

class BaileysBridge:
    def __init__(self):
        self.channel = settings.WHATSAPP_INBOUND_CHANNEL
        self.is_running = False

    async def start(self):
        """Subscribe to the Redis inbound channel and process messages."""
        pubsub = redis_client.redis.pubsub()
        await pubsub.subscribe(self.channel)
        
        self.is_running = True
        logger.info(f"🚀 Baileys Bridge started. Listening on Redis channel: {self.channel}")

        try:
            while self.is_running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message:
                    await self._handle_raw_message(message)
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"[Baileys Bridge] 🚨 Error in listener loop: {e}", exc_info=True)
        finally:
            self.is_running = False
            await pubsub.unsubscribe(self.channel)

    async def stop(self):
        self.is_running = False

    async def _handle_raw_message(self, raw_message):
        """Parse and route the inbound message to the existing processing logic."""
        try:
            data = json.loads(raw_message["data"])
            
            sender_raw = data.get("from", "") # e.g. "447700900000@s.whatsapp.net"
            message_text = data.get("message", "")
            message_id = data.get("messageId", "")
            message_ts = int(data.get("timestamp", time.time()))

            if not sender_raw or not message_text:
                return

            # 1. Normalize phone to internal format "whatsapp:+447700900000"
            phone_id = sender_raw.split("@")[0]
            sender_phone = f"whatsapp:+{phone_id}"
            sender_name = data.get("pushName", "there")

            logger.info(f"[Baileys Bridge] 📩 Message from {sender_name} ({sender_phone}): {message_text[:50]}...")

            # 2. Dedup Check
            dedup_key = f"dedup:{message_id}"
            if await redis_client.redis.get(dedup_key):
                logger.info(f"[Baileys Bridge] Duplicate message {message_id}, ignoring")
                return
            await redis_client.redis.set(dedup_key, "1", ex=86400)

            # 3. Generation Cleanup & Setup
            client_id = data.get("sessionId")
            await redis_client.check_and_clear_stale_generation(sender_phone)

            # 4. Buffer the message
            batch_id, is_first = await redis_client.buffer_message(sender_phone, message_text)
            
            # Store metadata for processing
            await redis_client.redis.set(f"last_msg_id:{sender_phone}", message_id, ex=300)
            await redis_client.redis.set(f"last_name:{sender_phone}", sender_name, ex=300)
            if client_id:
                await redis_client.redis.set(f"client_id:{sender_phone}", client_id, ex=300)

            # 5. Fire delayed processor (reusing webhook logic)
            # Since we don't have BackgroundTasks here, we use create_task
            asyncio.create_task(delayed_buffer_process(sender_phone, batch_id, message_ts, client_id=client_id))

            # 6. Fire hard-max safety check
            if is_first:
                asyncio.create_task(hard_max_check(sender_phone, message_ts, client_id=client_id))

            # 7. Tracker Log
            asyncio.create_task(background_tracker_log(sender_phone, sender_name, message_text, client_id=client_id))

        except Exception as e:
            logger.error(f"[Baileys Bridge] ❌ Failed to handle message: {e}")

    # ─── OUTBOUND (Routing to Baileys Service) ───────────────────────────

    async def send_message(self, phone: str, text: str, client_id: Optional[str] = None):
        """Publish a text message to the Baileys outbound channel."""
        # phone format: "whatsapp:+44..." -> "44...@s.whatsapp.net"
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "message": text,
            "replyToMessageId": await redis_client.redis.get(f"last_msg_id:{phone}")
        }
        await redis_client.redis.publish("outbound", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}

    async def send_chunked_messages(self, phone: str, chunks: list, client_id: Optional[str] = None):
        """Send multiple messages with delays."""
        for idx, chunk in enumerate(chunks):
            await self.send_message(phone, chunk, client_id=client_id)
            if idx < len(chunks) - 1:
                await self.send_typing_indicator(phone, client_id=client_id)
                await asyncio.sleep(settings.CHUNK_DELAY_SECONDS)

    async def send_typing_indicator(self, phone: str, client_id: Optional[str] = None):
        """Show typing indicator in Baileys."""
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net"
        }
        await redis_client.redis.publish("outbound:typing", json.dumps(payload))
        return True

    async def mark_as_read(self, phone: str, message_id: str):
        """Mark a message as read in Baileys."""
        if not message_id: return False
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        payload = {
            "to": f"{phone_id}@s.whatsapp.net",
            "messageId": message_id
        }
        await redis_client.redis.publish("outbound:mark_read", json.dumps(payload))
        return True

    async def send_media(self, phone: str, media_url: str, media_type: str = "document", caption: str = "", client_id: Optional[str] = None):
        """Send a media file via Baileys."""
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "type": media_type,
            "url": media_url,
            "caption": caption
        }
        await redis_client.redis.publish("outbound:media", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}

    async def send_poll(self, phone: str, question: str, options: list, client_id: Optional[str] = None):
        """Send a poll via Baileys."""
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "question": question,
            "options": options
        }
        await redis_client.redis.publish("outbound:poll", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}

    async def forward_message(self, phone: str, original_msg_id: str, forward_to: str, client_id: Optional[str] = None):
        """Forward a message via Baileys."""
        phone_id = phone.split(':')[-1] if ':' in phone else phone
        target_id = forward_to.split(':')[-1] if ':' in forward_to else forward_to
        payload = {
            "sessionId": client_id,
            "to": f"{phone_id}@s.whatsapp.net",
            "forwardTo": f"{target_id}@s.whatsapp.net",
            "originalMessageKey": {
                "remoteJid": f"{phone_id}@s.whatsapp.net",
                "id": original_msg_id,
                "fromMe": False
            }
        }
        await redis_client.redis.publish("outbound:forward", json.dumps(payload))
        return {"status": "enqueued", "provider": "baileys"}

baileys_bridge = BaileysBridge()
