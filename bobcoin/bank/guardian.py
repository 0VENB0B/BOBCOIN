"""Bank guardian: health status, luck modifiers, the whale-proof bet cap and
force collection of overdue loans."""

import asyncio

from .core import (
    _Abort,
    _BANK_FLOOR,
    _get_db,
    _house_ref,
    _in_txn,
    _ref,
    get_house_balance,
    get_house_data,
    log_history,
)
from .debt import house_auto_borrow
from ..settings import MAX_BET

_MIN_HOUSE_TO_GAMBLE = 100_000  # บาทต่ำสุดที่คลังต้องมีก่อนเปิดให้เล่น


async def house_can_pay_games() -> bool:
    """Return True if house can pay. Auto-borrows if below threshold and under debt ceiling."""
    bal = await get_house_balance()
    if bal >= _MIN_HOUSE_TO_GAMBLE:
        return True
    return await house_auto_borrow(_MIN_HOUSE_TO_GAMBLE - bal)


# Whale protection: cap a single bet so one outcome can't wipe the house.
# 2% of balance → worst case 20x jackpot ≈ 40% of house in a single spin.
_MAX_BET_RATIO = 0.02
_MAX_BET_FLOOR = 1_000


async def max_bet_allowed(bal: int | None = None) -> int:
    """Max single bet allowed right now, based on house balance (whale-proof).
    Pass a pre-fetched balance to avoid an extra read."""
    if bal is None:
        bal = await get_house_balance()
    return min(MAX_BET, max(int(bal * _MAX_BET_RATIO), _MAX_BET_FLOOR))


def house_status_band(bal: int) -> tuple[int, str, str]:
    """Return (tier, label, icon) for a house balance. tier: 0=รวย ... 3=แทบล้มละลาย."""
    if bal >= 10_000_000:
        return 0, "ร่ำรวยมาก", "🟢"
    if bal >= 1_000_000:
        return 1, "พอไปได้", "🟡"
    if bal >= 100_000:
        return 2, "เริ่มสั่นคลอน", "🟠"
    return 3, "แทบล้มละลาย", "🔴"


# ── Luck ─────────────────────────────────────────────────────────────────────

async def get_user_luck(user_id: int) -> float:
    doc = await _ref(user_id).get()
    return float((doc.to_dict() or {}).get("luck", 1.0))


async def set_user_luck(user_id: int, luck: float) -> None:
    await _ref(user_id).set({"luck": max(0.0, luck)}, merge=True)


# Crisis thresholds — house imposes luck penalty on ALL players when bleeding
_CRISIS_TIERS = [
    (500_000,  0.30),   # severe: bal < 500k → luck capped at 0.30
    (2_000_000, 0.55),  # bad:    bal < 2M   → luck capped at 0.55
    (5_000_000, 0.75),  # mild:   bal < 5M   → luck capped at 0.75
]


async def get_effective_luck(user_id: int) -> float:
    """Return user's luck, clamped down when house is in crisis."""
    user_luck, bal = await asyncio.gather(get_user_luck(user_id), get_house_balance())
    for threshold, cap in _CRISIS_TIERS:
        if bal < threshold:
            return min(user_luck, cap)
    return user_luck


async def get_bank_health() -> dict:
    hd = await get_house_data()
    bal, tin = hd["balance"], hd["total_in"]
    ratio = bal / max(tin, 1)
    if ratio >= 0.20 and bal >= _BANK_FLOOR:
        status = "healthy"
    elif ratio >= 0.10 or bal >= _BANK_FLOOR // 2:
        status = "warning"
    elif ratio >= 0.05 or bal >= _BANK_FLOOR // 5:
        status = "critical"
    else:
        status = "danger"
    return {"balance": bal, "ratio": ratio, "status": status, "total_in": tin}


async def get_lucky_users() -> list[dict]:
    """All users with luck != 1.0, sorted by luck descending."""
    result = []
    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        luck = float(d.get("luck", 1.0))
        if luck != 1.0:
            result.append({
                "id": doc.id,
                "luck": luck,
                "wallet": int(d.get("wallet", 0)),
                "deposited": int(d.get("deposited", d.get("bank", 0))),
                "guardian_original_luck": d.get("guardian_original_luck"),
            })
    return sorted(result, key=lambda x: x["luck"], reverse=True)


async def guardian_nerf_user(user_id: int, current_luck: float, new_luck: float) -> None:
    """Reduce luck and store original value for later restoration."""
    ref = _ref(user_id)
    doc = await ref.get()
    orig = (doc.to_dict() or {}).get("guardian_original_luck")
    update = {"luck": round(new_luck, 3)}
    if orig is None:  # first guardian nerf — save original
        update["guardian_original_luck"] = current_luck
    await ref.set(update, merge=True)


async def guardian_restore_user(user_id: int, current_luck: float, original_luck: float) -> float:
    """Restore 25% of gap toward original. Returns new luck. Clears flag when fully restored."""
    restored = min(current_luck + (original_luck - current_luck) * 0.25, original_luck)
    restored = round(restored, 3)
    update = {"luck": restored}
    if restored >= original_luck:
        update["guardian_original_luck"] = None
    await _ref(user_id).set(update, merge=True)
    return restored


# ── Guardian Force Collection ─────────────────────────────────────────────────

async def guardian_force_collect(pct: float = 0.10) -> tuple[int, int]:
    """Silently auto-repay pct of wallet from users with outstanding loans back to house.
    Returns (users_hit, total_collected)."""
    house_ref = _house_ref()
    users_hit = total_collected = 0

    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        lb = int(d.get("loan_balance", 0))
        w  = int(d.get("wallet", 0))
        if lb <= 0 or w <= 0:
            continue
        take = min(max(int(w * pct), 1), lb, w)
        if take <= 0:
            continue

        uid = int(doc.id)
        ref = _ref(uid)

        async def _work(t, _ur=ref, _hr=house_ref, _take=take):
            u_snap = await _ur.get(transaction=t)
            h_snap = await _hr.get(transaction=t)
            sd = u_snap.to_dict() or {}
            hd = h_snap.to_dict() or {}
            w2  = int(sd.get("wallet", 0))
            lb2 = int(sd.get("loan_balance", 0))
            actual = min(_take, w2, lb2)
            if actual <= 0:
                raise _Abort()
            t.set(_ur, {"wallet": w2 - actual, "loan_balance": lb2 - actual}, merge=True)
            t.set(_hr, {
                "balance":  int(hd.get("balance", 0)) + actual,
                "total_in": int(hd.get("total_in", 0)) + actual,
            }, merge=True)
            return actual

        collected = await _in_txn(_work)
        if collected is None:
            continue
        await log_history(uid, {"cmd": "guardian_collect", "amount": collected, "net": -collected})
        users_hit += 1
        total_collected += collected

    return users_hit, total_collected
