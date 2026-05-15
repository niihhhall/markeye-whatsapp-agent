import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class WhatsAppCloudClient:
    def __init__(self, token: str, phone_number_id: str, version: str = "v19.0"):
        self.token = token
        self.phone_number_id = phone_number_id
        self.base_url = f"https://graph.facebook.com/{version}/{phone_number_id}"
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def send_text(self, to: str, message: str) -> Dict[str, Any]:
        """Send a standard text message."""
        url = f"{self.base_url}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": message}
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"WhatsApp Cloud API Error: {e.response.text}")
                raise

    async def mark_as_read(self, message_id: str) -> bool:
        """Mark a message as read (triggers the blue check)."""
        url = f"{self.base_url}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, headers=self.headers)
            return res.status_code == 200

    async def send_typing_on(self, to: str):
        """
        Simulate human behavior. 
        Note: Official Cloud API support for 'typing' bubbles varies by account type.
        """
        # Place holder for future official typing support or internal state management
        pass
