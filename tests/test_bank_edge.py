"""Deep edge-case tests for the bank package (core / rewards / loans / debt).

These target every branch and boundary that the happy-path tests don't reach:
invalid inputs, missing accounts, partial payouts, budget caps, cache expiry
and the transaction abort helper itself.
"""

import asyncio
import time

import pytest

import bobcoin.ai
import bobcoin.bank.core as bank_core
from bobcoin.bank import (
    _positive_amount,
    accrue_loan_interest,
    add_xp,
    ai_loan_limit,
    pay_interest_all,
    calc_interest,
    calc_loan_limit,
    charge_wallet,
    contribute_jackpot,
    get_achievements,
    get_bank_data,
    get_balance,
    get_history,
    get_house_balance,
    get_house_debt,
    get_jackpot_pool,
    get_cooldown,
    get_loan_info,
    grant_achievement,
    has_transfer_relation,
    house_auto_borrow,
    house_payout,
    house_receive,
    house_repay_debt,
    is_registered,
    log_history,
    open_account,
    repay_loan,
    rob_transfer,
    set_cooldown,
    take_loan,
    transfer_to_user,
    trigger_jackpot,
    try_daily,
    update_bank,
    user_deposit,
    user_withdraw,
    xp_to_level,
)


class _User:
    def __init__(self, uid):
        self.id = uid


def run(coro):
    return asyncio.run(coro)


# ── Low-level core helpers ───────────────────────────────────────────────

def test_get_db_lazy_initialization():
    saved = bank_core._db
    bank_core._db = None
    try:
        db = bank_core._get_db()
        assert db is bank_core._db          # cached for next calls
        assert type(db).__name__ == "FakeClient"
    finally:
        bank_core._db = saved


def test_parse_falsy_data():
    assert bank_core._parse(None) == (0, 0)
    assert bank_core._parse({}) == (0, 0)
    assert bank_core._parse(False) == (0, 0)


def test_get_balance_missing_account():
    async def scenario():
        assert await get_balance(_User(99)) == [0, 0]
    run(scenario())


# ── Transaction helper ───────────────────────────────────────────────────

def test_in_txn_returns_work_result():
    async def scenario():
        async def work(t):
            return "ok"
        assert await bank_core._in_txn(work) == "ok"
    run(scenario())


def test_in_txn_abort_passes_value():
    async def scenario():
        async def work(t):
            raise bank_core._Abort("custom-value")
        assert await bank_core._in_txn(work) == "custom-value"

    run(scenario())


def test_in_txn_abort_without_value_returns_none():
    async def scenario():
        async def work(t):
            raise bank_core._Abort()
        assert await bank_core._in_txn(work) is None
    run(scenario())


# ── _positive_amount / low-level parsers ────────────────────────────────

def test_positive_amount_contract():
    assert _positive_amount(100) == 100
    assert _positive_amount("100") == 100
    assert _positive_amount(0) is None
    assert _positive_amount(-5) is None
    assert _positive_amount("abc") is None
    assert _positive_amount(None) is None
    assert _positive_amount("") is None


def test_parse_legacy_bank_field_migration():
    async def scenario():
        # old accounts stored money under 'bank' instead of 'deposited'
        store = bank_core._db._store
        store["users/1"] = {"wallet": 10, "bank": 90}
        assert await get_balance(_User(1)) == [10, 90]
        # deposit path keeps working with migrated field
        await update_bank(_User(1), 5)
        assert (await get_balance(_User(1)))[0] == 15
    run(scenario())


def test_open_account_with_extra_fields():
    async def scenario():
        assert await open_account(_User(1), extra={"xp": 100, "likes": []}) is True
        d = bank_core._db._store["users/1"]
        assert d["xp"] == 100 and d["wallet"] == 0 and d["deposited"] == 0
    run(scenario())


def test_update_bank_accepts_int_user_id():
    async def scenario():
        await open_account(_User(1))
        assert await update_bank(1, 500) == [500, 0]
        assert await is_registered(1)
    run(scenario())


def test_update_bank_missing_account():
    async def scenario():
        assert await update_bank(_User(99), 500) is None
    run(scenario())


def test_charge_wallet_invalid_amount():
    async def scenario():
        await open_account(_User(1))
        await update_bank(_User(1), 100)
        assert await charge_wallet(_User(1), "abc") is None
        assert await charge_wallet(_User(1), 0) is None
        assert (await get_balance(_User(1)))[0] == 100
    run(scenario())


def test_house_receive_invalid_returns_balance_unchanged():
    async def scenario():
        await house_receive(1000)
        assert await house_receive("abc") == 1000
        assert await house_receive(0) == 1000
        assert await house_receive(-10) == 1000
        assert await get_house_balance() == 1000
    run(scenario())


def test_house_payout_invalid_and_cap():
    async def scenario():
        assert await house_payout("abc") == 0
        await house_receive(1000)
        # payout capped at available balance
        assert await house_payout(5000) == 1000
        assert await get_house_balance() == 0
        # empty house pays nothing
        assert await house_payout(100) == 0
    run(scenario())


# ── Deposit / Withdraw edge cases ───────────────────────────────────────

def test_deposit_requires_account():
    async def scenario():
        assert await user_deposit(_User(1), 100) is None
    run(scenario())


def test_deposit_invalid_amount():
    async def scenario():
        await open_account(_User(1))
        assert await user_deposit(_User(1), "abc") is None
        assert await user_deposit(_User(1), -5) is None
    run(scenario())


def test_withdraw_requires_account_and_invalid():
    async def scenario():
        assert await user_withdraw(_User(1), 100) is None
        await open_account(_User(1))
        assert await user_withdraw(_User(1), "abc") is None
    run(scenario())


def test_transfer_invalid_amount_and_sender_missing():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        assert await transfer_to_user(_User(1), _User(2), "abc") is None
        assert await transfer_to_user(_User(9), _User(2), 100) is None  # sender missing
    run(scenario())


# ── History ─────────────────────────────────────────────────────────────

def test_history_log_and_query():
    async def scenario():
        await open_account(_User(1))
        assert await get_history(1) == []
        await log_history(1, {"cmd": "flip", "net": 50})
        await log_history(1, {"cmd": "slot", "net": -20})
        entries = await get_history(1, limit=5)
        assert len(entries) == 2
        cmds = {e["cmd"] for e in entries}
        assert cmds == {"flip", "slot"}
        assert all(e["ts"] > 0 for e in entries)
        # limit respected
        assert len(await get_history(1, limit=1)) == 1
    run(scenario())


def test_history_is_per_user():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        await log_history(1, {"cmd": "flip"})
        assert len(await get_history(2)) == 0
    run(scenario())


# ── Interest edges ──────────────────────────────────────────────────────

def test_calc_interest_tier_boundaries():
    assert calc_interest(99_999) == 99            # low tier 0.1%
    assert calc_interest(100_000) == 150          # switches to 0.15%
    assert calc_interest(999_999) == 1499         # 0.15% floor int
    assert calc_interest(1_000_000) == 2000       # switches to 0.2%
    assert calc_interest(-50) == 0
    assert calc_interest(1) == 10                 # floor of 10


def test_pay_interest_no_depositors():
    async def scenario():
        assert await pay_interest_all() == (0, 0)
    run(scenario())


def test_pay_interest_zero_budget():
    async def scenario():
        await open_account(_User(1))
        await update_bank(_User(1), 1000)
        await user_deposit(_User(1), 1000)
        # drain the house after deposit → budget = 0 → nothing paid
        bank_core._db._store["system/bank"]["balance"] = 0
        assert await pay_interest_all() == (0, 0)
    run(scenario())


def test_pay_interest_budget_cap_stops_early():
    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        store["system/bank"]["guardian_interest_cap"] = 0.00001  # budget = 100
        for uid in range(1, 21):
            await open_account(_User(uid))
            await update_bank(_User(uid), 10_000)
            await user_deposit(_User(uid), 10_000)  # interest 10 each (floor)
        n, total = await pay_interest_all()
        assert total == 100
        assert n == 10  # 11th would push 110 > 100 budget → break
    run(scenario())


def test_pay_interest_house_depleted_breaks():
    async def scenario():
        store = bank_core._db._store
        await house_receive(10)                      # tiny house
        store["system/bank"]["guardian_interest_cap"] = 2.0  # budget = 20
        for uid in range(1, 6):
            await open_account(_User(uid))
            await update_bank(_User(uid), 10_000)
            await user_deposit(_User(uid), 10_000)
        # deposits filled the house — drain it back to 10 to simulate a poor house
        store["system/bank"]["balance"] = 10
        n, total = await pay_interest_all()
        assert (n, total) == (1, 10)  # 2nd user: payout 0 → break
    run(scenario())


# ── XP / achievements edges ─────────────────────────────────────────────

def test_xp_to_level_negative_and_boundaries():
    assert xp_to_level(-100) == 0
    assert xp_to_level(9) == 0
    assert xp_to_level(10) == 1
    assert xp_to_level(39) == 1
    assert xp_to_level(40) == 2


def test_add_xp_creates_doc_on_the_fly():
    async def scenario():
        new_xp, new_level, leveled = await add_xp(999, 25)
        assert (new_xp, new_level, leveled) == (25, 1, True)
        # add_xp writes an xp-only doc (not a full registered account)
        assert await is_registered(999) is True
        assert await get_balance(_User(999)) == [0, 0]
    run(scenario())


def test_add_xp_negative_gain_characterizes_behavior():
    """Characterization: a negative gain pushes the raw xp field below zero.
    (The level stays sane because xp_to_level clamps.) If this is later
    considered a bug, this test is the place to update the contract."""
    async def scenario():
        await open_account(_User(1))
        await add_xp(1, 5)
        new_xp, new_level, leveled = await add_xp(1, -10)
        assert new_xp == -5            # raw field goes negative (current behaviour)
        assert new_level == 0          # but the level is clamped, never negative
        assert leveled is False
    run(scenario())


def test_achievements_missing_account_returns_empty():
    async def scenario():
        assert await get_achievements(777) == []
        assert await grant_achievement(777, "jackpot") is True
        assert await get_achievements(777) == ["jackpot"]
    run(scenario())


# ── Daily edges ─────────────────────────────────────────────────────────

def test_daily_requires_account():
    async def scenario():
        assert await try_daily(555) is None
    run(scenario())


def test_daily_capped_by_house_balance():
    async def scenario():
        await open_account(_User(1))
        await house_receive(100)          # less than any daily reward
        reward, streak = await try_daily(1)
        assert (reward, streak) == (100, 1)
        assert await get_house_balance() == 0
    run(scenario())


def test_daily_reward_bounds():
    async def scenario():
        await open_account(_User(1))
        await house_receive(10_000_000)
        best = 0
        for _ in range(5):
            reward, _ = await try_daily(1)
            best = max(best, reward)
            # force the cooldown to pass so we can claim again
            bank_core._db._store["users/1"]["last_daily"] = 0
        assert best <= 8_000  # _DAILY_MAX
    run(scenario())


# ── Jackpot edges ───────────────────────────────────────────────────────

def test_jackpot_default_pool_when_no_doc():
    async def scenario():
        assert await get_jackpot_pool() == 50_000  # seed
    run(scenario())


def test_contribute_jackpot_invalid_keeps_pool():
    async def scenario():
        assert await contribute_jackpot("abc") == 50_000
        assert await contribute_jackpot(-100) == 50_000
        assert await contribute_jackpot(0) == 50_000
    run(scenario())


def test_trigger_jackpot_pays_capped_at_house():
    async def scenario():
        await house_receive(1_000)
        await contribute_jackpot(5_000)
        assert await get_jackpot_pool() == 55_000
        paid = await trigger_jackpot()
        assert paid == 1_000                      # capped by house balance
        assert await get_jackpot_pool() == 50_000  # still reset
        assert await get_house_balance() == 0
    run(scenario())


# ── Loans edges ─────────────────────────────────────────────────────────

def test_calc_loan_limit_boundaries():
    assert calc_loan_limit(0, 0) == 50_000
    assert calc_loan_limit(1, 0) == 60_000
    assert calc_loan_limit(0, 100_000) == 80_000  # +30% of deposited
    assert calc_loan_limit(1000, 10_000_000_000) == 10_000_000  # hard cap


def test_loan_info_daily_interest_floor():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 1_000)
        info = await get_loan_info(1)
        assert info["daily_interest"] == 10  # max(3, 10) floor
        assert info["loan_balance"] == 1_000
        assert info["available"] == 50_000 - 1_000
    run(scenario())


def test_take_loan_invalid_and_missing_account():
    async def scenario():
        assert await take_loan(1, 0) is not None          # "จำนวนต้องมากกว่า 0"
        assert await take_loan(1, "abc") is not None
        assert await take_loan(99, 1000) is not None       # "ยังไม่มีบัญชี"
    run(scenario())


def test_take_loan_with_ai_approved_ceiling():
    async def scenario():
        await open_account(_User(1))
        await house_receive(10_000_000)
        # AI ceiling overrides static limit
        assert await take_loan(1, 5_000_000, ai_approved=8_000_000) is None
        info = await get_loan_info(1)
        assert info["loan_balance"] == 5_000_000
        assert info["loan_limit"] == 8_000_000        # AI ceiling is visible now
        assert info["available"] == 3_000_000
        # cannot exceed the AI ceiling
        assert await take_loan(1, 4_000_000, ai_approved=8_000_000) is not None
    run(scenario())


def test_ai_loan_ceiling_persisted_and_reset_on_full_repay():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        await house_receive(10_000_000)
        await take_loan(1, 5_000_000, ai_approved=8_000_000)
        assert store["users/1"]["ai_loan_ceiling"] == 8_000_000
        # partial repay keeps the AI headroom
        await repay_loan(1, 1_000_000)
        assert (await get_loan_info(1))["available"] == 8_000_000 - 4_000_000
        # full repayment resets the ceiling back to the static limit
        await update_bank(1, 10_000_000)
        await repay_loan(1, 4_000_000)
        info = await get_loan_info(1)
        assert info["loan_balance"] == 0
        assert info["loan_limit"] == calc_loan_limit(0, 0)  # static 50k, AI gone
    run(scenario())


def test_static_loan_never_writes_ai_ceiling():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 10_000)   # static path, ai_approved defaults 0
        assert "ai_loan_ceiling" not in store["users/1"]
    run(scenario())


def test_take_loan_second_draw_keeps_taken_at():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 10_000)
        first = (await get_loan_info(1))["loan_taken_at"]
        await take_loan(1, 10_000)
        assert (await get_loan_info(1))["loan_taken_at"] == first
    run(scenario())


def test_take_loan_over_limit_message_includes_available():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 40_000)
        err = await take_loan(1, 50_000)  # only 10k available
        assert err is not None and "10,000" in err
    run(scenario())


def test_repay_loan_invalid_and_missing_account():
    async def scenario():
        actual, err = await repay_loan(1, "abc")
        assert actual == 0 and err is not None
        actual, err = await repay_loan(99, 100)
        assert actual == 0 and err is not None
    run(scenario())


def test_repay_loan_insufficient_wallet():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 5_000)   # wallet has 5000 but loan == 5000
        # drain wallet
        await update_bank(_User(1), -5_000)
        actual, err = await repay_loan(1, 1_000)
        assert actual == 0 and err is not None  # "เงินในกระเป๋าไม่พอ"
        assert (await get_loan_info(1))["loan_balance"] == 5_000
    run(scenario())


def test_repay_more_than_loan_capped():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 1_000)
        await update_bank(_User(1), 50_000)
        actual, err = await repay_loan(1, 999_999)
        assert err is None and actual == 1_000
        assert (await get_loan_info(1))["loan_balance"] == 0
    run(scenario())


def test_accrue_loan_interest_floor_and_guard():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 100)   # tiny loan → floor 10 interest
        n, total = await accrue_loan_interest()
        assert (n, total) == (1, 10)
        # 23h guard: second call does nothing
        assert await accrue_loan_interest() == (0, 0)
    run(scenario())


def test_accrue_loan_interest_skips_non_borrowers():
    async def scenario():
        await open_account(_User(1))
        assert await accrue_loan_interest() == (0, 0)
    run(scenario())


def test_ai_loan_limit_house_too_small():
    """hard_ceil <= 0 → deny without calling the AI."""
    async def scenario():
        called = {"n": 0}

        async def _fake_ai(*a, **kw):
            called["n"] += 1
            return '{"approved": 999999}'

        bobcoin.ai.call_ai = _fake_ai
        await open_account(_User(1))
        # no house money → ceil is 0
        assert await ai_loan_limit(1, 500_000) == 0
        assert called["n"] == 0
    run(scenario())


def test_ai_loan_limit_approved_capped_to_hard_ceiling():
    async def scenario():
        async def _fake_ai(*a, **kw):
            return '{"approved": 900000}'

        bobcoin.ai.call_ai = _fake_ai
        await open_account(_User(1))
        await house_receive(10_000_000)   # 20% = 2M ceiling
        # requested 500k → ceil = min(500k, 2M) = 500k → AI's 900k capped to 500k
        assert await ai_loan_limit(1, 500_000) == 500_000
    run(scenario())


def test_ai_loan_limit_malformed_and_negative():
    async def scenario():
        async def _fake_ai(*a, **kw):
            return "not json at all"

        bobcoin.ai.call_ai = _fake_ai
        await open_account(_User(1))
        await house_receive(10_000_000)
        assert await ai_loan_limit(1, 500_000) == 0

        async def _fake_neg(*a, **kw):
            return '{"approved": -5}'

        bobcoin.ai.call_ai = _fake_neg
        assert await ai_loan_limit(1, 500_000) == 0
    run(scenario())


def test_ai_loan_limit_non_numeric_approved_field():
    """{"approved": "abc"} → int() raises → caught by except → deny (0)."""
    async def scenario():
        async def _fake_ai(*a, **kw):
            return '{"approved": "abc"}'

        bobcoin.ai.call_ai = _fake_ai
        await open_account(_User(1))
        await house_receive(10_000_000)
        assert await ai_loan_limit(1, 500_000) == 0
    run(scenario())


# ── House debt edges ────────────────────────────────────────────────────

def test_get_house_debt_default_zero():
    async def scenario():
        assert await get_house_debt() == 0
    run(scenario())


def test_auto_borrow_invalid_needed_is_success():
    async def scenario():
        assert await house_auto_borrow("abc") is True
        assert await get_house_debt() == 0
    run(scenario())


def test_auto_borrow_at_ceiling_fails():
    async def scenario():
        store = bank_core._db._store
        store["system/debt"] = {"amount": 50_000_000}  # at ceiling
        assert await house_auto_borrow(1_000) is False
        assert await get_house_debt() == 50_000_000
    run(scenario())


def test_auto_borrow_capped_at_ceiling():
    async def scenario():
        store = bank_core._db._store
        store["system/debt"] = {"amount": 49_500_000}
        assert await house_auto_borrow(5_000_000) is True
        assert await get_house_debt() == 50_000_000  # capped exactly at ceiling
        assert await get_house_balance() == 500_000
    run(scenario())


def test_repay_debt_invalid_and_no_debt():
    async def scenario():
        assert await house_repay_debt("abc") == 0
        assert await house_repay_debt(100) == 0
    run(scenario())


def test_repay_debt_keeps_floor_buffer():
    async def scenario():
        store = bank_core._db._store
        await house_receive(600_000)            # floor is 500k → only 100k repayable
        store["system/debt"] = {"amount": 300_000}
        assert await house_repay_debt(300_000) == 100_000
        assert await get_house_debt() == 200_000
        assert await get_house_balance() == 500_000  # never below floor
    run(scenario())


def test_repay_debt_below_floor_returns_zero():
    async def scenario():
        store = bank_core._db._store
        await house_receive(300_000)             # below the 500k floor
        store["system/debt"] = {"amount": 100_000}
        # repayable = max(300k - 500k, 0) = 0 → nothing can be repaid
        assert await house_repay_debt(100_000) == 0
        assert await get_house_debt() == 100_000  # untouched
        assert await get_house_balance() == 300_000
    run(scenario())


# ── Rob (atomic transfer) ───────────────────────────────────────────────

def test_rob_transfer_success():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        await update_bank(_User(2), 10_000)
        assert await rob_transfer(_User(1), _User(2), 2_500) is True
        assert (await get_balance(_User(1)))[0] == 2_500
        assert (await get_balance(_User(2)))[0] == 7_500
    run(scenario())


def test_rob_transfer_insufficient_is_atomic():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        await open_account(_User(2))
        await update_bank(_User(2), 500)
        before = {k: dict(v) for k, v in store.items()}
        assert await rob_transfer(_User(1), _User(2), 999) is False
        assert {k: dict(v) for k, v in store.items()} == before  # nothing moved
    run(scenario())


def test_rob_transfer_missing_accounts():
    async def scenario():
        await open_account(_User(1))
        assert await rob_transfer(_User(1), _User(99), 100) is False   # target missing
        assert await rob_transfer(_User(99), _User(1), 100) is False   # robber missing (target broke)
        # robber missing while target HAS funds → reaches the robber check
        await open_account(_User(2))
        await update_bank(_User(2), 500)
        assert await rob_transfer(_User(99), _User(2), 100) is False
        assert (await get_balance(_User(2)))[0] == 500  # untouched
        assert await rob_transfer(_User(1), _User(2), "abc") is False  # invalid amount
    run(scenario())


def test_rob_transfer_accepts_int_ids():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        await update_bank(2, 1_000)
        assert await rob_transfer(1, 2, 400) is True
        assert (await get_balance(_User(1)))[0] == 400
        assert (await get_balance(_User(2)))[0] == 600
    run(scenario())


def test_rob_transfer_self_steal_blocked():
    """Same-account rob would create money via last-write-wins — must be refused."""
    async def scenario():
        await open_account(_User(1))
        await update_bank(_User(1), 1_000)
        assert await rob_transfer(_User(1), _User(1), 500) is False
        assert (await get_balance(_User(1)))[0] == 1_000  # untouched
    run(scenario())


# ── Cooldowns (persisted) ───────────────────────────────────────────────

def test_cooldown_set_and_remaining():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        assert await get_cooldown(1, "rob_2", 7200) == 0.0   # never set
        await set_cooldown(1, "rob_2")
        remaining = await get_cooldown(1, "rob_2", 7200)
        assert 7190 <= remaining <= 7200
        # persisted in the user doc — a restart won't lose it
        assert store["users/1"]["cd"]["rob_2"] > 0
        # different key → no cooldown
        assert await get_cooldown(1, "rob_3", 7200) == 0.0
    run(scenario())


def test_cooldown_expires():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        await set_cooldown(1, "panel_slot")
        assert await get_cooldown(1, "panel_slot", 30) > 0
        # simulate time passing: backdate the stored timestamp
        store["users/1"]["cd"]["panel_slot"] = int(time.time()) - 60
        assert await get_cooldown(1, "panel_slot", 30) == 0.0
    run(scenario())


def test_cooldown_works_without_account():
    async def scenario():
        assert await get_cooldown(999, "any", 60) == 0.0   # no doc → no cooldown
        await set_cooldown(999, "any")                      # creates the doc
        assert await get_cooldown(999, "any", 60) > 0
    run(scenario())


# ── Transfer relations (anti self-farming) ───────────────────────────────

def test_has_transfer_relation_detects_give():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        # mimic what the $give command logs on both sides
        await log_history(1, {"cmd": "give", "amount": 100, "to_id": "2", "to_name": "B", "net": -100})
        await log_history(2, {"cmd": "receive", "amount": 100, "from_id": "1", "from_name": "A", "net": 100})
        assert await has_transfer_relation(1, 2) is True
        assert await has_transfer_relation(2, 1) is True   # symmetric
    run(scenario())


def test_has_transfer_relation_false_for_strangers():
    async def scenario():
        await open_account(_User(1))
        await open_account(_User(2))
        await open_account(_User(3))
        await log_history(1, {"cmd": "give", "amount": 100, "to_id": "2", "net": -100})
        assert await has_transfer_relation(1, 2) is True
        assert await has_transfer_relation(1, 3) is False
        assert await has_transfer_relation(99, 1) is False  # unknown user
    run(scenario())


def test_has_transfer_relation_ignores_non_transfer_history():
    async def scenario():
        await open_account(_User(1))
        await log_history(1, {"cmd": "slot", "net": 50})
        await log_history(1, {"cmd": "daily", "net": 100})
        assert await has_transfer_relation(1, 2) is False
    run(scenario())


# ── Cache edges ─────────────────────────────────────────────────────────

def test_cache_get_expired_returns_none():
    bank_core._cache["k"] = (time.monotonic() - 100, "stale")
    assert bank_core._cache_get("k") is None


def test_cache_get_missing_and_fresh():
    assert bank_core._cache_get("nope") is None
    bank_core._cache_set("k", "fresh")
    assert bank_core._cache_get("k") == "fresh"


def test_bank_data_cache_expiry_refetches():
    async def scenario():
        store = bank_core._db._store
        await open_account(_User(1))
        data = await get_bank_data()
        assert "1" in data
        # expire the cached entry manually → refetch sees new user
        bank_core._cache["bank_data"] = (time.monotonic() - 100, {"0": {}})
        store["users/2"] = {"wallet": 50}
        data = await get_bank_data()
        assert "2" in data and "0" not in data
    run(scenario())
