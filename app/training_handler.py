import json
import re
import logging
from datetime import datetime
from app.config import settings
from app.supabase_client import supabase_client

logger = logging.getLogger(__name__)

VALID_CATEGORIES = [
    "tone", "objection", "sales", "qna", "greeting", 
    "closing", "followup", "personality", "voice", 
    "context", "escalation", "recovery"
]

class TrainingHandler:
    """Handles all /train commands for Albert live training."""
    
    def __init__(self):
        self.training_sessions = {}  # phone -> session state
        self.admin_numbers = settings.ADMIN_NUMBERS
    
    def is_admin(self, phone: str) -> bool:
        """Check if the phone number is in the authorized admin list."""
        # Normalize phone comparison
        p = phone.strip().replace(" ", "").replace("+", "")
        for admin in self.admin_numbers:
            a = admin.strip().replace(" ", "").replace("+", "")
            if p == a or p.endswith(a) or a.endswith(p):
                return True
        return False
    
    def is_training_command(self, message: str) -> bool:
        """Check if message is a training command."""
        return message.strip().lower().startswith(("/train", "/endtrain"))
    
    async def handle(self, phone: str, message: str) -> str:
        """Main handler for all training commands."""
        if not self.is_admin(phone):
            return "⚠️ Access Denied. Admin only."
        
        msg = message.strip()
        cmd = msg.lower()
        
        # /endtrain
        if cmd == "/endtrain":
            self.training_sessions.pop(phone, None)
            return "✅ Training Mode DEACTIVATED. Albert is back to normal customer mode."
        
        # /train (enter training mode or menu)
        if cmd == "train" or cmd == "/train":
            self.training_sessions[phone] = {"active": True, "last_action": None}
            return self._get_training_menu()
        
        # /train list [category] [page]
        if cmd.startswith("/train list"):
            return await self._handle_list(msg)
        
        # /train delete [id]
        if cmd.startswith("/train delete"):
            return await self._handle_delete(msg)
        
        # /train stats
        if cmd == "/train stats":
            return await self._handle_stats()
        
        # /train [category] — Add new entry
        for cat in VALID_CATEGORIES:
            if cmd.startswith(f"/train {cat}"):
                return await self._handle_add_entry(cat, msg, phone)
        
        return "❓ Unknown command. Send /train for the full menu."
    
    async def _handle_add_entry(self, category: str, message: str, phone: str) -> str:
        """Parse and store a new training entry."""
        lines = message.split("\n")
        content = "\n".join(lines[1:]) if len(lines) > 1 else ""
        
        if not content.strip():
            return self._get_category_format(category)
        
        parsed = self._parse_entry(content, category)
        
        if not parsed.get("scenario") or not parsed.get("ideal_response"):
            return f"⚠️ Missing required fields. Format:\n{self._get_category_format(category)}"
        
        if not parsed.get("trigger_keywords"):
            parsed["trigger_keywords"] = self._extract_keywords(parsed["scenario"])
        
        try:
            client = await supabase_client.get_client()
            entry = {
                "category": category,
                "scenario": parsed["scenario"],
                "ideal_response": parsed["ideal_response"],
                "priority": parsed.get("priority", 5),
                "is_active": True,
                "subcategory": parsed.get("subcategory"),
                "trigger_keywords": parsed.get("trigger_keywords", []),
                "added_by": phone,
                "notes": parsed.get("notes")
            }

            result = await client.table("dynamic_training").insert(entry).execute()
            entry_id = result.data[0]["id"][:8]

            # Invalidate assembler cache so next customer message picks up new training
            from app.prompt_assembler import prompt_assembler
            await prompt_assembler.invalidate_cache()

            return (
                f"✅ Training added!\n\n"
                f"📂 Category: {category}\n"
                f"🆔 ID: {entry_id}\n"
                f"📝 Scenario: {parsed['scenario'][:60]}...\n"
                f"🔑 Keywords: {', '.join(parsed['trigger_keywords'][:5])}"
            )
        except Exception as e:
            logger.error(f"Error storing training: {e}")
            return f"❌ Error storing training: {str(e)}"
    
    def _parse_entry(self, content: str, category: str) -> dict:
        result = {}
        field_mapping = {
            "situation": "scenario", "scenario": "scenario", "customer": "scenario", 
            "question": "scenario", "trigger": "scenario", "type": "subcategory", 
            "stage": "subcategory", "update": "scenario", "rule": "scenario", 
            "example": "ideal_response", "response": "ideal_response", 
            "message": "ideal_response", "answer": "ideal_response", 
            "approach": "ideal_response", "action": "ideal_response", 
            "avoid": "notes", "technique": "notes", "framework": "notes"
        }
        
        current_key = None
        current_value = []
        
        for line in content.split("\n"):
            line = line.strip()
            if not line: continue
            
            matched = False
            for key in field_mapping:
                if line.lower().startswith(f"{key}:"):
                    if current_key:
                        self._store_parsed_value(result, current_key, " ".join(current_value).strip(), field_mapping)
                    current_key = key
                    current_value = [line.split(":", 1)[1].strip()]
                    matched = True
                    break
            
            if not matched and current_key:
                current_value.append(line)
        
        if current_key:
            self._store_parsed_value(result, current_key, " ".join(current_value).strip(), field_mapping)
        
        return result
    
    def _store_parsed_value(self, result: dict, key: str, value: str, mapping: dict):
        standard_key = mapping.get(key, key)
        if standard_key == "trigger_keywords":
            result[standard_key] = [k.strip() for k in value.replace(",", " ").split() if k.strip()]
        elif standard_key == "notes":
            existing = result.get("notes", "")
            result["notes"] = f"{existing} | {key}: {value}".strip(" | ")
        else:
            result[standard_key] = value

    def _extract_keywords(self, text: str) -> list:
        stop_words = {"hai", "ka", "ki", "ke", "ko", "se", "mein", "kya", "aur", "ya", "toh", "bhi"}
        words = re.findall(r'\w+', text.lower())
        return list(set([w for w in words if w not in stop_words and len(w) > 2]))[:10]

    async def _handle_list(self, message: str) -> str:
        parts = message.lower().split()
        category = parts[2] if len(parts) > 2 and parts[2] in VALID_CATEGORIES else None
        
        client = await supabase_client.get_client()
        query = client.table("training_data").select("*").eq("is_active", True)
        query = (await supabase_client.get_client()).table("dynamic_training")
        if category:
            query = query.eq("category", category)
        
        result = await query.order("created_at", desc=True).limit(10).execute()
        if not result.data: return "📋 No entries found."
        
        lines = [f"📋 Training Entries{f' ({category})' if category else ''}:"]
        for e in result.data:
            lines.append(f"• [{e['id'][:8]}] {e['category']}: {e.get('scenario', '')[:40]}...")
        return "\n".join(lines)

    async def _handle_delete(self, message: str) -> str:
        parts = message.split()
        if len(parts) < 3: return "⚠️ Format: /train delete [id]"
        entry_id = parts[2]
        try:
            client = await supabase_client.get_client()
            await client.table("dynamic_training").update({"is_active": False}).like("id", f"{entry_id}%").execute()
            # Invalidate assembler cache
            from app.prompt_assembler import prompt_assembler
            await prompt_assembler.invalidate_cache()
            return f"✅ Deleted entry starting with {entry_id}"
        except Exception as e: return f"❌ Error: {str(e)}"

    async def _handle_stats(self) -> str:
        try:
            client = await supabase_client.get_client()
            result = await client.table("dynamic_training").select("category, is_active").execute()
            stats = {}
            for e in result.data:
                cat = e["category"]
                stats[cat] = stats.get(cat, 0) + (1 if e["is_active"] else 0)
            
            lines = ["📊 Training Stats:"]
            for cat, count in stats.items():
                lines.append(f"• {cat:12}: {count} active")
            return "\n".join(lines)
        except: return "❌ Stats unavailable."

    def _get_training_menu(self) -> str:
        return "🎯 Training Mode ACTIVE!\n\n/train [category]\nsituation: [when]\nresponse: [reply]\n\nCategories: tone, objection, sales, qna, greeting, closing, followup, personality, voice, context, escalation\n\nCommands: /train list, /train delete [id], /train stats, /endtrain"

    def _get_category_format(self, category: str) -> str:
        return f"📝 Format:\n/train {category}\nsituation: [when to use]\nresponse: [what Albert says]\nnotes: [additional info]"

# Global instance
training_handler = TrainingHandler()
