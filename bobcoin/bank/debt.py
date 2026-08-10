"""House debt: the central bank can borrow (create) coins up to a ceiling and
repays them when healthy."""

from .core import _BANK_FLOOR, _Abort, _get_db, _house_ref, _in_txn, _positive_amount, logger

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

    async def _work(t):
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

    borrowed = await _in_txn(_work)
    if borrowed is None:
        return False
    logger.info("House auto-borrowed %d coins (ceiling %d)", borrowed, _HOUSE_DEBT_CEILING)
    return True


async def house_repay_debt(amount: int) -> int:
    """Repay up to `amount` from house balance. Returns actual repaid."""
    amount = _positive_amount(amount)
    if amount is None:
        return 0
    debt_ref  = _debt_ref()
    house_ref = _house_ref()

    async def _work(t):
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

    # success always returns actual >= 1 (else _Abort), so `or 0` only maps abort → 0
    return await _in_txn(_work) or 0
