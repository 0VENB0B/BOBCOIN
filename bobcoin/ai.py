import logging
import os

import aiohttp

from . import metrics

logger = logging.getLogger("bobcoin.ai")

BOB_SYSTEM = (
    "คุณชื่อ BOB บอท Discord ที่มีบุคลิกกวนส้นตีน ตลก พูดตรงไม่อ้อมค้อม "
    "ใช้ภาษาไทยสแลงแบบเพื่อนสนิท ไม่เป็นทางการ ไม่ต้องสุภาพ "
    "แซวได้ ล้อได้ในแบบตลก ไม่ได้โกรธจริง "
    "ตอบสั้น 1-2 ประโยค ห้ามพูดยาว ห้ามใช้คำสุภาพเกินไป"
)

_session: aiohttp.ClientSession | None = None


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def call_ai(system: str, messages: list, fallback: str = "", max_tokens: int = 150) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        metrics.incr("ai_skipped_no_key")
        return fallback
    metrics.incr("ai_calls")
    try:
        async with _get_session().post(
            "https://gateway.9arm.co/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={
                "model": "qwen3.6-35b-a3b",
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, *messages],
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
            result = data["choices"][0]["message"]["content"].strip()
            metrics.incr("ai_successes")
            return result
    except Exception:
        logger.exception("AI call failed")
        metrics.incr("ai_failures")
        return fallback
