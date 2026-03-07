import httpx
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.config import settings

class LLMClient:
    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://after5.digital",
            "X-Title": "After5 Agent"
        }
        
        if settings.HELICONE_API_KEY:
            self.base_url = "https://openrouter.helicone.ai/api/v1/chat/completions"
            self.headers["Helicone-Auth"] = f"Bearer {settings.HELICONE_API_KEY}"

    async def call_llm(
        self, 
        messages: List[Dict[str, str]], 
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        session_path: Optional[str] = None,
        session_name: Optional[str] = None,
        user_id: Optional[str] = None,
        properties: Optional[Dict[str, str]] = None,
        cache_enabled: bool = False
    ) -> str:
        """Calls OpenRouter API with fallback logic and Helicone metrics/caching."""
        model = model or settings.OPENROUTER_PRIMARY_MODEL
        
        headers = self.headers.copy()
        if settings.HELICONE_API_KEY:
            if session_id:
                headers["Helicone-Session-Id"] = session_id
            if session_path:
                headers["Helicone-Session-Path"] = session_path
            if session_name:
                headers["Helicone-Session-Name"] = session_name
            if user_id:
                headers["Helicone-User-Id"] = user_id
            if properties:
                for key, value in properties.items():
                    headers[f"Helicone-Property-{key}"] = str(value)
            if cache_enabled:
                headers["Helicone-Cache-Enabled"] = "true"
                headers["Cache-Control"] = "max-age=3600"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages
                    }
                )
                response.raise_for_status()
                print(f"[LLM] Successfully called model: {model}")
                data = response.json()
                return data['choices'][0]['message']['content']
            except Exception as e:
                print(f"Error calling primary model {model}: {e}")
                if model != settings.OPENROUTER_FALLBACK_MODEL:
                    return await self.call_llm(
                        messages, 
                        model=settings.OPENROUTER_FALLBACK_MODEL,
                        session_id=session_id,
                        session_path=session_path,
                        session_name=session_name,
                        user_id=user_id,
                        properties=properties,
                        cache_enabled=cache_enabled
                    )
                raise e

    async def build_context(self, session: Dict[str, Any], lead_data: Dict[str, Any], message: str) -> List[Dict[str, str]]:
        """Builds the full LLM context."""
        prompt_path = os.path.join(os.getcwd(), "prompts", "system_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()

        # Replace placeholders
        current_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # Format conversation history for the system prompt placeholder
        history = session.get("history", [])
        if history:
            history_lines = []
            for msg in history:
                role_label = "Lead" if msg["role"] == "user" else "Albert"
                history_lines.append(f"{role_label}: {msg['content']}")
            formatted_history = "\n".join(history_lines)
        else:
            formatted_history = "(no conversation yet)"

        replacements = {
            "{{lead_name}}": lead_data.get("name", "there"),
            "{{lead_company}}": lead_data.get("company", "your company"),
            "{{current_state}}": session.get("state", "opening"),
            "{{calendly_link}}": settings.CALENDLY_LINK,
            "{{bant_scores}}": str(session.get("bant_scores", {})),
            "{{current_datetime}}": current_datetime,
            "{{conversation_history}}": formatted_history,
        }
        
        for key, value in replacements.items():
            system_prompt = system_prompt.replace(key, str(value))

        messages = [{"role": "system", "content": system_prompt}]
        
        # Add history
        for msg in session.get("history", []):
            messages.append(msg)
            
        # Add current message
        messages.append({"role": "user", "content": message})
        
        return messages

llm_client = LLMClient()
