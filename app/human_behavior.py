"""
app/human_behavior.py
=====================
Human Behavior Layer — micro-interaction orchestrator.

Sits between LLM response and message delivery. Owns:
  - Blue tick timing (mark as read)
  - Reading delay (Mark "reading" their message)
  - Think pause (before typing starts)
  - Typing indicator (Cloud API or Baileys — routed transparently)
  - Typing delay with interrupt polling
  - Review pause (before send)
  - Inter-chunk gap (between bubbles)

Sequence per chunk:
  Chunk 0:  blue_tick_delay → mark_as_read
            → reading_delay  (LLM already done by here)
            → think_pause
            → typing_on → typing_delay [interrupt polled every 500ms] → review_pause
            → SEND

  Chunk N:  inter_chunk_gap
            → typing_on → typing_delay [interrupt polled]
            → SEND

Works for both Cloud API and Baileys — message_router.py routes transparently.
"""

import asyncio
import logging
import random
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


# ─── Timing calculations ──────────────────────────────────────────────────────

def _blue_tick_delay(last_message_ts: float) -> float:
    """
    2–2.5s if active chat (last message < 60s ago).
    4–5.5s if returning after a gap.
    """
    gap = time.time() - last_message_ts
    if gap < 60:
        return random.uniform(1.5, 2.5)
    return random.uniform(4.0, 5.5)


def _reading_delay(text: str) -> float:
    """
    0.04s per character. Clamped 4–10s.
    Simulates Mark reading the lead's message before responding.
    """
    raw = len(text) * 0.04
    jitter = random.uniform(-0.3, 0.3)
    return max(4.0, min(10.0, raw + jitter))


def _think_pause() -> float:
    """0.8–1.2s. Mark deciding what to say."""
    return random.uniform(0.8, 1.2)


def _typing_delay(text: str) -> float:
    """
    0.1s per character. Min 1.5s.
    Short messages get a small bonus to avoid instant sends.
    """
    char_count = len(text)
    base = char_count * 0.1
    if char_count < 20:
        return max(1.5, base + random.uniform(0.3, 0.7))
    return max(1.5, base + random.uniform(-0.5, 0.5))


def _review_pause() -> float:
    """0.3–0.7s. Mark re-reading before hitting send."""
    return random.uniform(0.3, 0.7)


def _inter_chunk_gap() -> float:
    """
    0.3–0.8s between chunks.
    Typing indicator drops briefly then comes back on for the next bubble.
    """
    return random.uniform(0.3, 0.8)


# ─── Interrupt polling ────────────────────────────────────────────────────────

async def _poll_with_interrupt(
    phone: str,
    duration: float,
    interval: float = 0.5,
) -> bool:
    """
    Sleep for `duration` seconds, polling for new messages every `interval`.
    Returns True if interrupted (new message arrived), False if clean.
    """
    from app.redis_client import redis_client

    elapsed = 0.0
    while elapsed < duration:
        sleep_for = min(interval, duration - elapsed)
        await asyncio.sleep(sleep_for)
        elapsed += sleep_for
        if await redis_client.has_new_messages(phone):
            return True  # interrupted
    return False  # clean


# ─── Main delivery function ───────────────────────────────────────────────────

async def deliver_with_human_timing(
    phone: str,
    chunks: List[str],
    incoming_text: str = "",
    message_id: str = "",
    last_message_ts: float = 0.0,
    client_config: Optional[dict] = None,
) -> bool:
    """
    Deliver message chunks with full human-like micro-interaction sequence.

    Returns:
        True  — all chunks delivered cleanly
        False — interrupted mid-delivery (caller should re-invoke)

    Args:
        phone:            Internal format "whatsapp:+..."
        chunks:           List of message bubbles to send (max 3)
        incoming_text:    The lead's message text (used for reading_delay calc)
        message_id:       WhatsApp message ID from the incoming webhook
        last_message_ts:  Unix timestamp of the lead's last message
        client_config:    Client config dict for multi-tenant routing
    """
    from app.message_router import send_message, send_typing_indicator, mark_as_read
    from app.redis_client import redis_client

    if not chunks:
        logger.warning("[HumanBehavior] No chunks to deliver for %s", phone)
        return True

    # Fallback: if no timestamp given, assume active chat
    effective_ts = last_message_ts if last_message_ts > 0 else time.time() - 30

    for i, chunk in enumerate(chunks):

        # ── Pre-chunk interrupt check ──────────────────────────────────────
        if await redis_client.has_new_messages(phone):
            logger.info("[HumanBehavior] Interrupt before chunk %d for %s", i, phone)
            return False

        if i == 0:
            # ── CHUNK 0: full human sequence ───────────────────────────────

            # 1. Blue tick delay — before marking as read
            bt = _blue_tick_delay(effective_ts)
            logger.debug("[HumanBehavior] Blue tick delay %.1fs for %s", bt, phone)
            interrupted = await _poll_with_interrupt(phone, bt)
            if interrupted:
                return False

            # 2. Mark as read (blue ticks appear on lead's screen)
            if message_id:
                await mark_as_read(phone, message_id, client_config=client_config)
                logger.debug("[HumanBehavior] Marked as read: %s", message_id)

            # 3. Reading delay — Mark "reads" their message
            rd = _reading_delay(incoming_text or chunk)
            logger.debug("[HumanBehavior] Reading delay %.1fs for %s", rd, phone)
            interrupted = await _poll_with_interrupt(phone, rd)
            if interrupted:
                return False

            # 4. Think pause
            tp = _think_pause()
            await asyncio.sleep(tp)

            # 5. Typing indicator ON
            await send_typing_indicator(
                phone,
                message_id=message_id,
                client_config=client_config,
            )

            # 6. Typing delay with interrupt polling
            td = _typing_delay(chunk)
            logger.debug("[HumanBehavior] Typing delay %.1fs for chunk 0", td)
            interrupted = await _poll_with_interrupt(phone, td)
            if interrupted:
                return False

            # 7. Review pause (Mark re-reads before sending)
            await asyncio.sleep(_review_pause())

        else:
            # ── CHUNK N: minimal sequence ──────────────────────────────────

            # Inter-chunk gap (typing drops briefly)
            gap = _inter_chunk_gap()
            interrupted = await _poll_with_interrupt(phone, gap)
            if interrupted:
                return False

            # Typing indicator back ON for this bubble
            await send_typing_indicator(
                phone,
                message_id=message_id,
                client_config=client_config,
            )

            # Typing delay
            td = _typing_delay(chunk)
            interrupted = await _poll_with_interrupt(phone, td)
            if interrupted:
                return False

        # ── Send the bubble ────────────────────────────────────────────────
        await send_message(phone, chunk, client_config=client_config)
        logger.info(
            "[HumanBehavior] Sent chunk %d/%d to %s: %s...",
            i + 1, len(chunks), phone, chunk[:40],
        )

    return True


# ─── Convenience: outbound template / follow-up (no interrupt, no reading delay)
# Used for proactive outreach where there's no incoming message to "read"
# ─────────────────────────────────────────────────────────────────────────────

async def deliver_outbound_sequence(
    phone: str,
    chunks: List[str],
    client_config: Optional[dict] = None,
) -> None:
    """
    Simplified sequence for proactive outbound (no incoming message).
    No blue tick, no reading delay. Just think → type → send per chunk.
    """
    from app.message_router import send_message, send_typing_indicator

    for i, chunk in enumerate(chunks):
        if i == 0:
            await asyncio.sleep(_think_pause())

        await send_typing_indicator(phone, message_id="", client_config=client_config)
        td = _typing_delay(chunk)
        await asyncio.sleep(td)
        await asyncio.sleep(_review_pause() if i == 0 else 0)
        await send_message(phone, chunk, client_config=client_config)

        if i < len(chunks) - 1:
            await asyncio.sleep(_inter_chunk_gap())
