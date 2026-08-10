"""Tests for bobcoin.bank money logic, run against an in-memory fake Firestore.

All async tests are wrapped with asyncio.run() so we don't need pytest-asyncio.
"""

import asyncio
import time

from fake_firestore import FakeClient

import bobcoin.bank as bank
import bobcoin.bank.core as bank_core
from bobcoin.bank import (
    accrue_loan_interest,
    add_xp,
    calc_interest,
    charge_wallet,
    contribute_jackpot,
    get_achievements,
    get_balance,
    get_bank_data,
    get_game_stats,
    get_history,
    get_house_balance,
    get_house_data,
    get_house_debt,
    get_jackpot_pool,
    get_leaderboard,
    get_loan_info,
    get_total_outstanding_loans,
    grant_achievement,
    house_auto_borrow,
    house_receive,
    house_repay_debt,
    house_status_band,
    is_registered,
    max_bet_allowed,
    open_account,
    pay_interest_all,
    record_game_outcome,
    repay_loan,
    rob_transfer,
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


# ── Accounts ─────────────────────────────────────────────────────────────

def test_open_account_and_balance():
    async def scenario():
        u = _User(1)
        assert await open_account(u) is True
        assert await is_registered(1) is True
        assert await open_account(u) is False  # duplicate
        assert await get_balance(u) == [0, 0]
    run(scenario())


def test_update_bank_and_charge():
    async def scenario():
        u = _User(1)
        await open_account(u)
        assert await update_bank(u, 1000) == [1000, 0]
        assert await charge_wallet(u, 300) == [700, 0]
        assert await charge_wallet(u, 999) is None  # insufficient → no deduction
        assert await get_balance(u) == [700, 0]
    run(scenario())


def test_update_bank_never_negative():
    async def scenario():
        u = _User(1)
        await open_account(u)
        await update_bank(u, 100)
        assert await update_bank(u, -500) is None
        assert await get_balance(u) == [100, 0]
    run(scenario())


# ── Deposit / Withdraw ───────────────────────────────────────────────────

def test_deposit_flow():
    async def scenario():
        u = _User(1)
        await open_account(u)
        await update_bank(u, 5000)
        w, d = await user_deposit(u, 2000)
        assert [w, d] == [3000, 2000]
        assert (await get_house_data())["balance"] == 2000
        # insufficient wallet
        assert await user_deposit(u, 99_999) is None
    run(scenario())


def test_withdraw_flow():
    async def scenario():
        u = _User(1)
        await open_account(u)
        await update_bank(u, 5000)
        await user_deposit(u, 3000)
        w, d = await user_withdraw(u, 1000)
        assert [w, d] == [3000, 2000]
        # more than deposited
        assert await user_withdraw(u, 99_999) is None
    run(scenario())


def test_withdraw_house_broke():
    async def scenario():
        store = {}
        bank_core._db = FakeClient(store)
        u = _User(1)
        await open_account(u)
        await update_bank(u, 5000)
        await user_deposit(u, 5000)  # house has exactly 5000
        # house paid out elsewhere — now broke
        store["system/bank"]["balance"] = 100
        assert await user_withdraw(u, 5000) is False
        # recovers → withdraw works again
        store["system/bank"]["balance"] = 5000
        w, d = await user_withdraw(u, 2000)
        assert [w, d] == [2000, 3000]
    run(scenario())


# ── Transfer ─────────────────────────────────────────────────────────────

def test_transfer_between_users():
    async def scenario():
        a, b = _User(1), _User(2)
        await open_account(a)
        await update_bank(a, 1000)
        assert await transfer_to_user(a, b, 100) is False  # b not registered
        await open_account(b)
        remaining = await transfer_to_user(a, b, 300)
        assert remaining == 700
        assert await get_balance(b) == [300, 0]
        assert await transfer_to_user(a, b, 99_999) is None  # insufficient
        assert await get_balance(a) == [700, 0]
    run(scenario())


# ── Daily ────────────────────────────────────────────────────────────────

def test_daily_claim_and_cooldown():
    async def scenario():
        await open_account(_User(1))
        await house_receive(10_000_000)
        reward, streak = await try_daily(1)
        assert streak == 1 and reward > 0
        assert await try_daily(1) is None  # too soon
    run(scenario())


def test_daily_house_broke():
    async def scenario():
        await open_account(_User(1))
        assert await try_daily(1) is False  # no house money
    run(scenario())


def test_daily_streak_increment_and_reset():
    async def scenario():
        store = {}
        bank_core._db = FakeClient(store)
        await open_account(_User(1))
        await house_receive(10_000_000)
        _, s1 = await try_daily(1)
        assert s1 == 1
        # 25h later (within 48h) → streak grows
        store["users/1"]["last_daily"] = int(time.time()) - 90_000
        _, s2 = await try_daily(1)
        assert s2 == 2
        # > 48h gap → streak resets
        store["users/1"]["last_daily"] = int(time.time()) - 200_000
        _, s3 = await try_daily(1)
        assert s3 == 1
    run(scenario())


# ── Loans ────────────────────────────────────────────────────────────────

def test_loan_take_and_repay():
    async def scenario():
        u = _User(1)
        await open_account(u)
        await house_receive(1_000_000)
        assert await take_loan(1, 10_000) is None
        info = await get_loan_info(1)
        assert info["loan_balance"] == 10_000
        assert (await get_balance(u))[0] == 10_000
        # over available limit (base limit 50k at level 0)
        assert await take_loan(1, 10_000_000) is not None
        # repay partially
        actual, err = await repay_loan(1, 4_000)
        assert err is None and actual == 4_000
        assert (await get_loan_info(1))["loan_balance"] == 6_000
    run(scenario())


def test_loan_house_broke():
    async def scenario():
        await open_account(_User(1))
        err = await take_loan(1, 1_000)
        assert err is not None  # "คลังหลวงไม่มีเงินให้กู้ตอนนี้"
    run(scenario())


def test_repay_without_loan():
    async def scenario():
        await open_account(_User(1))
        actual, err = await repay_loan(1, 100)
        assert actual == 0 and err is not None
    run(scenario())


# ── Interest ─────────────────────────────────────────────────────────────

def test_calc_interest_tiers():
    assert calc_interest(0) == 0
    assert calc_interest(50_000) == 50          # 0.10%
    assert calc_interest(150_000) == 225        # 0.15%
    assert calc_interest(2_000_000) == 4_000    # 0.20%
    assert calc_interest(10) == 10              # floor: at least 10


def test_pay_interest_all():
    async def scenario():
        u1, u2 = _User(1), _User(2)
        await open_account(u1)
        await open_account(u2)
        await house_receive(1_000_000)
        for u in (u1, u2):
            await update_bank(u, 200_000)
            await user_deposit(u, 200_000)  # 200k deposited each
        n, total = await pay_interest_all()
        assert n == 2
        assert total == 600  # 300 each (0.15% of 200k)
        assert (await get_balance(u1))[0] == 300  # interest lands in wallet
        # second run: interest is already paid and balance unchanged
        n2, total2 = await pay_interest_all()
        assert n2 == 2 and total2 == 600
    run(scenario())


def test_accrue_loan_interest():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 10_000)
        n, total = await accrue_loan_interest()
        assert n == 1
        assert total == 30  # 0.3% of 10k
        # 23h guard prevents double charging
        n2, total2 = await accrue_loan_interest()
        assert n2 == 0 and total2 == 0
    run(scenario())


# ── XP / Levels / Achievements ──────────────────────────────────────────

def test_xp_to_level():
    assert xp_to_level(0) == 0
    assert xp_to_level(810) == 9    # 9²×10 = 810
    assert xp_to_level(999) == 9
    assert xp_to_level(1000) == 10


def test_add_xp_level_up():
    async def scenario():
        await open_account(_User(1))
        new_xp, new_level, leveled = await add_xp(1, 1000)
        assert (new_xp, new_level, leveled) == (1000, 10, True)
        new_xp, new_level, leveled = await add_xp(1, 10)
        assert (new_xp, new_level, leveled) == (1010, 10, False)
    run(scenario())


def test_achievements():
    async def scenario():
        await open_account(_User(1))
        assert await grant_achievement(1, "first_win") is True
        assert await grant_achievement(1, "first_win") is False  # dup
        assert await get_achievements(1) == ["first_win"]
    run(scenario())


# ── Jackpot / House debt ────────────────────────────────────────────────

def test_jackpot_pool():
    async def scenario():
        await house_receive(1_000_000)
        await contribute_jackpot(5_000)
        assert await get_jackpot_pool() == 55_000  # 50k seed + 5k
        paid = await trigger_jackpot()
        assert paid == 55_000
        assert await get_jackpot_pool() == 50_000  # reset to seed
    run(scenario())


def test_house_auto_borrow_and_repay():
    async def scenario():
        await house_receive(10_000)
        assert await house_auto_borrow(500_000) is True
        assert await get_house_debt() == 1_000_000  # 1M chunks
        assert await get_house_balance() >= 500_000
        repaid = await house_repay_debt(300_000)
        assert repaid == 300_000
        assert await get_house_debt() == 700_000
    run(scenario())


def test_outstanding_loans_total():
    async def scenario():
        await house_receive(1_000_000)
        for uid in (1, 2):
            await open_account(_User(uid))
            await take_loan(uid, 5_000)
        assert await get_total_outstanding_loans() == 10_000
    run(scenario())


# ── Whale protection / status bands ─────────────────────────────────────

def test_max_bet_allowed_scales_with_house():
    async def scenario():
        await house_receive(1_000_000)
        assert await max_bet_allowed() == 20_000  # 2% of 1M
        await house_receive(100_000_000)
        assert await max_bet_allowed() == 2_020_000  # 2% of 101M
    run(scenario())


def test_max_bet_allowed_floor():
    async def scenario():
        await house_receive(1_000)  # tiny house
        assert await max_bet_allowed() == 1_000  # never below the 1k floor
    run(scenario())


def test_house_status_band():
    assert house_status_band(50_000_000)[0] == 0
    assert house_status_band(5_000_000)[0] == 1
    assert house_status_band(500_000)[0] == 2
    assert house_status_band(50_000)[0] == 3
    labels = [band[1] for band in (house_status_band(50_000_000), house_status_band(5_000_000), house_status_band(500_000), house_status_band(50_000))]
    assert len(set(labels)) == 4  # all distinct


# ── Cache ───────────────────────────────────────────────────────────────

def test_bank_data_cache():
    async def scenario():
        store = {}
        bank_core._db = FakeClient(store)
        await open_account(_User(1))
        data = await get_bank_data()
        assert "1" in data
        # direct store mutation invisible while cached
        store["users/2"] = {"wallet": 50}
        assert "2" not in await get_bank_data()
        bank_core._cache.clear()
        assert "2" in await get_bank_data()
    run(scenario())


def test_ai_loan_limit_resolves_and_denies(monkeypatch):
    """ai_loan_limit must resolve its lazy AI import and return 0 when AI denies."""
    async def scenario():
        import bobcoin.ai
        from bobcoin.bank import ai_loan_limit

        async def _fake_ai(*a, **kw):
            return "{}"  # AI denies
        monkeypatch.setattr(bobcoin.ai, "call_ai", _fake_ai)

        await open_account(_User(1))
        await house_receive(10_000_000)
        assert await ai_loan_limit(1, 500_000) == 0
    run(scenario())


def test_ai_loan_extract_approved_strict_int():
    """P1 #4: only plain decimal integers pass — floats, exponents, bools, junk
    and huge strings must never widen the approved loan."""
    from bobcoin.bank.loans import _extract_approved

    ceil = 100_000
    assert _extract_approved('{"approved": 50000}', ceil) == 50_000
    assert _extract_approved('{"approved": "50000"}', ceil) == 50_000
    assert _extract_approved('{"approved": 200000}', ceil) == ceil        # capped
    assert _extract_approved('{"approved": 0}', ceil) == 0
    # injection attempts — must all be rejected
    assert _extract_approved('{"approved": 1500.0}', ceil) == 0          # float
    assert _extract_approved('{"approved": 1500.5}', ceil) == 0
    assert _extract_approved('{"approved": "1e10"}', ceil) == 0         # exponent
    assert _extract_approved('{"approved": true}', ceil) == 0            # bool
    assert _extract_approved('{"approved": []}', ceil) == 0              # list
    assert _extract_approved('{"approved": null}', ceil) == 0
    assert _extract_approved('{"approved": "99999999999999999999"}', ceil) == 0  # >18 digits
    assert _extract_approved('not json at all', ceil) == 0
    assert _extract_approved('', ceil) == 0


def test_ai_loan_logs_request_and_decision(monkeypatch):
    """P1 #4: every AI request is logged to user history for auditability."""
    async def scenario():
        import bobcoin.ai
        from bobcoin.bank import ai_loan_limit

        async def _fake_ai(*a, **kw):
            return '{"approved": 70000}'
        monkeypatch.setattr(bobcoin.ai, "call_ai", _fake_ai)

        await open_account(_User(1))
        await house_receive(10_000_000)
        assert await ai_loan_limit(1, 500_000) == 70_000
        entries = await get_history(1)
        assert entries and entries[0]["cmd"] == "ai_loan"
        assert entries[0]["requested"] == 500_000
        assert entries[0]["approved"] == 70_000
    run(scenario())


def test_ai_loan_rate_limited_to_once_per_day(monkeypatch):
    """P1 #4: only one AI loan decision per user per day."""
    async def scenario():
        import bobcoin.ai
        from bobcoin.bank import ai_loan_limit

        calls = []

        async def _fake_ai(*a, **kw):
            calls.append(a)
            return '{"approved": 70000}'
        monkeypatch.setattr(bobcoin.ai, "call_ai", _fake_ai)

        await open_account(_User(1))
        await house_receive(10_000_000)
        assert await ai_loan_limit(1, 500_000) == 70_000
        assert len(calls) == 1
        # second attempt within 24h → denied without calling the AI again
        assert await ai_loan_limit(1, 500_000) == 0
        assert len(calls) == 1
    run(scenario())


def test_outstanding_loans_cache():
    async def scenario():
        await house_receive(1_000_000)
        await open_account(_User(1))
        await take_loan(1, 5_000)
        assert await get_total_outstanding_loans() == 5_000
        # cache hit: fresh loan invisible without clearing the cache
        await take_loan(1, 1_000)
        assert await get_total_outstanding_loans() == 5_000
        # cache cleared → refetch sees the new loan
        bank._cache.clear()
        assert await get_total_outstanding_loans() == 6_000
    run(scenario())


# ── Leaderboard denormalization (P2 #10) ────────────────────────────────

def test_total_field_maintained_on_every_money_write():
    """Every money-mutating path keeps `total` (wallet+deposited) in sync."""
    async def scenario():
        u = _User(1)
        await open_account(u)
        assert (await bank._ref(1).get()).to_dict()["total"] == 0

        await update_bank(u, 1_000)                      # wallet only
        assert (await bank._ref(1).get()).to_dict()["total"] == 1_000

        await user_deposit(u, 400)                       # wallet→deposited (total unchanged)
        assert (await bank._ref(1).get()).to_dict()["total"] == 1_000

        await user_withdraw(u, 200)
        assert (await bank._ref(1).get()).to_dict()["total"] == 1_000

        await open_account(_User(2))
        await transfer_to_user(u, _User(2), 100)
        assert (await bank._ref(1).get()).to_dict()["total"] == 900
        assert (await bank._ref(2).get()).to_dict()["total"] == 100

        await rob_transfer(_User(2), u, 50)
        assert (await bank._ref(1).get()).to_dict()["total"] == 850
        assert (await bank._ref(2).get()).to_dict()["total"] == 150
    run(scenario())


def test_total_field_survives_loans_daily_and_force_collect():
    async def scenario():
        await house_receive(10_000_000)
        await open_account(_User(1))
        await update_bank(1, 1_000)
        await take_loan(1, 5_000)                        # wallet += 5k
        assert (await bank._ref(1).get()).to_dict()["total"] == 6_000
        await repay_loan(1, 2_000)
        assert (await bank._ref(1).get()).to_dict()["total"] == 4_000
        await try_daily(1)                               # wallet += reward
        assert (await bank._ref(1).get()).to_dict()["total"] > 4_000
        # force collect pulls wallet → total must shrink with it
        store = bank_core._db._store
        store["users/1"]["wallet"] = 0
        store["users/1"]["total"] = store["users/1"]["wallet"] + store["users/1"]["deposited"]
        from bobcoin.bank import guardian_force_collect
        await update_bank(1, 3_000)
        await guardian_force_collect(1.0)                # take all wallet to repay loan
        d = (await bank._ref(1).get()).to_dict()
        assert d["total"] == d["wallet"] + d["deposited"]
    run(scenario())


def test_total_field_keeps_deposited_after_loan_and_daily():
    """Regression: writes that omit `deposited` (loan/daily/force-collect) must
    still produce a correct total — the old _with_total read only the new fields
    and silently zeroed deposited for users with savings."""
    async def scenario():
        await house_receive(10_000_000)
        u = _User(1)
        await open_account(u)
        await update_bank(u, 50_000)
        await user_deposit(u, 20_000)                    # deposited = 20k
        assert (await bank._ref(1).get()).to_dict()["total"] == 50_000

        await take_loan(1, 5_000)                        # wallet += 5k, no deposited in fields
        d = (await bank._ref(1).get()).to_dict()
        assert d["total"] == d["wallet"] + d["deposited"] == 55_000

        await repay_loan(1, 2_000)
        d = (await bank._ref(1).get()).to_dict()
        assert d["total"] == d["wallet"] + d["deposited"] == 53_000

        await try_daily(1)
        d = (await bank._ref(1).get()).to_dict()
        assert d["total"] == d["wallet"] + d["deposited"]
        assert d["total"] > 53_000                       # reward added to wallet
    run(scenario())


def test_leaderboard_returns_richest_first():
    async def scenario():
        for uid, w in ((1, 1_000), (2, 9_000), (3, 5_000)):
            await open_account(_User(uid))
            await update_bank(uid, w)
        entries = await get_leaderboard(3)
        assert [uid for uid, _ in entries] == [2, 3, 1]
        assert entries[0][1]["wallet"] == 9_000
        # limit respected
        assert len(await get_leaderboard(2)) == 2
    run(scenario())


def test_leaderboard_counts_deposited_as_wealth():
    async def scenario():
        await open_account(_User(1))
        await update_bank(1, 1_000)
        await user_deposit(_User(1), 500)                # total stays 1_000
        await open_account(_User(2))
        await update_bank(2, 900)
        entries = await get_leaderboard(2)
        # user 1 (1_000) ranks above user 2 (900)
        assert entries[0][0] == 1 and entries[1][0] == 2
    run(scenario())


# ── Game stats (P2 #8) ───────────────────────────────────────────────────

def test_record_game_outcome_and_stats():
    async def scenario():
        assert await get_game_stats() == {}
        await record_game_outcome("slot", 1_000, -1_000)   # house win
        await record_game_outcome("slot", 1_000, 7_000)    # player win
        await record_game_outcome("flip", 500, -500)
        s = await get_game_stats("slot")
        assert s["games"] == 2
        assert s["house_wins"] == 1
        assert s["bets"] == 2_000
        assert s["house_net"] == 1_000 - 7_000 == -6_000
        assert (await get_game_stats())["flip"]["games"] == 1
        assert await get_game_stats("bj") == {"games": 0, "house_wins": 0, "bets": 0, "house_net": 0}
    run(scenario())
