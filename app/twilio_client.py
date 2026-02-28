import asyncio
from twilio.rest import Client
from app.config import settings
from app.chunker import calculate_typing_delay
from typing import List

class TwilioClient:
    def __init__(self):
        self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    def send_message(self, to_phone: str, body: str):
        """Sends WhatsApp message via Twilio."""
        message = self.client.messages.create(
            from_=settings.TWILIO_WHATSAPP_NUMBER,
            body=body,
            to=to_phone
        )
        return message.sid

    async def send_typing_indicator(self, to_phone: str):
        """
        Fires typing indicator. 
        Note: Twilio doesn't have a native typing indicator API for WhatsApp yet,
        so we simulate by adding a small delay.
        """
        # In a real scenario, some providers support a 'typing' payload.
        # For now, we just log it as a simulation precursor.
        pass

    async def send_chunked_messages(self, to_phone: str, chunks: List[str]):
        """Sends multiple messages with realistic delays between them."""
        for chunk in chunks:
            # Simulate typing delay
            delay = calculate_typing_delay(chunk)
            await asyncio.sleep(delay)
            
            self.send_message(to_phone, chunk)
            
            # Additional break between chunks
            await asyncio.sleep(settings.CHUNK_DELAY_SECONDS)

twilio_client = TwilioClient()
