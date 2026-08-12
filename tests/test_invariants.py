"""Invariant tests: the strongest guarantee the bank must hold.

1. **Money conservation** — across every operation the total (user wallets +
   house balance − house debt) never changes. ``deposited`` is a claim on the
   house, so counting it would double-count. Money can only enter the system
   through ``house_auto_borrow`` (debt-backed), so the adjusted total must stay
   *exactly* constant through deposits, withdraws, transfers, loans, interest,
   daily claims, and force collection.
2. **No negative balances** — wallets, deposits, loans and house fields never
   go below zero.
3. **Atomicity** — every *failed* operation leaves ALL state untouched.
"""

import asyncio

import bobcoin.bank.core as bank_core
from bobcoin.bank import (
    accrue_loan_interest,
    charge_wallet,
    get_bank_data,
    guardian_force_collect,
    house_auto_borrow,
    house_receive,
    house_repay_debt,
    open_account,
    pay_interest_all,
    repay_loan,
    take_loan,
    transfer_to_user,
    try_daily,
    update_bank,
    user_deposit,
    user_withdraw,
)


class _User:
    def __init__(self, uid):
        self.id = uid


def run(coro):
    return asyncio.run(coro)


def _accounting(store):
    """Total real money: user wallets + house − debt.

    Note: the user's ``deposited`` field is deliberately NOT counted — it is a
    claim on house balance, and the coins themselves already sit in
    ``system/bank``. Counting both would double-count deposits.
    """
    total = 0
    for key, d in store.items():
        if key.startswith("users/"):
            total += int(d.get("wallet", 0))
        elif key == "system/bank":
            total += int(d.get("balance", 0))
        elif key == "system/debt":
            total -= int(d.get("amount", 0))
    return total


def _no_negative_balances(store) -> bool:
    for key, d in store.items():
        if key.startswith("users/"):
            for f in ("wallet", "deposited", "loan_balance"):
                if int(d.get(f, 0)) < 0:
                    return False
        elif key in ("system/bank", "system/debt", "system/jackpot"):
            for f in ("balance", "amount", "total_in", "total_out", "pool"):
                if int(d.get(f, 0)) < 0:
                    return False
    return True


def _snapshot(store):
    return {k: dict(v) for k, v in store.items()}


def test_money_conservation_through_full_economy_flow():
    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        baseline = _accounting(store)

        # register + fund 3 users
        for uid in (1, 2, 3):
            await open_account(_User(uid))
            await update_bank(uid, 100_000)
        assert _accounting(store) == baseline + 300_000
        after_funding = _accounting(store)

        # deposits
        await user_deposit(_User(1), 50_000)
        await user_deposit(_User(2), 20_000)
        assert _accounting(store) == after_funding

        # transfer
        await transfer_to_user(_User(1), _User(3), 5_000)
        assert _accounting(store) == after_funding

        # loans + partial repay
        await take_loan(1, 30_000)
        await take_loan(3, 10_000)
        await repay_loan(1, 5_000)
        assert _accounting(store) == after_funding

        # daily claims (house is rich)
        for uid in (2, 3):
            await try_daily(uid)
        assert _accounting(store) == after_funding

        # interest cycle
        await pay_interest_all()
        await accrue_loan_interest()
        assert _accounting(store) == after_funding

        # force collection pulls from debtor wallets
        await guardian_force_collect(0.10)
        assert _accounting(store) == after_funding

        # debt creation + repayment nets out
        await house_auto_borrow(2_000_000)
        await house_repay_debt(1_000_000)
        assert _accounting(store) == after_funding

        # finally: never a negative balance anywhere
        assert _no_negative_balances(store)
    run(scenario())


def test_failed_operations_are_atomic():
    """Every rejected operation must leave ALL accounts byte-identical."""
    async def scenario():
        store = bank_core._db._store
        await house_receive(1_000_000)
        await open_account(_User(1))
        await open_account(_User(2))
        await update_bank(_User(1), 1_000)

        # 1. deposit more than wallet
        before = _snapshot(store)
        assert await user_deposit(_User(1), 99_999) is None
        assert _snapshot(store) == before

        # 2. withdraw more than deposited
        before = _snapshot(store)
        assert await user_withdraw(_User(1), 99_999) is None
        assert _snapshot(store) == before

        # 3. transfer more than wallet
        before = _snapshot(store)
        assert await transfer_to_user(_User(1), _User(2), 99_999) is None
        assert _snapshot(store) == before

        # 4. loan over the limit
        before = _snapshot(store)
        assert await take_loan(1, 10_000_000) is not None
        assert _snapshot(store) == before

        # 5. repay without a loan
        before = _snapshot(store)
        actual, err = await repay_loan(2, 100)
        assert actual == 0 and err is not None
        assert _snapshot(store) == before

        # 6. charge below zero
        before = _snapshot(store)
        assert await charge_wallet(_User(2), 50) is None
        assert _snapshot(store) == before
    run(scenario())


def test_withdraw_house_broke_is_atomic():
    async def scenario():
        store = bank_core._db._store
        await house_receive(500)
        await open_account(_User(1))
        await update_bank(_User(1), 5_000)
        await user_deposit(_User(1), 5_000)
        store["system/bank"]["balance"] = 100  # house drained externally

        before = _snapshot(store)
        assert await user_withdraw(_User(1), 1_000) is False
        assert _snapshot(store) == before  # nothing moved despite the failure
    run(scenario())


def test_loan_rejected_when_house_empty_is_atomic():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        before = _snapshot(store)
        assert await take_loan(1, 1_000) is not None
        assert _snapshot(store) == before
    run(scenario())


def test_daily_claim_when_house_empty_is_atomic():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        before = _snapshot(store)
        assert await try_daily(1) is False
        assert _snapshot(store) == before
    run(scenario())


def test_total_accounting_matches_bank_data_view():
    """Sanity: get_bank_data (the leaderboard source) matches raw store."""
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        await open_account(_User(2))
        await update_bank(1, 1_000)
        await update_bank(2, 500)
        data = await get_bank_data()
        assert sum(int(d["wallet"]) for d in data.values()) == 1_500
        assert len(data) == 2
        assert _accounting(store) == 1_500  # no house money yet
    run(scenario())
