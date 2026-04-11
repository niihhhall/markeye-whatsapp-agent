"""
MarkTracker — writes all Mark activity to Supabase
so the Markeye dashboard can display it in real time.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional
from app.supabase_client import supabase_client

class MarkTracker:
    # ─── LEADS ───────────────────────────────────────────────

    async def create_lead(
        self,
        phone: str,
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        company: str = "",
        industry: str = "",
        lead_source: str = "Other",
        form_message: str = "",
        client_id: Optional[str] = None
    ) -> dict:
        """Call when a new lead submits the form or contacts Mark for the first time."""
        try:
            # Check if lead already exists (duplicate phone)
            existing = await self.get_lead_by_phone(phone)
            if existing:
                return existing

            client = await supabase_client.get_client()
            result = await client.table("leads").insert({
                "phone": phone,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "company": company,
                "industry": industry,
                "lead_source": lead_source,
                "form_message": form_message,
                "client_id": client_id,
                "temperature": "Cold",
                "outcome": "In Progress",
                "signal_score": 0,
            }).execute()

            if result.data:
                lead = result.data[0]
                await self._init_conversation_state(lead["id"])
                print(f"[Mark Tracker] ✅ Lead created: {first_name} {last_name} ({phone})")
                return lead

        except Exception as e:
            print(f"[Markeye Tracker Error] create_lead: {e}")
        return {}

    async def get_lead_by_phone(self, phone: str) -> Optional[dict]:
        """Call on every incoming WhatsApp message to find the lead with retry logic."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                start = asyncio.get_event_loop().time()
                client = await supabase_client.get_client()
                result = await client.table("leads").select("*").eq("phone", phone).execute()
                end = asyncio.get_event_loop().time()
                
                if attempt > 0:
                    print(f"[Mark Tracker] ♻️ Retry {attempt} success in {end-start:.2f}s", flush=True)
                
                return result.data[0] if result.data else None
            except Exception as e:
                print(f"[Markeye Tracker Error] get_lead_by_phone (attempt {attempt+1}): {e}", flush=True)
                if attempt < max_retries:
                    await asyncio.sleep(1) # Small pause before retry
                else:
                    return None

    async def get_all_leads(self, client_id: Optional[str] = None) -> list:
        """Fetch all leads to display in the admin panel."""
        try:
            client = await supabase_client.get_client()
            query = client.table("leads").select("id, phone, first_name, last_name, temperature")
            if client_id:
                query = query.eq("client_id", client_id)
            result = await query.order("created_at", desc=True).execute()
            return result.data if result.data else []
        except Exception as e:
            print(f"[Markeye Tracker Error] get_all_leads: {e}")
            return []

    async def update_signal_score(self, lead_id: str, score: int) -> None:
        """Call whenever Mark recalculated lead quality. Score: 0–10."""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("leads").update({
                "signal_score": max(0, min(10, score)),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] update_signal_score: {e}")

    async def update_temperature(self, lead_id: str, temperature: str) -> None:
        """temperature: 'Cold' | 'Warm' | 'Hot'"""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("leads").update({
                "temperature": temperature,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] update_temperature: {e}")

    async def update_outcome(self, lead_id: str, outcome: str) -> None:
        """outcome: 'In Progress' | 'Not Interested' | 'Disqualified' | 'Meeting Booked'"""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("leads").update({
                "outcome": outcome,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] update_outcome: {e}")

    # ─── MESSAGES ────────────────────────────────────────────

    async def log_inbound(self, lead_id: str, content: str, client_id: Optional[str] = None, metadata: Optional[dict] = None) -> dict:
        """Call every time a lead sends Mark a WhatsApp message."""
        if not lead_id or lead_id == "unknown":
            return {}
        try:
            client = await supabase_client.get_client()
            result = await client.table("messages").insert({
                "lead_id": lead_id,
                "client_id": client_id,
                "direction": "inbound",
                "content": content,
                "metadata": metadata or {}
            }).execute()
            await self._increment_message_count(lead_id)
            await self._update_last_active(lead_id)
            return result.data[0] if result.data else {}
        except Exception as e:
            print(f"[Markeye Tracker Error] log_inbound: {e}")
            return {}

    async def log_outbound(self, lead_id: str, content: str, client_id: Optional[str] = None, metadata: Optional[dict] = None) -> dict:
        """Call every time Mark sends a WhatsApp reply."""
        if not lead_id or lead_id == "unknown":
            return {}
        try:
            client = await supabase_client.get_client()
            result = await client.table("messages").insert({
                "lead_id": lead_id,
                "client_id": client_id,
                "direction": "outbound",
                "content": content,
                "metadata": metadata or {}
            }).execute()
            await self._increment_message_count(lead_id)
            await self._update_last_active(lead_id)
            return result.data[0] if result.data else {}
        except Exception as e:
            print(f"[Markeye Tracker Error] log_outbound: {e}")
            return {}

    # ─── CONVERSATION STATE ───────────────────────────────────

    async def update_state(
        self,
        lead_id: str,
        current_state: str,
        bant_budget: Optional[str] = None,
        bant_authority: Optional[str] = None,
        bant_need: Optional[str] = None,
        bant_timeline: Optional[str] = None,
    ) -> None:
        if not lead_id or lead_id == "unknown":
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "lead_id": lead_id,
                "current_state": current_state,
                "last_active_at": now,
                "updated_at": now,
            }
            # Only include BANT fields that were explicitly passed
            if bant_budget is not None:
                payload["bant_budget"] = bant_budget
            if bant_authority is not None:
                payload["bant_authority"] = bant_authority
            if bant_need is not None:
                payload["bant_need"] = bant_need
            if bant_timeline is not None:
                payload["bant_timeline"] = bant_timeline

            client = await supabase_client.get_client()
            await client.table("conversation_state").upsert(payload, on_conflict="lead_id").execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] update_state: {e}")

    async def set_typing_status(self, lead_id: str, is_typing: bool) -> None:
        """Updates the is_typing field in conversation_state."""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("conversation_state").update({
                "is_typing": is_typing,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("lead_id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] set_typing_status: {e}")

    # ─── BOOKINGS ─────────────────────────────────────────────

    async def confirm_booking(
        self,
        lead_id: str,
        calendly_event_id: str,
        scheduled_at: str,
    ) -> dict:
        """Call when Calendly confirms a meeting. Automatically updates outcome + state."""
        if not lead_id or lead_id == "unknown":
            return {}
        try:
            client = await supabase_client.get_client()
            result = await client.table("bookings").insert({
                "lead_id": lead_id,
                "calendly_event_id": calendly_event_id,
                "scheduled_at": scheduled_at,
                "status": "confirmed",
            }).execute()
            # Auto-update lead outcome and conversation state
            await self.update_outcome(lead_id, "Meeting Booked")
            await self.update_state(lead_id, "Confirmed")
            print(f"[Mark Tracker] ✅ Booking confirmed for lead {lead_id}")
            return result.data[0] if result.data else {}
        except Exception as e:
            print(f"[Markeye Tracker Error] confirm_booking: {e}")
            return {}

    async def cancel_booking(self, lead_id: str, calendly_event_id: str) -> None:
        """Call when Calendly cancels a meeting. Puts lead back to In Progress."""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("bookings").update({
                "status": "cancelled"
            }).eq("calendly_event_id", calendly_event_id).execute()
            await self.update_outcome(lead_id, "In Progress")
            await self.update_state(lead_id, "Awaiting")
        except Exception as e:
            print(f"[Markeye Tracker Error] cancel_booking: {e}")

    async def get_conversation_state(self, lead_id: str) -> Optional[dict]:
        """Fetch the latest state for a lead directly from Supabase."""
        if not lead_id or lead_id == "unknown":
            return None
        try:
            client = await supabase_client.get_client()
            result = await client.table("conversation_state").select("*").eq("lead_id", lead_id).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"[Markeye Tracker Error] get_conversation_state: {e}")
            return None

    async def get_latest_booking(self, lead_id: str) -> Optional[dict]:
        """Fetch the most recent booking for a lead."""
        if not lead_id or lead_id == "unknown":
            return None
        try:
            client = await supabase_client.get_client()
            result = await client.table("bookings").select("*").eq("lead_id", lead_id).order("created_at", desc=True).limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"[Markeye Tracker Error] get_latest_booking: {e}")
            return None

    # ─── LLM TRACKING ─────────────────────────────────────────

    async def log_llm_call(
        self,
        lead_id: str,
        response_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: int,
        conversation_state: str,
    ) -> None:
        """Called automatically by llm.py — do not call manually."""
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("llm_sessions").insert({
                "lead_id": lead_id,
                "helicone_id": response_id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "conversation_state": conversation_state,
            }).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] log_llm_call: {e}")

    # ─── PRIVATE HELPERS ──────────────────────────────────────

    async def _init_conversation_state(self, lead_id: str) -> None:
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("conversation_state").upsert({
                "lead_id": lead_id,
                "current_state": "Opening",
                "message_count": 0,
                "last_active_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="lead_id").execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] _init_conversation_state: {e}")

    async def _increment_message_count(self, lead_id: str) -> None:
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            result = await client.table("conversation_state").select("message_count").eq("lead_id", lead_id).execute()
            if result.data:
                count = (result.data[0].get("message_count") or 0) + 1
                await client.table("conversation_state").update({
                    "message_count": count
                }).eq("lead_id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] _increment_message_count: {e}")

    async def _update_last_active(self, lead_id: str) -> None:
        if not lead_id or lead_id == "unknown":
            return
        try:
            client = await supabase_client.get_client()
            await client.table("conversation_state").update({
                "last_active_at": datetime.now(timezone.utc).isoformat()
            }).eq("lead_id", lead_id).execute()
        except Exception as e:
            print(f"[Markeye Tracker Error] _update_last_active: {e}")
