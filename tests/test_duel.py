"""Tests for bobcoin.cogs.duel — the PvP duel flow.

Fake channels/views simulate challenge acceptance, and the random draws are
made deterministic, so every money path (winner takes net pot, house cut,
refunds on ties, rejections) is verified with exact balances + conservation.
"""

import asyncio

import pytest

import bobcoin.bank.core as bank_core
import bobcoin.cogs.duel as duel
from bobcoin.bank import (
    get_balance,
    get_house_balance,
    house_receive,
    open_account,
    update_bank,
)


class _Avatar:
    url = "http://avatar.example/x.png"


class _Member:
    def __init__(self, uid, name):
        self.id = uid
        self.name = name
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.display_avatar = _Avatar()


class _Message:
    def __init__(self):
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        m = _Message()
        self.sent.append((a, kw, m))
        return m


def _accounting(store):
    total = 0
    for key, d in store.items():
        if key.startswith("users/"):
            total += int(d.get("wallet", 0))
        elif key == "system/bank":
            total += int(d.get("balance", 0))
        elif key == "system/debt":
            total -= int(d.get("amount", 0))
    return total


def run(coro):
    return asyncio.run(coro)


async def _drain():
    for _ in range(15):
        await asyncio.sleep(0)


async def _setup(store, uid, wallet):
    await open_account(_Member(uid, f"U{uid}"))
    await update_bank(uid, wallet)


def _seq(values):
    """Deterministic card draw: yield `values`, then hold the last."""
    it = iter(values)
    last = [values[-1]]

    def _next(_lk=None):
        try:
            return next(it)
        except StopIteration:
            return last[0]

    return _next


# ── Fake views ──────────────────────────────────────────────────────────

class _AcceptView:
    def __init__(self, target_id):
        self.accepted = True

    async def wait(self):
        return None


class _DeclineView(_AcceptView):
    def __init__(self, target_id):
        self.accepted = False


class _TimeoutView(_AcceptView):
    def __init__(self, target_id):
        self.accepted = None


class _SideViewStub:
    """Flip side picker — immediately 'times out', defaults to หัว (1)."""

    def __init__(self, user_id):
        self.side = "1"

    async def wait(self):
        return True


class _StandView:
    """BJ hit/stand picker — immediately stands."""

    def __init__(self, player_id):
        self.action = "stand"

    async def wait(self):
        return True


@pytest.fixture
def fast(monkeypatch):
    """Collapse animation sleeps into quick yields (flip has a 1.8s delay)."""
    _real_sleep = asyncio.sleep

    async def _fast(_s):
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast)
    return None


# ── Rejection paths (return before the challenge view) ─────────────────

def test_duel_rejects_invalid_bet():
    async def scenario():
        ch = _Channel()
        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "flip", 0)
        assert any("ไม่ถูกต้อง" in s[0][0] for s in ch.sent)
    run(scenario())


def test_duel_rejects_unregistered_challenger():
    async def scenario():
        ch = _Channel()
        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "flip", 1000)
        assert any("ยังไม่มีบัญชี" in s[0][0] for s in ch.sent)
    run(scenario())


def test_duel_rejects_poor_challenger():
    async def scenario():
        store = bank_core._db._store
        await _setup(store, 1, 0)
        await _setup(store, 2, 0)
        ch = _Channel()
        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "flip", 1000)
        assert any("ไม่พอ" in s[0][0] for s in ch.sent)
    run(scenario())


# ── Decline / timeout ───────────────────────────────────────────────────

@pytest.mark.parametrize("view_cls", [_DeclineView, _TimeoutView])
def test_duel_decline_and_timeout_move_no_money(monkeypatch, view_cls):
    monkeypatch.setattr(duel, "DuelChallengeView", view_cls)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        ch = _Channel()
        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "flip", 10_000)
        assert (await get_balance(_Member(1, "A")))[0] == 100_000
        assert (await get_balance(_Member(2, "B")))[0] == 100_000
        assert await get_house_balance() == 10_000_000
    run(scenario())


# ── Flip duel ───────────────────────────────────────────────────────────

def test_duel_flip_challenger_wins(fast, monkeypatch):
    monkeypatch.setattr(duel, "DuelChallengeView", _AcceptView)
    monkeypatch.setattr(duel, "_SideView", _SideViewStub)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        base = _accounting(store)
        ch = _Channel()

        orig = duel.random.random
        try:
            duel.random.random = lambda: 0.0     # < 0.5 → challenger wins
            await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "flip", 10_000)
        finally:
            duel.random.random = orig
        await _drain()

        bet, pot, cut = 10_000, 20_000, int(20_000 * 0.05)
        net_pot = pot - cut
        assert (await get_balance(_Member(1, "A")))[0] == 100_000 - bet + net_pot
        assert (await get_balance(_Member(2, "B")))[0] == 100_000 - bet
        assert await get_house_balance() == 10_000_000 + cut
        assert _accounting(store) == base         # money conserved
    run(scenario())


# ── Slot duel ───────────────────────────────────────────────────────────

def test_duel_slot_challenger_wins(fast, monkeypatch):
    monkeypatch.setattr(duel, "DuelChallengeView", _AcceptView)

    async def _fake_spin(user_id):
        if user_id == 1:
            return (["🍎", "🍎", "🍎"], 80)       # jackpot 80 pts
        return (["🍊", "🍐", "🍇"], 0)            # miss 0 pts

    monkeypatch.setattr(duel, "_slot_spin", _fake_spin)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        base = _accounting(store)
        ch = _Channel()

        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "slot", 10_000)
        await _drain()

        bet, pot, cut = 10_000, 20_000, int(20_000 * 0.05)
        net_pot = pot - cut
        assert (await get_balance(_Member(1, "A")))[0] == 100_000 - bet + net_pot
        assert (await get_balance(_Member(2, "B")))[0] == 100_000 - bet
        assert await get_house_balance() == 10_000_000 + cut
        assert _accounting(store) == base
    run(scenario())


def test_duel_slot_tie_refunds(fast, monkeypatch):
    monkeypatch.setattr(duel, "DuelChallengeView", _AcceptView)

    async def _tie_spin(user_id):
        return (["🍎", "🍎", "🍎"], 80)          # equal scores → tie

    monkeypatch.setattr(duel, "_slot_spin", _tie_spin)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        ch = _Channel()

        await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "slot", 10_000)
        await _drain()

        bet, pot, cut = 10_000, 20_000, int(20_000 * 0.05)
        net_pot = pot - cut
        refund = net_pot // 2
        # each user: -bet, then refund back (odd remainder goes to the house)
        assert (await get_balance(_Member(1, "A")))[0] == 100_000 - bet + refund
        assert (await get_balance(_Member(2, "B")))[0] == 100_000 - bet + refund
        assert await get_house_balance() == 10_000_000 + cut + (net_pot - refund * 2)
    run(scenario())


# ── BJ duel ─────────────────────────────────────────────────────────────

def test_duel_bj_challenger_wins(fast, monkeypatch):
    monkeypatch.setattr(duel, "DuelChallengeView", _AcceptView)
    monkeypatch.setattr(duel, "_BJView", _StandView)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        base = _accounting(store)
        ch = _Channel()

        orig_lc, orig_bd = duel._lucky_card, duel._bj_draw
        try:
            # dealer 17; C = [10,9]=19 beats it, T = [10,7]=17 pushes → C wins
            duel._bj_draw = _seq([10, 7])
            duel._lucky_card = _seq([10, 9, 10, 7])
            await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "bj", 10_000)
        finally:
            duel._lucky_card, duel._bj_draw = orig_lc, orig_bd
        await _drain()

        bet, pot, cut = 10_000, 20_000, int(20_000 * 0.05)
        net_pot = pot - cut
        assert (await get_balance(_Member(1, "A")))[0] == 100_000 - bet + net_pot
        assert (await get_balance(_Member(2, "B")))[0] == 100_000 - bet
        assert await get_house_balance() == 10_000_000 + cut
        assert _accounting(store) == base
    run(scenario())


def test_duel_bj_target_wins(fast, monkeypatch):
    monkeypatch.setattr(duel, "DuelChallengeView", _AcceptView)
    monkeypatch.setattr(duel, "_BJView", _StandView)

    async def scenario():
        store = bank_core._db._store
        await house_receive(10_000_000)
        await _setup(store, 1, 100_000)
        await _setup(store, 2, 100_000)
        ch = _Channel()

        orig_lc, orig_bd = duel._lucky_card, duel._bj_draw
        try:
            # dealer 17; C = [10,5]=15 loses to it, T = [10,10]=20 wins → T wins
            duel._bj_draw = _seq([10, 7])
            duel._lucky_card = _seq([10, 5, 10, 10])
            await duel.DuelCog(None).run_duel(ch, _Member(1, "A"), _Member(2, "B"), "bj", 10_000)
        finally:
            duel._lucky_card, duel._bj_draw = orig_lc, orig_bd
        await _drain()

        bet, pot, cut = 10_000, 20_000, int(20_000 * 0.05)
        net_pot = pot - cut
        assert (await get_balance(_Member(1, "A")))[0] == 100_000 - bet
        assert (await get_balance(_Member(2, "B")))[0] == 100_000 - bet + net_pot
        assert await get_house_balance() == 10_000_000 + cut
    run(scenario())
