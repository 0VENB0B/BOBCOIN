"""User loans: credit limits, take/repay, daily interest accrual, AI approval."""

import json as _json
from datetime import datetime, timezone

from .core import (
    _Abort,
    _cache_get,
    _cache_set,
    _get_db,
    _house_ref,
    _in_txn,
    _positive_amount,
    _ref,
    get_history,
    log_history,
)
from .guardian import get_bank_health
from .rewards import xp_to_level

_LOAN_INTEREST_RATE = 0.003   # 0.3%/day on outstanding balance
_LOAN_BASE_LIMIT    = 50_000


def calc_loan_limit(level: int, deposited: int) -> int:
    """Credit limit scales with level and deposited savings."""
    return min(_LOAN_BASE_LIMIT + level * 10_000 + int(deposited * 0.3), 10_000_000)


async def get_loan_info(user_id: int) -> dict:
    doc = await _ref(user_id).get()
    d = doc.to_dict() or {}
    level     = xp_to_level(int(d.get("xp", 0)))
    deposited = int(d.get("deposited", d.get("bank", 0)))
    loan_bal  = int(d.get("loan_balance", 0))
    # AI-approved loans persist their ceiling so the true headroom is visible
    ai_ceiling = int(d.get("ai_loan_ceiling", 0))
    limit     = max(calc_loan_limit(level, deposited), ai_ceiling)
    return {
        "loan_balance":       loan_bal,
        "loan_limit":         limit,
        "available":          max(limit - loan_bal, 0),
        "level":              level,
        "deposited":          deposited,
        "daily_interest":     max(int(loan_bal * _LOAN_INTEREST_RATE), 10) if loan_bal > 0 else 0,
        "loan_taken_at":      int(d.get("loan_taken_at", 0)),
        "last_loan_interest": int(d.get("last_loan_interest", 0)),
    }


async def take_loan(user_id: int, amount: int, ai_approved: int = 0) -> str | None:
    """Borrow from house. Returns error string or None on success.
    ai_approved: if > 0, skips static calc_loan_limit and uses this as the total credit ceiling."""
    amount = _positive_amount(amount)
    if amount is None:
        return "จำนวนต้องมากกว่า 0"

    user_ref  = _ref(user_id)
    house_ref = _house_ref()
    now = int(datetime.now(timezone.utc).timestamp())

    async def _work(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _Abort("ยังไม่มีบัญชี")
        ud = u_doc.to_dict() or {}
        hd = h_doc.to_dict() or {}
        loan_bal  = int(ud.get("loan_balance", 0))
        deposited = int(ud.get("deposited", ud.get("bank", 0)))
        level     = xp_to_level(int(ud.get("xp", 0)))
        limit     = ai_approved if ai_approved > 0 else calc_loan_limit(level, deposited)
        available = max(limit - loan_bal, 0)
        if amount > available:
            raise _Abort("limit")
        if int(hd.get("balance", 0)) < amount:
            raise _Abort("คลังหลวงไม่มีเงินให้กู้ตอนนี้")
        new_fields: dict = {
            "wallet":       int(ud.get("wallet", 0)) + amount,
            "loan_balance": loan_bal + amount,
        }
        if loan_bal == 0:          # first draw — record when the debt started
            new_fields["loan_taken_at"] = now
        if ai_approved > 0:        # persist the AI ceiling so get_loan_info sees it
            new_fields["ai_loan_ceiling"] = max(int(ud.get("ai_loan_ceiling", 0)), ai_approved)
        t.set(user_ref, new_fields, merge=True)
        t.set(house_ref, {
            "balance":   int(hd.get("balance", 0)) - amount,
            "total_out": int(hd.get("total_out", 0)) + amount,
        }, merge=True)

    err = await _in_txn(_work)
    if err == "limit":
        info = await get_loan_info(user_id)
        return f"วงเงินที่กู้ได้เหลือ **{info['available']:,}** 🪙"
    if err:
        return err
    await log_history(user_id, {"cmd": "loan", "amount": amount, "net": amount})
    return None


async def repay_loan(user_id: int, amount: int) -> tuple[int, str | None]:
    """Repay loan. Returns (actual_repaid, error_str). error_str is None on success."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0, "จำนวนต้องมากกว่า 0"
    user_ref  = _ref(user_id)
    house_ref = _house_ref()

    async def _work(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _Abort((0, "ไม่มียอดหนี้ที่ต้องชำระ"))
        ud = u_doc.to_dict() or {}
        hd = h_doc.to_dict() or {}
        loan_bal = int(ud.get("loan_balance", 0))
        wallet   = int(ud.get("wallet", 0))
        if loan_bal <= 0:
            raise _Abort((0, "ไม่มียอดหนี้ที่ต้องชำระ"))
        actual = min(amount, loan_bal, wallet)
        if actual <= 0:
            raise _Abort((0, "เงินในกระเป๋าไม่พอ"))
        new_fields = {
            "wallet":       wallet - actual,
            "loan_balance": loan_bal - actual,
        }
        if loan_bal - actual <= 0:
            new_fields["ai_loan_ceiling"] = 0   # full repayment resets AI headroom
        t.set(user_ref, new_fields, merge=True)
        t.set(house_ref, {
            "balance":  int(hd.get("balance", 0)) + actual,
            "total_in": int(hd.get("total_in", 0)) + actual,
        }, merge=True)
        return actual

    result = await _in_txn(_work)
    if isinstance(result, tuple):   # abort → (0, error_message)
        return result
    await log_history(user_id, {"cmd": "repay", "amount": result, "net": -result})
    return result, None


async def accrue_loan_interest() -> tuple[int, int]:
    """Charge 0.3%/day interest on all outstanding user loans. Atomic per user, 23h guard."""
    now = int(datetime.now(timezone.utc).timestamp())
    users_charged = total_interest = 0

    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        if int(d.get("loan_balance", 0)) <= 0:
            continue
        if now - int(d.get("last_loan_interest", 0)) < 82_800:   # 23h guard
            continue

        uid = int(doc.id)
        ref = _ref(uid)

        async def _work(t, _ref=ref, _now=now):
            snap = await _ref.get(transaction=t)
            sd   = snap.to_dict() or {}
            lb   = int(sd.get("loan_balance", 0))
            if lb <= 0 or _now - int(sd.get("last_loan_interest", 0)) < 82_800:
                raise _Abort()
            charged = max(int(lb * _LOAN_INTEREST_RATE), 10)
            t.set(_ref, {"loan_balance": lb + charged, "last_loan_interest": _now}, merge=True)
            return charged

        charged = await _in_txn(_work)
        if charged is None:
            continue
        await log_history(uid, {"cmd": "loan_interest", "amount": charged, "net": -charged})
        users_charged += 1
        total_interest += charged

    return users_charged, total_interest


async def get_total_outstanding_loans() -> int:
    """Sum of all users' current loan_balance (principal + accrued interest).
    Cached 60s — only used for read-only display."""
    cached = _cache_get("outstanding_loans")
    if cached is not None:
        return cached
    total = 0
    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        total += int(d.get("loan_balance", 0))
    _cache_set("outstanding_loans", total)
    return total


# ── AI Loan Approval ──────────────────────────────────────────────────────────

_AI_LOAN_SYSTEM = (
    "คุณเป็น AI อนุมัติสินเชื่อ GUCOIN ปกป้องธนาคารกลางจากการล่ม\n"
    "วิเคราะห์ความเสี่ยงจากข้อมูล user และสถานะธนาคาร\n"
    "ตอบ JSON เท่านั้น: {\"approved\": <int>, \"reason\": \"...\"}\n"
    "ถ้าเสี่ยงเกินหรือธนาคารอ่อนแอ ให้ approved=0"
)


async def ai_loan_limit(user_id: int, requested: int) -> int:
    """Ask AI to approve a loan exceeding the static limit.
    Returns approved amount (0 = denied). Hard cap: 20% of house balance."""
    from ..ai import call_ai

    health = await get_bank_health()
    # ponytail: hard ceiling prevents any single loan from nuking the house
    hard_ceil = min(requested, int(health["balance"] * 0.20))
    if hard_ceil <= 0:
        return 0

    doc = await _ref(user_id).get()
    d = doc.to_dict() or {}
    level     = xp_to_level(int(d.get("xp", 0)))
    deposited = int(d.get("deposited", d.get("bank", 0)))
    wallet    = int(d.get("wallet", 0))
    loan_bal  = int(d.get("loan_balance", 0))

    history  = await get_history(user_id, limit=20)
    game_h   = [e for e in history if e.get("cmd") in ("slot", "flip", "lottery")]
    wins     = sum(1 for e in game_h if e.get("net", 0) > 0)
    net      = sum(e.get("net", 0) for e in game_h)

    prompt = (
        f"ขอกู้: {requested:,}  วงเงินปลอดภัยสูงสุด: {hard_ceil:,}\n"
        f"level:{level}  deposited:{deposited:,}  wallet:{wallet:,}  หนี้ค้าง:{loan_bal:,}\n"
        f"เกม {len(game_h)} ครั้ง — ชนะ {wins}  กำไรสุทธิ {net:+,}\n"
        f"ธนาคาร: {health['status']}  balance:{health['balance']:,}  ratio:{health['ratio']:.1%}"
    )

    raw = await call_ai(_AI_LOAN_SYSTEM, [{"role": "user", "content": prompt}], fallback="{}", max_tokens=120)
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = _json.loads(raw[start:end]) if start >= 0 else {}
        return max(0, min(int(data.get("approved", 0)), hard_ceil))
    except Exception:
        return 0
