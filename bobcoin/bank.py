import asyncio
import json as _json
import logging
import os
import random
from datetime import datetime, timezone
from math import isqrt

from google.cloud.firestore import AsyncClient, Query, async_transactional

logger = logging.getLogger("bobcoin.bank")

_db: AsyncClient | None = None


def _get_db() -> AsyncClient:
    global _db
    if _db is None:
        _db = AsyncClient(project=os.getenv("FIREBASE_PROJECT_ID"))
    return _db


def _ref(user_id: int):
    return _get_db().collection("users").document(str(user_id))


def _house_ref():
    return _get_db().collection("system").document("bank")


def _parse(data: dict | None) -> tuple[int, int]:
    if not data:
        return 0, 0
    w = int(data.get("wallet", 0))
    # prefer 'deposited', fall back to old 'bank' field for migration
    d = int(data.get("deposited", data.get("bank", 0)))
    return w, d


def _positive_amount(amount) -> int | None:
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


class _Abort(Exception):
    pass


class _AbortNoRecipient(_Abort):
    pass


class _AbortHouseBroke(_Abort):
    pass


class _AbortLimit(_Abort):
    pass


# ── User accounts ───────────────────────────────────────────────────────────

async def open_account(user, extra: dict | None = None) -> bool:
    ref = _ref(user.id)
    doc = await ref.get()
    if doc.exists:
        return False
    data = {"wallet": 0, "deposited": 0}
    if extra:
        data.update(extra)
    await ref.set(data)
    return True


async def is_registered(user_id: int) -> bool:
    return (await _ref(user_id).get()).exists


async def get_bank_data() -> dict:
    result = {}
    async for doc in _get_db().collection("users").stream():
        result[doc.id] = doc.to_dict()
    return result


async def get_balance(user) -> list[int]:
    doc = await _ref(user.id).get()
    if not doc.exists:
        return [0, 0]
    w, d = _parse(doc.to_dict())
    return [w, d]


async def update_bank(user, change=0) -> list[int] | None:
    """Add/subtract from user wallet only. user can be a Discord user or int user_id."""
    change = int(change)
    ref = _ref(user if isinstance(user, int) else user.id)

    @async_transactional
    async def _txn(transaction):
        doc = await ref.get(transaction=transaction)
        if not doc.exists:
            raise _Abort()
        w, d = _parse(doc.to_dict())
        new_w = w + change
        if new_w < 0:
            raise _Abort()
        transaction.set(ref, {"wallet": new_w, "deposited": d}, merge=True)
        return [new_w, d]

    try:
        return await _txn(_get_db().transaction())
    except _Abort:
        return None


async def charge_wallet(user, amount: int) -> list[int] | None:
    amount = _positive_amount(amount)
    if amount is None:
        return None
    return await update_bank(user, -amount)


# ── Central / House bank ────────────────────────────────────────────────────

async def get_house_data() -> dict:
    doc = await _house_ref().get()
    d = doc.to_dict() or {}
    return {
        "balance":   int(d.get("balance", 0)),
        "total_in":  int(d.get("total_in", 0)),
        "total_out": int(d.get("total_out", 0)),
    }


async def get_house_balance() -> int:
    return (await get_house_data())["balance"]


async def house_receive(amount: int) -> int:
    """House collects money. Returns new balance."""
    amount = _positive_amount(amount)
    if amount is None:
        return await get_house_balance()
    ref = _house_ref()

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        d = doc.to_dict() or {}
        new_bal = int(d.get("balance", 0)) + amount
        t.set(ref, {"balance": new_bal, "total_in": int(d.get("total_in", 0)) + amount}, merge=True)
        return new_bal

    return await _txn(_get_db().transaction())


async def house_payout(amount: int) -> int:
    """House pays winnings. Returns actual amount paid (capped at balance)."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0
    ref = _house_ref()

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        d = doc.to_dict() or {}
        bal = int(d.get("balance", 0))
        actual = min(amount, bal)
        t.set(ref, {"balance": bal - actual, "total_out": int(d.get("total_out", 0)) + actual}, merge=True)
        return actual

    return await _txn(_get_db().transaction())


# ── Deposit / Withdraw (user ↔ central bank, atomic 2-doc) ─────────────────

async def user_deposit(user, amount: int) -> list[int] | None:
    """wallet → central bank. Returns [new_wallet, new_deposited] or None."""
    amount = _positive_amount(amount)
    if amount is None:
        return None
    user_ref  = _ref(user.id)
    house_ref = _house_ref()

    @async_transactional
    async def _txn(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _Abort()
        w, d  = _parse(u_doc.to_dict())
        if w < amount:
            raise _Abort()
        hd = h_doc.to_dict() or {}
        t.set(user_ref,  {"wallet": w - amount, "deposited": d + amount}, merge=True)
        t.set(house_ref, {"balance": int(hd.get("balance", 0)) + amount, "total_in": int(hd.get("total_in", 0)) + amount}, merge=True)
        return [w - amount, d + amount]

    try:
        return await _txn(_get_db().transaction())
    except _Abort:
        return None


# Returns: [wallet, deposited] | None (not enough deposited) | False (house broke)
async def user_withdraw(user, amount: int) -> list[int] | None | bool:
    """central bank → wallet. Returns [new_wallet, new_deposited], None, or False."""
    amount = _positive_amount(amount)
    if amount is None:
        return None
    user_ref  = _ref(user.id)
    house_ref = _house_ref()

    @async_transactional
    async def _txn(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _Abort()
        w, d  = _parse(u_doc.to_dict())
        hd = h_doc.to_dict() or {}
        h_bal = int(hd.get("balance", 0))
        if d < amount:
            raise _Abort()
        if h_bal < amount:
            raise _AbortHouseBroke()
        t.set(user_ref,  {"wallet": w + amount, "deposited": d - amount}, merge=True)
        t.set(house_ref, {"balance": h_bal - amount, "total_out": int(hd.get("total_out", 0)) + amount}, merge=True)
        return [w + amount, d - amount]

    try:
        return await _txn(_get_db().transaction())
    except _AbortHouseBroke:
        return False
    except _Abort:
        return None


# ── Transfer between users ──────────────────────────────────────────────────

# Returns: int (remaining wallet) | None (insufficient funds) | False (recipient not registered)
async def transfer_to_user(sender, recipient, amount: int) -> int | None | bool:
    amount = _positive_amount(amount)
    if amount is None:
        return None
    sender_ref    = _ref(sender.id)
    recipient_ref = _ref(recipient.id)

    @async_transactional
    async def _txn(transaction):
        r_doc = await recipient_ref.get(transaction=transaction)
        if not r_doc.exists:
            raise _AbortNoRecipient()
        s_doc = await sender_ref.get(transaction=transaction)
        if not s_doc.exists:
            raise _Abort()
        s_w, s_d = _parse(s_doc.to_dict())
        if s_w < amount:
            raise _Abort()
        r_w, r_d = _parse(r_doc.to_dict())
        transaction.set(sender_ref,    {"wallet": s_w - amount, "deposited": s_d}, merge=True)
        transaction.set(recipient_ref, {"wallet": r_w + amount, "deposited": r_d}, merge=True)
        return s_w - amount

    try:
        return await _txn(_get_db().transaction())
    except _AbortNoRecipient:
        return False
    except _Abort:
        return None


# ── History ─────────────────────────────────────────────────────────────────

async def log_history(user_id: int, entry: dict) -> None:
    entry["ts"] = int(datetime.now(timezone.utc).timestamp())
    await _ref(user_id).collection("history").add(entry)


async def get_history(user_id: int, limit: int = 15) -> list[dict]:
    col = (
        _ref(user_id)
        .collection("history")
        .order_by("ts", direction=Query.DESCENDING)
        .limit(limit)
    )
    return [doc.to_dict() async for doc in col.stream()]


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

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        old_xp = int((doc.to_dict() or {}).get("xp", 0))
        new_xp = old_xp + xp_gain
        t.set(ref, {"xp": new_xp}, merge=True)
        return old_xp, new_xp

    old_xp, new_xp = await _txn(_get_db().transaction())
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

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        ach = list((doc.to_dict() or {}).get("ach", []))
        if key in ach:
            return False
        ach.append(key)
        t.set(ref, {"ach": ach}, merge=True)
        return True

    return await _txn(_get_db().transaction())


async def get_achievements(user_id: int) -> list[str]:
    doc = await _ref(user_id).get()
    return list((doc.to_dict() or {}).get("ach", []))


# ── Daily Claim ───────────────────────────────────────────────────────────────

_DAILY_BASE = 1_000
_DAILY_MAX  = 8_000


async def try_daily(user_id: int) -> tuple[int, int] | None | bool:
    """Claim daily reward. Returns (reward, streak), None if too soon, or False if house is empty."""
    now = int(datetime.now(timezone.utc).timestamp())
    user_ref = _ref(user_id)
    house_ref = _house_ref()

    @async_transactional
    async def _txn(t):
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
            raise _AbortHouseBroke()
        wallet = int(d.get("wallet", 0))
        t.set(user_ref, {"wallet": wallet + actual, "last_daily": now, "daily_streak": streak}, merge=True)
        t.set(house_ref, {
            "balance": house_bal - actual,
            "total_out": int(hd.get("total_out", 0)) + actual,
        }, merge=True)
        return actual, streak

    try:
        actual, streak = await _txn(_get_db().transaction())
    except _AbortHouseBroke:
        return False
    except _Abort:
        return None

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

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        pool = int((doc.to_dict() or {}).get("pool", _JACKPOT_SEED))
        new_pool = pool + amount
        t.set(ref, {"pool": new_pool}, merge=True)
        return new_pool

    return await _txn(_get_db().transaction())


async def get_user_luck(user_id: int) -> float:
    doc = await _ref(user_id).get()
    return float((doc.to_dict() or {}).get("luck", 1.0))


async def set_user_luck(user_id: int, luck: float) -> None:
    await _ref(user_id).set({"luck": max(0.0, luck)}, merge=True)


async def trigger_jackpot() -> int:
    """Claim jackpot pool, reset to seed. Returns actual paid from house."""
    ref = _jackpot_ref()

    @async_transactional
    async def _txn(t):
        doc = await ref.get(transaction=t)
        pool = int((doc.to_dict() or {}).get("pool", _JACKPOT_SEED))
        t.set(ref, {"pool": _JACKPOT_SEED}, merge=True)
        return pool

    pool = await _txn(_get_db().transaction())
    return await house_payout(pool)


# ── Guardian / Bank Health ────────────────────────────────────────────────────

_BANK_FLOOR = 500_000  # absolute minimum target balance


_MIN_HOUSE_TO_GAMBLE = 100_000  # บาทต่ำสุดที่คลังต้องมีก่อนเปิดให้เล่น


async def house_can_pay_games() -> bool:
    """Return True if house can pay. Auto-borrows if below threshold and under debt ceiling."""
    bal = await get_house_balance()
    if bal >= _MIN_HOUSE_TO_GAMBLE:
        return True
    return await house_auto_borrow(_MIN_HOUSE_TO_GAMBLE - bal)


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


# ── User Loans ────────────────────────────────────────────────────────────────

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
    limit     = calc_loan_limit(level, deposited)
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

    @async_transactional
    async def _txn(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _AbortNoRecipient()
        ud = u_doc.to_dict() or {}
        hd = h_doc.to_dict() or {}
        loan_bal  = int(ud.get("loan_balance", 0))
        deposited = int(ud.get("deposited", ud.get("bank", 0)))
        level     = xp_to_level(int(ud.get("xp", 0)))
        limit     = ai_approved if ai_approved > 0 else calc_loan_limit(level, deposited)
        available = max(limit - loan_bal, 0)
        if amount > available:
            raise _AbortLimit()
        if int(hd.get("balance", 0)) < amount:
            raise _AbortHouseBroke()
        new_fields: dict = {
            "wallet":       int(ud.get("wallet", 0)) + amount,
            "loan_balance": loan_bal + amount,
        }
        if loan_bal == 0:          # first draw — record when the debt started
            new_fields["loan_taken_at"] = now
        t.set(user_ref, new_fields, merge=True)
        t.set(house_ref, {
            "balance":   int(hd.get("balance", 0)) - amount,
            "total_out": int(hd.get("total_out", 0)) + amount,
        }, merge=True)

    try:
        await _txn(_get_db().transaction())
    except _AbortNoRecipient:
        return "ยังไม่มีบัญชี"
    except _AbortLimit:
        info = await get_loan_info(user_id)
        return f"วงเงินที่กู้ได้เหลือ **{info['available']:,}** 🪙"
    except _Abort:
        return "คลังหลวงไม่มีเงินให้กู้ตอนนี้"

    await log_history(user_id, {"cmd": "loan", "amount": amount, "net": amount})
    return None


async def repay_loan(user_id: int, amount: int) -> tuple[int, str | None]:
    """Repay loan. Returns (actual_repaid, error_str). error_str is None on success."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0, "จำนวนต้องมากกว่า 0"
    user_ref  = _ref(user_id)
    house_ref = _house_ref()
    actual_repaid = 0

    @async_transactional
    async def _txn(t):
        u_doc = await user_ref.get(transaction=t)
        h_doc = await house_ref.get(transaction=t)
        if not u_doc.exists:
            raise _AbortNoRecipient()
        ud = u_doc.to_dict() or {}
        hd = h_doc.to_dict() or {}
        loan_bal = int(ud.get("loan_balance", 0))
        wallet   = int(ud.get("wallet", 0))
        if loan_bal <= 0:
            raise _AbortNoRecipient()
        actual = min(amount, loan_bal, wallet)
        if actual <= 0:
            raise _Abort()
        t.set(user_ref, {
            "wallet":       wallet - actual,
            "loan_balance": loan_bal - actual,
        }, merge=True)
        t.set(house_ref, {
            "balance":  int(hd.get("balance", 0)) + actual,
            "total_in": int(hd.get("total_in", 0)) + actual,
        }, merge=True)
        return actual

    try:
        actual_repaid = await _txn(_get_db().transaction())
    except _AbortNoRecipient:
        return 0, "ไม่มียอดหนี้ที่ต้องชำระ"
    except _Abort:
        return 0, "เงินในกระเป๋าไม่พอ"

    await log_history(user_id, {"cmd": "repay", "amount": actual_repaid, "net": -actual_repaid})
    return actual_repaid, None


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

        @async_transactional
        async def _txn(t, _ref=ref, _now=now):
            snap = await _ref.get(transaction=t)
            sd   = snap.to_dict() or {}
            lb   = int(sd.get("loan_balance", 0))
            if lb <= 0 or _now - int(sd.get("last_loan_interest", 0)) < 82_800:
                raise _Abort()
            charged = max(int(lb * _LOAN_INTEREST_RATE), 10)
            t.set(_ref, {"loan_balance": lb + charged, "last_loan_interest": _now}, merge=True)
            return charged

        try:
            charged = await _txn(_get_db().transaction())
            await log_history(uid, {"cmd": "loan_interest", "amount": charged, "net": -charged})
            users_charged += 1
            total_interest += charged
        except _Abort:
            pass

    return users_charged, total_interest


async def get_total_outstanding_loans() -> int:
    """Sum of all users' current loan_balance (principal + accrued interest)."""
    total = 0
    async for doc in _get_db().collection("users").stream():
        d = doc.to_dict() or {}
        total += int(d.get("loan_balance", 0))
    return total


# ── House Debt ─────────────────────────────────────────────────────────────────

_HOUSE_DEBT_CEILING = 50_000_000  # max the house can owe before games are blocked
_HOUSE_BORROW_CHUNK = 1_000_000   # borrow in 1M increments


def _debt_ref():
    return _get_db().collection("system").document("debt")


async def get_house_debt() -> int:
    doc = await _debt_ref().get()
    return int((doc.to_dict() or {}).get("amount", 0))


async def house_auto_borrow(needed: int) -> bool:
    """Borrow enough to cover `needed`. Returns True if successful."""
    needed = _positive_amount(needed)
    if needed is None:
        return True
    debt_ref  = _debt_ref()
    house_ref = _house_ref()

    @async_transactional
    async def _txn(t):
        d_doc = await debt_ref.get(transaction=t)
        current_debt = int((d_doc.to_dict() or {}).get("amount", 0))
        if current_debt >= _HOUSE_DEBT_CEILING:
            raise _Abort()
        chunks  = (needed + _HOUSE_BORROW_CHUNK - 1) // _HOUSE_BORROW_CHUNK
        borrow  = min(chunks * _HOUSE_BORROW_CHUNK, _HOUSE_DEBT_CEILING - current_debt)
        if borrow <= 0:
            raise _Abort()
        h_doc = await house_ref.get(transaction=t)
        hd = h_doc.to_dict() or {}
        t.set(house_ref, {
            "balance":  int(hd.get("balance", 0)) + borrow,
            "total_in": int(hd.get("total_in", 0)) + borrow,
        }, merge=True)
        t.set(debt_ref, {"amount": current_debt + borrow}, merge=True)
        return borrow

    try:
        borrow_amount = await _txn(_get_db().transaction())
        logger.info("House auto-borrowed %d coins (ceiling %d)", borrow_amount, _HOUSE_DEBT_CEILING)
        return True
    except _Abort:
        return False


async def house_repay_debt(amount: int) -> int:
    """Repay up to `amount` from house balance. Returns actual repaid."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0
    debt_ref  = _debt_ref()
    house_ref = _house_ref()

    @async_transactional
    async def _txn(t):
        d_doc = await debt_ref.get(transaction=t)
        current_debt = int((d_doc.to_dict() or {}).get("amount", 0))
        if current_debt <= 0:
            raise _Abort()
        h_doc = await house_ref.get(transaction=t)
        hd = h_doc.to_dict() or {}
        house_bal = int(hd.get("balance", 0))
        repayable = max(house_bal - _BANK_FLOOR, 0)   # keep floor buffer
        actual = min(amount, current_debt, repayable)
        if actual <= 0:
            raise _Abort()
        t.set(house_ref, {
            "balance":   house_bal - actual,
            "total_out": int(hd.get("total_out", 0)) + actual,
        }, merge=True)
        t.set(debt_ref, {"amount": current_debt - actual}, merge=True)
        return actual

    try:
        return await _txn(_get_db().transaction())
    except _Abort:
        return 0


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
    from .ai import call_ai

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

        @async_transactional
        async def _txn(t, _ur=ref, _hr=house_ref, _take=take):
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

        try:
            collected = await _txn(_get_db().transaction())
            await log_history(uid, {"cmd": "guardian_collect", "amount": collected, "net": -collected})
            users_hit += 1
            total_collected += collected
        except _Abort:
            pass

    return users_hit, total_collected
