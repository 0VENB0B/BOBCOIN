"""Core bank infrastructure: Firestore access, atomic transactions, accounts,
wallet/house money movement and history.

Every money-touching operation goes through the atomic ``_in_txn`` helper so a
single place owns transaction retry/abort semantics.
"""

import logging
import os
import time
from datetime import UTC, datetime

from google.cloud.firestore import AsyncClient, Query, async_transactional

logger = logging.getLogger("bobcoin.bank")

_db: AsyncClient | None = None


def _get_db() -> AsyncClient:
    global _db
    if _db is None:
        _db = AsyncClient(project=os.getenv("FIREBASE_PROJECT_ID"))
    return _db


# ── Read cache (TTL) ──────────────────────────────────────────────────────────
# Full-collection scans (leaderboard, outstanding loans) are expensive on
# Firestore. Cache hot reads for a short window; 60s staleness is fine for
# leaderboards/stats, while all money-mutating paths stay uncached and atomic.

_cache: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 60.0


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _cache_set(key: str, value) -> None:
    _cache[key] = (time.monotonic(), value)


def _ref(user_id: int):
    return _get_db().collection("users").document(str(user_id))


_BANK_FLOOR = 500_000  # absolute minimum target balance for the house


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
    """Abort a transaction. ``value`` is what the caller should return (default None)."""

    def __init__(self, value=None):
        super().__init__()
        self.value = value


async def _in_txn(work):
    """Run ``work(transaction)`` atomically. Returns its result, or the value of
    any ``_Abort`` raised inside (None if unspecified)."""
    @async_transactional
    async def _run(t):
        return await work(t)

    try:
        return await _run(_get_db().transaction())
    except _Abort as e:
        return e.value


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
    """All user data (cached 60s). Only used for read-only views (leaderboard)."""
    cached = _cache_get("bank_data")
    if cached is not None:
        return cached
    result = {}
    async for doc in _get_db().collection("users").stream():
        result[doc.id] = doc.to_dict()
    _cache_set("bank_data", result)
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

    async def _work(t):
        doc = await ref.get(transaction=t)
        if not doc.exists:
            raise _Abort()
        w, d = _parse(doc.to_dict())
        new_w = w + change
        if new_w < 0:
            raise _Abort()
        t.set(ref, {"wallet": new_w, "deposited": d}, merge=True)
        return [new_w, d]

    return await _in_txn(_work)


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

    async def _work(t):
        doc = await ref.get(transaction=t)
        d = doc.to_dict() or {}
        new_bal = int(d.get("balance", 0)) + amount
        t.set(ref, {"balance": new_bal, "total_in": int(d.get("total_in", 0)) + amount}, merge=True)
        return new_bal

    return await _in_txn(_work)


async def house_payout(amount: int) -> int:
    """House pays winnings. Returns actual amount paid (capped at balance)."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0
    ref = _house_ref()

    async def _work(t):
        doc = await ref.get(transaction=t)
        d = doc.to_dict() or {}
        bal = int(d.get("balance", 0))
        actual = min(amount, bal)
        t.set(ref, {"balance": bal - actual, "total_out": int(d.get("total_out", 0)) + actual}, merge=True)
        return actual

    return await _in_txn(_work)


# ── Deposit / Withdraw (user ↔ central bank, atomic 2-doc) ─────────────────

async def user_deposit(user, amount: int) -> list[int] | None:
    """wallet → central bank. Returns [new_wallet, new_deposited] or None."""
    amount = _positive_amount(amount)
    if amount is None:
        return None
    user_ref  = _ref(user.id)
    house_ref = _house_ref()

    async def _work(t):
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

    return await _in_txn(_work)


# Returns: [wallet, deposited] | None (not enough deposited) | False (house broke)
async def user_withdraw(user, amount: int) -> list[int] | bool | None:
    """central bank → wallet. Returns [new_wallet, new_deposited], None, or False."""
    amount = _positive_amount(amount)
    if amount is None:
        return None
    user_ref  = _ref(user.id)
    house_ref = _house_ref()

    async def _work(t):
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
            raise _Abort(False)   # house broke → caller gets False
        t.set(user_ref,  {"wallet": w + amount, "deposited": d - amount}, merge=True)
        t.set(house_ref, {"balance": h_bal - amount, "total_out": int(hd.get("total_out", 0)) + amount}, merge=True)
        return [w + amount, d - amount]

    return await _in_txn(_work)


# ── Transfer between users ──────────────────────────────────────────────────

# Returns: int (remaining wallet) | None (insufficient funds) | False (recipient not registered)
async def transfer_to_user(sender, recipient, amount: int) -> int | bool | None:
    amount = _positive_amount(amount)
    if amount is None:
        return None
    sender_ref    = _ref(sender.id)
    recipient_ref = _ref(recipient.id)

    async def _work(t):
        r_doc = await recipient_ref.get(transaction=t)
        if not r_doc.exists:
            raise _Abort(False)   # recipient not registered
        s_doc = await sender_ref.get(transaction=t)
        if not s_doc.exists:
            raise _Abort()
        s_w, s_d = _parse(s_doc.to_dict())
        if s_w < amount:
            raise _Abort()
        r_w, r_d = _parse(r_doc.to_dict())
        t.set(sender_ref,    {"wallet": s_w - amount, "deposited": s_d}, merge=True)
        t.set(recipient_ref, {"wallet": r_w + amount, "deposited": r_d}, merge=True)
        return s_w - amount

    return await _in_txn(_work)


# ── Cooldowns (persisted in the user doc — survive restarts) ──────────────

async def get_cooldown(user_id: int, key: str, duration: float) -> float:
    """Seconds remaining for a cooldown key (0 if none or expired).

    Persisted in the user's Firestore doc under ``cd`` so bot restarts don't
    reset cooldowns (previously in-RAM dicts wiped on restart, letting users
    re-rob / re-gamble instantly).
    """
    doc = await _ref(user_id).get()
    cd = (doc.to_dict() or {}).get("cd") or {}
    last = int(cd.get(key, 0))
    return max(duration - (time.time() - last), 0.0)


async def set_cooldown(user_id: int, key: str) -> None:
    ref = _ref(user_id)

    async def _work(t):
        doc = await ref.get(transaction=t)
        cd = (doc.to_dict() or {}).get("cd") or {}
        cd[key] = int(time.time())
        t.set(ref, {"cd": cd}, merge=True)

    await _in_txn(_work)


# ── Rob (atomic steal between users) ────────────────────────────────────────

async def rob_transfer(robber, target, amount: int) -> bool:
    """Atomically move ``amount`` from ``target``'s wallet to ``robber``'s wallet.

    One transaction — if the target lacks the funds (or either account is
    missing), nothing moves and False is returned. Replaces the old two-step
    charge + credit that could lose money to a race between transactions.
    """
    amount = _positive_amount(amount)
    if amount is None:
        return False
    robber_ref = _ref(robber if isinstance(robber, int) else robber.id)
    target_ref  = _ref(target if isinstance(target, int) else target.id)
    if robber_ref._key == target_ref._key:
        return False  # self-steal would otherwise create money (last write wins)

    async def _work(t):
        t_doc = await target_ref.get(transaction=t)
        if not t_doc.exists:
            raise _Abort()
        t_w, t_d = _parse(t_doc.to_dict())
        if t_w < amount:
            raise _Abort()
        r_doc = await robber_ref.get(transaction=t)
        if not r_doc.exists:
            raise _Abort()
        r_w, r_d = _parse(r_doc.to_dict())
        t.set(target_ref, {"wallet": t_w - amount, "deposited": t_d}, merge=True)
        t.set(robber_ref, {"wallet": r_w + amount, "deposited": r_d}, merge=True)
        return True

    return await _in_txn(_work) or False


# ── Transfer relations (anti self-farming) ────────────────────────────────

async def has_transfer_relation(a_id: int, b_id: int, limit: int = 30) -> bool:
    """True if a $give ever passed between these two users (either direction).

    Used by ``$rob`` to block self-farming: a user funding an alt with $give
    and then robbing that alt would otherwise launder money risk-free.
    """
    for uid, other in ((a_id, b_id), (b_id, a_id)):
        for e in await get_history(uid, limit=limit):
            if e.get("cmd") not in ("give", "receive"):
                continue
            if str(e.get("to_id") or e.get("from_id") or "") == str(other):
                return True
    return False


# ── History ─────────────────────────────────────────────────────────────────

async def log_history(user_id: int, entry: dict) -> None:
    entry["ts"] = int(datetime.now(UTC).timestamp())
    await _ref(user_id).collection("history").add(entry)


async def get_history(user_id: int, limit: int = 15) -> list[dict]:
    col = (
        _ref(user_id)
        .collection("history")
        .order_by("ts", direction=Query.DESCENDING)
        .limit(limit)
    )
    return [doc.to_dict() async for doc in col.stream()]
