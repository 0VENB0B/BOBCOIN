"""Passive rewards: interest, XP/levels, achievements, daily claim, jackpot."""

import random
from datetime import UTC, datetime
from math import isqrt

from .core import _Abort, _get_db, _house_ref, _in_txn, _positive_amount, _ref, house_payout, log_history, update_bank

# ── Interest ─────────────────────────────────────────────────────────────────

def calc_interest(deposited: int) -> int:
    """Daily interest amount based on deposited balance (tiered rate)."""
    if deposited <= 0:
        return 0
    if deposited >= 1_000_000:
        rate = 0.002   # 0.20%/day
    elif deposited >= 100_000:
        rate = 0.0015  # 0.15%/day
    else:
        rate = 0.001   # 0.10%/day
    return max(int(deposited * rate), 10)


async def pay_interest_all() -> tuple[int, int]:
    """Pay daily interest to all users with deposited > 0.
    Returns (users_paid, total_paid). Budget cap set by guardian."""
    depositors: list[tuple[str, int]] = []
    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        dep = int(d.get("deposited", d.get("bank", 0)))
        if dep > 0:
            depositors.append((doc.id, dep))

    if not depositors:
        return 0, 0

    h_doc = await _house_ref().get()
    h_data = h_doc.to_dict() or {}
    house_bal = int(h_data.get("balance", 0))
    cap_pct = float(h_data.get("guardian_interest_cap", 0.30))
    budget = int(house_bal * cap_pct)
    if budget <= 0:
        return 0, 0

    users_paid = total_paid = 0
    for uid, dep in depositors:
        amount = calc_interest(dep)
        if total_paid + amount > budget:
            break
        actual = await house_payout(amount)
        if actual <= 0:
            break
        await update_bank(int(uid), actual)
        await log_history(int(uid), {"cmd": "interest", "amount": actual, "net": actual})
        users_paid += 1
        total_paid += actual

    return users_paid, total_paid


# ── XP / Level ───────────────────────────────────────────────────────────────

def xp_to_level(xp: int) -> int:
    """level n requires n² × 10 xp. level = isqrt(xp // 10)."""
    return isqrt(max(xp, 0) // 10)


async def add_xp(user_id: int, xp_gain: int) -> tuple[int, int, bool]:
    """Add XP to user. Returns (new_xp, new_level, leveled_up)."""
    ref = _ref(user_id)

    async def _work(t):
        doc = await ref.get(transaction=t)
        old_xp = int((doc.to_dict() or {}).get("xp", 0))
        new_xp = old_xp + xp_gain
        t.set(ref, {"xp": new_xp}, merge=True)
        return old_xp, new_xp

    old_xp, new_xp = await _in_txn(_work)
    return new_xp, xp_to_level(new_xp), xp_to_level(new_xp) > xp_to_level(old_xp)


# ── Achievements ─────────────────────────────────────────────────────────────

ACHIEVEMENTS: dict[str, tuple[str, str, str]] = {
    "first_win":   ("🩸", "First Blood",  "ชนะเกมครั้งแรก"),
    "jackpot":     ("🎰", "Jackpot!",     "ถูก Slot Jackpot"),
    "death":       ("💀", "Death",        "ถูก 💀💀💀 Death Jackpot"),
    "lottery_5":   ("🎟️", "หวยรวย",      "ถูกหวย 5 ตัว"),
    "streak_5":    ("🔥", "On Fire",      "Win Streak 5 ครั้งติด"),
    "high_roller": ("💎", "High Roller",  "เดิมพันครั้งเดียว ≥ 1,000,000"),
    "daily_7":     ("📅", "Dedicated",    "Daily claim 7 วันติด"),
    "level_10":    ("⭐", "Veteran",      "ถึง Level 10"),
}


async def grant_achievement(user_id: int, key: str) -> bool:
    """Grant achievement. Returns True if newly unlocked."""
    ref = _ref(user_id)

    async def _work(t):
        doc = await ref.get(transaction=t)
        ach = list((doc.to_dict() or {}).get("ach", []))
        if key in ach:
            return False
        ach.append(key)
        t.set(ref, {"ach": ach}, merge=True)
        return True

    return await _in_txn(_work)


async def get_achievements(user_id: int) -> list[str]:
    doc = await _ref(user_id).get()
    return list((doc.to_dict() or {}).get("ach", []))


# ── Daily Claim ───────────────────────────────────────────────────────────────

_DAILY_BASE = 1_000
_DAILY_MAX  = 8_000


async def try_daily(user_id: int) -> tuple[int, int] | bool | None:
    """Claim daily reward. Returns (reward, streak), None if too soon, or False if house is empty."""
    now = int(datetime.now(UTC).timestamp())
    user_ref = _ref(user_id)
    house_ref = _house_ref()

    async def _work(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _Abort()
        d = u_doc.to_dict() or {}
        last = int(d.get("last_daily", 0))
        elapsed = now - last
        if elapsed < 86_400:
            raise _Abort()
        streak = int(d.get("daily_streak", 0))
        streak = streak + 1 if elapsed < 172_800 else 1  # reset if >48h gap
        level = xp_to_level(int(d.get("xp", 0)))
        streak_bonus = min(streak * 150, 1500)  # +150/day, caps at streak 10
        reward = min(_DAILY_BASE + level * 200 + streak_bonus + random.randint(0, 500), _DAILY_MAX)
        hd = h_doc.to_dict() or {}
        house_bal = int(hd.get("balance", 0))
        actual = min(reward, house_bal)
        if actual <= 0:
            raise _Abort(False)   # house empty → caller gets False
        wallet = int(d.get("wallet", 0))
        t.set(user_ref, {"wallet": wallet + actual, "last_daily": now, "daily_streak": streak}, merge=True)
        t.set(house_ref, {
            "balance": house_bal - actual,
            "total_out": int(hd.get("total_out", 0)) + actual,
        }, merge=True)
        return actual, streak

    result = await _in_txn(_work)
    if result is None or result is False:
        return result
    actual, streak = result

    await log_history(user_id, {"cmd": "daily", "reward": actual, "streak": streak, "net": actual})
    return actual, streak


# ── Progressive Jackpot ───────────────────────────────────────────────────────

_JACKPOT_SEED = 50_000


def _jackpot_ref():
    return _get_db().collection("system").document("jackpot")


async def get_jackpot_pool() -> int:
    doc = await _jackpot_ref().get()
    return int((doc.to_dict() or {}).get("pool", _JACKPOT_SEED))


async def contribute_jackpot(amount: int) -> int:
    """Add to jackpot pool counter. Returns new pool size."""
    amount = _positive_amount(amount)
    if amount is None:
        return await get_jackpot_pool()
    ref = _jackpot_ref()

    async def _work(t):
        doc = await ref.get(transaction=t)
        pool = int((doc.to_dict() or {}).get("pool", _JACKPOT_SEED))
        new_pool = pool + amount
        t.set(ref, {"pool": new_pool}, merge=True)
        return new_pool

    return await _in_txn(_work)


async def trigger_jackpot() -> int:
    """Claim jackpot pool, reset to seed. Returns actual paid from house."""
    ref = _jackpot_ref()

    async def _work(t):
        doc = await ref.get(transaction=t)
        pool = int((doc.to_dict() or {}).get("pool", _JACKPOT_SEED))
        t.set(ref, {"pool": _JACKPOT_SEED}, merge=True)
        return pool

    pool = await _in_txn(_work)
    return await house_payout(pool)
