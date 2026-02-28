import httpx
import os
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

    async def call_llm(self, messages: List[Dict[str, str]], model: Optional[str] = None) -> str:
        """Calls OpenRouter API with fallback logic."""
        model = model or settings.OPENROUTER_PRIMARY_MODEL
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    self.base_url,
                    headers=self.headers,
                    json={
                        "model": model,
                        "messages": messages
                    }
                )
                response.raise_for_status()
                data = response.json()
                return data['choices'][0]['message']['content']
            except Exception as e:
                print(f"Error calling primary model {model}: {e}")
                if model != settings.OPENROUTER_FALLBACK_MODEL:
                    return await self.call_llm(messages, model=settings.OPENROUTER_FALLBACK_MODEL)
                raise e

    async def build_context(self, session: Dict[str, Any], lead_data: Dict[str, Any], message: str) -> List[Dict[str, str]]:
        """Builds the full LLM context."""
        prompt_path = os.path.join(os.getcwd(), "prompts", "system_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()

        # Replace placeholders
        replacements = {
            "{{lead_name}}": lead_data.get("name", "there"),
            "{{lead_company}}": lead_data.get("company", "your company"),
            "{{current_state}}": session.get("state", "opening"),
            "{{calendly_link}}": settings.CALENDLY_LINK,
            "{{bant_scores}}": str(session.get("bant_scores", {})),
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
