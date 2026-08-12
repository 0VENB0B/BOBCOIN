"""Full coverage of the guardian module: bank health, bet caps, luck system,
crisis tiers and force collection."""

import asyncio

import pytest

import bobcoin.bank.core as bank_core
from bobcoin.bank import (
    get_balance,
    get_bank_health,
    get_effective_luck,
    get_house_balance,
    get_house_debt,
    get_loan_info,
    get_lucky_users,
    get_user_luck,
    guardian_force_collect,
    guardian_nerf_user,
    guardian_restore_user,
    house_can_pay_games,
    house_receive,
    house_status_band,
    max_bet_allowed,
    open_account,
    set_user_luck,
    take_loan,
    update_bank,
)
from bobcoin.settings import MAX_BET


class _User:
    def __init__(self, uid):
        self.id = uid


def run(coro):
    return asyncio.run(coro)


# ── House can pay games ────────────────────────────────────────────────

def test_house_can_pay_when_healthy():
    async def scenario():
        await house_receive(200_000)  # ≥ 100k threshold
        assert await house_can_pay_games() is True
        assert await get_house_debt() == 0  # no borrowing happened
    run(scenario())


def test_house_can_pay_auto_borrows():
    async def scenario():
        await house_receive(50_000)  # below threshold → borrow
        assert await house_can_pay_games() is True
        assert await get_house_balance() >= 100_000
        assert await get_house_debt() > 0
    run(scenario())


def test_house_can_pay_fails_at_debt_ceiling():
    async def scenario():
        store = bank_core._db._store
        await house_receive(50_000)
        store["system/debt"] = {"amount": 50_000_000}  # can't borrow anymore
        assert await house_can_pay_games() is False
    run(scenario())


# ── Whale-proof bet cap ────────────────────────────────────────────────

def test_max_bet_with_explicit_balance():
    assert run(max_bet_allowed(500_000)) == 10_000       # 2%
    assert run(max_bet_allowed(1_000)) == 1_000          # floor
    assert run(max_bet_allowed(0)) == 1_000              # floor even at 0
    assert run(max_bet_allowed(-100)) == 1_000           # negative → floor


def test_max_bet_capped_at_settings_max():
    assert run(max_bet_allowed(100_000_000_000)) == MAX_BET  # 2% would exceed MAX_BET


def test_max_bet_floor_and_ratio_relationship():
    # cap never goes below the 1k floor and never above MAX_BET
    for bal in (1, 10, 1_000, 50_000, 1_000_000, 10_000_000, 10**12):
        cap = run(max_bet_allowed(bal))
        assert 1_000 <= cap <= MAX_BET
    # once the ratio beats the floor, it is exactly 2% of the balance
    for bal in (50_000, 100_000, 1_000_000, 10_000_000):
        cap = run(max_bet_allowed(bal))
        assert cap == int(bal * 0.02)
    # below the floor threshold, the 1k floor dominates
    assert run(max_bet_allowed(1_000)) == 1_000
    assert run(max_bet_allowed(10_000)) == 1_000


# ── Status band boundaries ──────────────────────────────────────────────

def test_house_status_band_exact_boundaries():
    assert house_status_band(10_000_000)[0] == 0   # rich
    assert house_status_band(9_999_999)[0] == 1
    assert house_status_band(1_000_000)[0] == 1
    assert house_status_band(999_999)[0] == 2
    assert house_status_band(100_000)[0] == 2
    assert house_status_band(99_999)[0] == 3
    assert house_status_band(0)[0] == 3
    assert house_status_band(-1)[0] == 3


def test_house_status_band_icons_consistent():
    tiers = [house_status_band(b)[0] for b in (15_000_000, 5_000_000, 500_000, 50_000)]
    assert tiers == [0, 1, 2, 3]
    assert house_status_band(15_000_000)[2] == "🟢"
    assert house_status_band(5_000_000)[2] == "🟡"
    assert house_status_band(500_000)[2] == "🟠"
    assert house_status_band(50_000)[2] == "🔴"


# ── Luck ────────────────────────────────────────────────────────────────

def test_luck_default_and_set():
    async def scenario():
        assert await get_user_luck(1) == 1.0
        await set_user_luck(1, 2.5)
        assert await get_user_luck(1) == 2.5
        # clamped to 0 minimum
        await set_user_luck(1, -3.0)
        assert await get_user_luck(1) == 0.0
    run(scenario())


def test_effective_luck_crisis_tiers():
    async def scenario():
        await open_account(_User(1))
        await house_receive(10_000_000)
        await set_user_luck(1, 1.0)
        # healthy house → full luck
        assert await get_effective_luck(1) == 1.0

        # mild crisis: bal < 5M → cap 0.75
        bank_core._db._store["system/bank"]["balance"] = 3_000_000
        assert await get_effective_luck(1) == 0.75

        # bad: bal < 2M → cap 0.55
        bank_core._db._store["system/bank"]["balance"] = 1_000_000
        assert await get_effective_luck(1) == 0.55

        # severe: bal < 500k → cap 0.30
        bank_core._db._store["system/bank"]["balance"] = 400_000
        assert await get_effective_luck(1) == 0.30
    run(scenario())


def test_effective_luck_user_below_cap_untouched():
    async def scenario():
        await set_user_luck(1, 0.20)
        bank_core._db._store["system/bank"] = {"balance": 400_000}  # severe crisis
        assert await get_effective_luck(1) == 0.20  # min(luck, cap) → keeps low luck
        bank_core._db._store["system/bank"]["balance"] = 10_000_000
        assert await get_effective_luck(1) == 0.20  # healthy → full user luck
    run(scenario())


# ── Bank health ─────────────────────────────────────────────────────────

def _health_with(balance, total_in):
    async def scenario():
        store = bank_core._db._store
        store["system/bank"] = {"balance": balance, "total_in": total_in}
        return await get_bank_health()
    return run(scenario())


def test_bank_health_statuses():
    assert _health_with(1_000_000, 4_000_000)["status"] == "healthy"   # ratio 0.25
    assert _health_with(600_000, 10_000_000)["status"] == "warning"    # bal ≥ floor/2
    assert _health_with(200_000, 10_000_000)["status"] == "critical"   # bal ≥ floor/5
    assert _health_with(10_000, 10_000_000)["status"] == "danger"      # below everything


def test_bank_health_zero_history():
    h = _health_with(1_000_000, 0)
    assert h["status"] == "healthy"  # ratio = bal/max(0,1) huge
    assert h["ratio"] == 1_000_000


def test_bank_health_ratio_computed():
    h = _health_with(100_000, 1_000_000)
    assert h["ratio"] == pytest.approx(0.1)


# ── Lucky users listing ─────────────────────────────────────────────────

def test_lucky_users_filters_and_sorts():
    async def scenario():
        for uid, luck in ((1, 2.0), (2, 1.0), (3, 0.5)):
            await open_account(_User(uid))
            await update_bank(_User(uid), uid * 100)
            await set_user_luck(uid, luck)
        users = await get_lucky_users()
        assert [u["luck"] for u in users] == [2.0, 0.5]  # 1.0 filtered out, desc sort
        assert users[0]["id"] == "1" and users[0]["wallet"] == 100
        assert users[1]["id"] == "3" and users[1]["wallet"] == 300
    run(scenario())


# ── Guardian nerf / restore ─────────────────────────────────────────────

def test_nerf_saves_original_only_once():
    async def scenario():
        await set_user_luck(1, 1.5)
        await guardian_nerf_user(1, 1.5, 0.8)
        d = bank_core._db._store["users/1"]
        assert d["luck"] == 0.8
        assert d["guardian_original_luck"] == 1.5
        # second nerf does not overwrite the original
        await guardian_nerf_user(1, 0.8, 0.5)
        assert bank_core._db._store["users/1"]["guardian_original_luck"] == 1.5
    run(scenario())


def test_restore_moves_quarter_of_gap():
    async def scenario():
        await set_user_luck(1, 1.0)
        await guardian_nerf_user(1, 1.0, 0.4)
        new = await guardian_restore_user(1, 0.4, 1.0)
        assert new == 0.55  # 0.4 + 0.6*0.25
        assert bank_core._db._store["users/1"]["guardian_original_luck"] == 1.0  # not cleared
    run(scenario())


def test_restore_fully_restored_clears_flag():
    async def scenario():
        await set_user_luck(1, 0.5)
        # luck already at/above original → fully restored
        new = await guardian_restore_user(1, 0.5, 0.5)
        assert new == 0.5
        assert bank_core._db._store["users/1"]["guardian_original_luck"] is None
    run(scenario())


def test_restore_never_exceeds_original():
    async def scenario():
        await set_user_luck(1, 0.0)
        # restores converge asymptotically: each step closes 25% of the gap
        new = 0.0
        for _ in range(10):
            new = await guardian_restore_user(1, new, 1.0)
        assert new < 1.0                 # asymptotic, never overshoots
        assert new > 0.9
        # and it strictly increased each step
        prev = 0.0
        for _ in range(5):
            nxt = await guardian_restore_user(1, prev, 1.0)
            assert nxt > prev
            prev = nxt
    run(scenario())


# ── Force collection ────────────────────────────────────────────────────

def test_force_collect_basic():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 10_000)          # wallet 10k, loan 10k
        users, total = await guardian_force_collect(0.10)
        assert (users, total) == (1, 1_000)
        assert (await get_balance(_User(1)))[0] == 9_000
        assert (await get_loan_info(1))["loan_balance"] == 9_000
        assert await get_house_balance() == 1_000_000 - 10_000 + 1_000
    run(scenario())


def test_force_collect_skips_clean_users():
    async def scenario():
        await open_account(_User(1))  # no loan
        await open_account(_User(2))
        await house_receive(1_000_000)
        await take_loan(2, 10_000)
        await update_bank(_User(2), -10_000)  # wallet 0 → cannot be collected
        users, total = await guardian_force_collect()
        assert (users, total) == (0, 0)
    run(scenario())


def test_force_collect_never_exceeds_loan_or_wallet():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 5_000)
        await update_bank(_User(1), -4_000)  # wallet 1_000 < loan 5_000
        users, total = await guardian_force_collect(1.0)  # aggressive
        assert (users, total) == (1, 1_000)
        assert (await get_balance(_User(1)))[0] == 0
        assert (await get_loan_info(1))["loan_balance"] == 4_000
    run(scenario())


def test_force_collect_minimum_one_coin():
    async def scenario():
        await open_account(_User(1))
        await house_receive(1_000_000)
        await take_loan(1, 100)
        await update_bank(_User(1), -99)  # wallet 1 coin
        users, total = await guardian_force_collect(0.001)  # 0.1% of 1 → floor 1
        assert (users, total) == (1, 1)
        assert (await get_balance(_User(1)))[0] == 0
    run(scenario())
