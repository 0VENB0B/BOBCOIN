"""Tests for bobcoin.gameplay game runners (_run_slot/_run_flip/_run_lottery/
_run_bj) using a fake ctx and deterministic randomness.

What these prove (things the bank-layer tests can't):
- the full bet → payout loop moves exactly the right money (conservation),
- jackpots / wins / losses / pushes pay out the advertised multipliers,
- blocked games (bet too big / house closed / no funds) move nothing,
- history is logged with the correct net.
"""

import asyncio

import pytest

import bobcoin.bank.core as bank_core
import bobcoin.gameplay as gp
from bobcoin.bank import (
    get_balance,
    get_game_stats,
    get_history,
    get_house_balance,
    house_receive,
    open_account,
    update_bank,
)


class _Avatar:
    url = "http://avatar.example/x.png"


class _Author:
    def __init__(self, uid, name="TestUser"):
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


class _Ctx:
    def __init__(self, uid):
        self.author = _Author(uid)
        self.sent = []

    async def send(self, *args, **kw):
        msg = _Message()
        self.sent.append((args, kw, msg))
        return msg


def _accounting(store):
    """wallet + house − debt (same invariant as test_invariants)."""
    total = 0
    for key, d in store.items():
        if key.startswith("users/"):
            total += int(d.get("wallet", 0))
        elif key == "system/bank":
            total += int(d.get("balance", 0))
        elif key == "system/debt":
            total -= int(d.get("amount", 0))
    return total


async def _drain():
    """Let fire-and-forget tasks (log_history, _post_game, call_ai) finish."""
    for _ in range(25):
        await asyncio.sleep(0)


def _pick_from(values):
    """random.choice that yields `values` one by one, then seq[0] forever."""
    it = iter(values)

    def _pick(seq):
        try:
            return next(it)
        except StopIteration:
            return seq[0]

    return _pick


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


# ── Fixtures / helpers ──────────────────────────────────────────────────

async def _setup(store, uid=1, wallet=100_000, house=10_000_000):
    await house_receive(house)
    await open_account(_Author(uid))
    await update_bank(uid, wallet)


@pytest.fixture
def speed(monkeypatch):
    """Collapse animation sleeps into quick yields, silence the AI.

    NOTE: patches the GLOBAL asyncio.sleep (gameplay calls it via the shared
    module) — safe because pytest runs tests sequentially and monkeypatch
    restores even on failure. Do not run these under xdist/threads without
    reworking this fixture.
    """
    _real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    monkeypatch.setattr(gp, "call_ai", _fake_ai)
    return None


async def _fake_ai(*_a, **_kw):
    return ""


# ── Slot ────────────────────────────────────────────────────────────────

def test_slot_jackpot_8x_payout(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        base = _accounting(store)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_choice, orig_random = gp.random.choice, gp.random.random
        try:
            gp.random.choice = _pick_from(["🍎", "🍊", "🍐"])
            gp.random.random = lambda: 0.0          # < base_jp → jackpot
            await gp._run_slot(ctx, 10_000)
        finally:
            gp.random.choice, gp.random.random = orig_choice, orig_random
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 90_000       # 10k bet, 90k payout (8x + stake)
        assert await get_house_balance() == 10_000_000 + 10_000 - 90_000
        assert _accounting(store) == base           # money conserved
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "slot" and entries[0]["net"] == 80_000
        assert entries[0]["multiplier"] == 8
    asyncio.run(scenario())


def test_slot_loss_pays_nothing(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_choice, orig_random = gp.random.choice, gp.random.random
        try:
            gp.random.choice = _pick_from(["🍎", "🍊", "🍐"])   # all distinct → no match
            gp.random.random = lambda: 0.99                     # no jackpot
            await gp._run_slot(ctx, 10_000)
        finally:
            gp.random.choice, gp.random.random = orig_choice, orig_random
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0 - 10_000
        assert await get_house_balance() == 10_000_000 + 10_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "slot" and entries[0]["net"] == -10_000
        assert entries[0]["outcome"] == "lose"
    asyncio.run(scenario())


def test_slot_blocked_when_bet_too_big(speed):
    async def scenario():
        await house_receive(100_000)        # ≥ threshold → no auto-borrow; max_bet = 2k
        await open_account(_Author(1))
        await update_bank(1, 100_000)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]
        house0 = await get_house_balance()

        await gp._run_slot(ctx, 5_000)      # > max allowed (2% of 100k)

        assert (await get_balance(_Author(1)))[0] == wallet0   # nothing moved
        assert await get_house_balance() == house0
        assert await get_history(1) == []                       # no game logged
    asyncio.run(scenario())


def test_slot_blocked_when_house_closed(speed):
    async def scenario():
        store = bank_core._db._store
        store["system/debt"] = {"amount": 50_000_000}   # can't borrow → closed
        await open_account(_Author(1))
        await update_bank(1, 100_000)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        await gp._run_slot(ctx, 5_000)

        assert (await get_balance(_Author(1)))[0] == wallet0
        assert await get_house_balance() == 0
        assert await get_history(1) == []
    asyncio.run(scenario())


def test_slot_blocked_when_no_funds(speed):
    async def scenario():
        await house_receive(10_000_000)
        await open_account(_Author(1))      # wallet 0
        ctx = _Ctx(1)

        await gp._run_slot(ctx, 5_000)

        assert (await get_balance(_Author(1)))[0] == 0
        assert await get_house_balance() == 10_000_000
        assert await get_history(1) == []
    asyncio.run(scenario())


# ── Graceful shutdown drain (P3 Ops) ───────────────────────────────────

def test_drain_background_tasks_waits_for_completion():
    async def scenario():
        flag = []

        async def _slow():
            await asyncio.sleep(0.01)
            flag.append("done")

        task = gp._spawn(_slow())
        still = await gp.drain_background_tasks(timeout=1.0)
        assert still == 0
        assert flag == ["done"]
        assert task not in gp._background_tasks      # callback discarded it
    asyncio.run(scenario())


def test_drain_background_tasks_cancels_stuck_tasks():
    async def scenario():
        async def _stuck():
            await asyncio.sleep(60)

        task = gp._spawn(_stuck())
        still = await gp.drain_background_tasks(timeout=0.05)
        assert still == 1
        await asyncio.sleep(0)          # let the cancel propagate to the task
        assert task.cancelled()
    asyncio.run(scenario())


def test_drain_background_tasks_empty_is_noop():
    async def scenario():
        assert await gp.drain_background_tasks(timeout=0.01) == 0
    asyncio.run(scenario())


# ── Game stats recording (P2 #8) ───────────────────────────────────────

def test_blocked_games_record_no_stats(speed):
    """Games that never run (no funds) must not touch the stats doc."""
    async def scenario():
        await house_receive(10_000_000)
        await open_account(_Author(1))      # wallet 0 → blocked
        ctx = _Ctx(1)
        await gp._run_slot(ctx, 5_000)
        assert await get_game_stats() == {}
    asyncio.run(scenario())


def test_slot_records_house_win_stat(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        orig_choice, orig_random = gp.random.choice, gp.random.random
        try:
            gp.random.choice = _pick_from(["🍎", "🍊", "🍐"])   # no match
            gp.random.random = lambda: 0.99                     # no jackpot
            await gp._run_slot(ctx, 10_000)
        finally:
            gp.random.choice, gp.random.random = orig_choice, orig_random
        await _drain()
        s = await get_game_stats("slot")
        assert s["games"] == 1 and s["house_wins"] == 1
        assert s["bets"] == 10_000 and s["house_net"] == 10_000
    asyncio.run(scenario())


def test_slot_two_match_records_stat_without_error(speed):
    """Regression: the two-match (near-miss) branch used to leave `net`
    undefined, making the spawned stat recorder raise NameError on ~33% of
    spins — the game still paid out but the stat was dropped."""
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]
        orig_choice, orig_random = gp.random.choice, gp.random.random
        try:
            # two matching symbols → near-miss (คืนทุน)
            gp.random.choice = _pick_from(["🍎", "🍎", "🍊"])
            gp.random.random = lambda: 0.99              # no jackpot
            await gp._run_slot(ctx, 10_000)
        finally:
            gp.random.choice, gp.random.random = orig_choice, orig_random
        await _drain()
        assert (await get_balance(_Author(1)))[0] == wallet0   # refunded
        s = await get_game_stats("slot")
        assert s["games"] == 1
        assert s["house_wins"] == 0                        # player broke even
        assert s["house_net"] == 0
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "slot" and entries[0]["outcome"] == "near"
    asyncio.run(scenario())


def test_flip_records_player_win_stat(speed):
    async def scenario():
        await _setup(bank_core._db._store)
        ctx = _Ctx(1)
        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.0    # win
            await gp._run_flip(ctx, 10_000, "1")
        finally:
            gp.random.random = orig
        await _drain()
        s = await get_game_stats("flip")
        assert s["games"] == 1 and s["house_wins"] == 0      # player won
        assert s["house_net"] == -8_000
    asyncio.run(scenario())


def test_bj_records_stat_on_all_exit_paths(speed, stand_view):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        # natural blackjack path
        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([11, 10])
            gp._bj_draw = lambda: 9
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()
        assert (await get_game_stats("bj"))["games"] == 1
        assert (await get_game_stats("bj"))["house_net"] == -15_000   # 2.5x payout
    asyncio.run(scenario())


# ── Flip ────────────────────────────────────────────────────────────────

def test_flip_win_pays_1_8x(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.0    # < 0.5 win chance
            await gp._run_flip(ctx, 10_000, "1")
        finally:
            gp.random.random = orig
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 18_000          # 1.8x
        assert await get_house_balance() == 10_000_000 + 10_000 - 18_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "flip" and entries[0]["net"] == 8_000
        assert entries[0]["win"] is True
    asyncio.run(scenario())


def test_flip_loss(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.9    # ≥ 0.5 → lose
            await gp._run_flip(ctx, 10_000, "1")
        finally:
            gp.random.random = orig
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0 - 10_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "flip" and entries[0]["win"] is False
        assert entries[0]["net"] == -10_000
    asyncio.run(scenario())


# ── Lottery ─────────────────────────────────────────────────────────────

def test_lottery_5_match_50x(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig = gp.random.randrange
        try:
            gp.random.randrange = lambda *a: 12345   # bot draws the player's number
            await gp._run_lottery(ctx, 100, "12345")
        finally:
            gp.random.randrange = orig
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 100 + 5_000            # 50x ticket
        assert await get_house_balance() == 10_000_000 + 100 - 5_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "lottery" and entries[0]["match"] == "5ตัว"
        assert entries[0]["net"] == 4_900
    asyncio.run(scenario())


def test_lottery_loss(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig = gp.random.randrange
        try:
            gp.random.randrange = lambda *a: 54321   # no match at all
            await gp._run_lottery(ctx, 100, "12345")
        finally:
            gp.random.randrange = orig
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0 - 100
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "lottery" and entries[0]["match"] == "ไม่ถูก"
    asyncio.run(scenario())


# ── Blackjack ───────────────────────────────────────────────────────────

class _FakeView:
    """Immediately 'times out' → player stands with whatever they have."""

    def __init__(self, player_id, allow_double=False):
        self.action = "stand"
        self.allow_double = allow_double

    async def wait(self):
        return True


@pytest.fixture
def stand_view(monkeypatch):
    monkeypatch.setattr(gp, "_BJView", _FakeView)


def test_bj_natural_blackjack_2_5x(speed, stand_view):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([11, 10])  # ace + 10 → 21 natural
            gp._bj_draw = lambda: 9          # dealer 18
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 25_000      # 2.5x
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj" and entries[0]["result"] == "blackjack"
    asyncio.run(scenario())


def test_bj_win_1_8x_via_stand(speed, stand_view):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([10, 9])      # player 19
            gp._bj_draw = _seq([10, 7])         # dealer 17
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 18_000      # 1.8x
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj" and entries[0]["result"] == "win"
    asyncio.run(scenario())


def test_bj_push_refunds(speed, stand_view):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([10, 7])      # player 17
            gp._bj_draw = _seq([10, 7])         # dealer 17
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0   # refunded
        assert await get_house_balance() == 10_000_000         # house net 0
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj" and entries[0]["result"] == "push"
    asyncio.run(scenario())


def test_bj_loss(speed, stand_view):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([10, 7])      # player 17
            gp._bj_draw = _seq([10, 10])        # dealer 20
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0 - 10_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj" and entries[0]["result"] == "lose"
    asyncio.run(scenario())


# ── Blackjack: Double Down ──────────────────────────────────────────────

class _DoubleView:
    """Presses Double Down when offered (first two cards), else stands."""

    def __init__(self, player_id, allow_double=False):
        self.action = "double" if allow_double else "stand"
        self.allow_double = allow_double

    async def wait(self):
        return False


@pytest.fixture
def double_view(monkeypatch):
    monkeypatch.setattr(gp, "_BJView", _DoubleView)


def test_bj_double_down_wins_on_doubled_bet(speed, double_view):
    """Double Down doubles the bet, draws one card, and settles on 2x stake."""
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([10, 9, 2])   # 19 → double draw 2 → 21
            gp._bj_draw = _seq([10, 7])         # dealer 17
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 20_000 + 36_000      # 1.8x on the doubled bet
        assert await get_house_balance() == 10_000_000 + 20_000 - 36_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj"
        assert entries[0]["bet"] == 20_000 and entries[0]["doubled"] is True
        assert entries[0]["result"] == "win"
        assert (await get_game_stats("bj"))["bets"] == 20_000
    asyncio.run(scenario())


def test_bj_double_down_insufficient_funds_stands(speed, double_view):
    """Can't afford the double → the hand continues on the original bet."""
    async def scenario():
        store = bank_core._db._store
        await _setup(store, wallet=15_000)          # only 5k left after the first bet
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        orig_lc, orig_bd = gp._lucky_card, gp._bj_draw
        try:
            gp._lucky_card = _seq([10, 9])      # 19 (no extra draw — double refused)
            gp._bj_draw = _seq([10, 7])         # dealer 17
            await gp._run_bj(ctx, 10_000)
        finally:
            gp._lucky_card, gp._bj_draw = orig_lc, orig_bd
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 18_000      # settled on the original bet
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "bj"
        assert entries[0]["bet"] == 10_000 and entries[0]["doubled"] is False
    asyncio.run(scenario())


# ── Slots: replay view ──────────────────────────────────────────────────

def test_slot_final_message_attaches_replay_view(speed):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        orig_choice, orig_random = gp.random.choice, gp.random.random
        try:
            gp.random.choice = _pick_from(["🍎", "🍊", "🍐"])
            gp.random.random = lambda: 0.99
            await gp._run_slot(ctx, 10_000)
        finally:
            gp.random.choice, gp.random.random = orig_choice, orig_random
        await _drain()
        msg = ctx.sent[0][2]
        view = msg.edits[-1].get("view")
        assert view is not None
        assert isinstance(view, gp._SlotReplayView)
        assert view.amount == 10_000 and view.author_id == 1
    asyncio.run(scenario())


def test_slot_replay_runs_once_then_respects_cooldown(monkeypatch):
    """Replay re-runs the slot with the same bet, then honors the persisted
    panel cooldown so a user can't chain spins for free."""
    runs = []

    async def _fake_run(ctx, amount):
        runs.append((ctx, amount))

    monkeypatch.setattr(gp, "_run_slot", _fake_run)

    class _Resp:
        def __init__(self):
            self.deferred = None
            self.messages = []

        async def defer(self, **kw):
            self.deferred = kw

        async def send_message(self, content=None, **kw):
            self.messages.append((content, kw))

    class _It:
        def __init__(self, uid):
            self.user = _Author(uid)
            self.response = _Resp()

    async def scenario():
        await _setup(bank_core._db._store)
        view = gp._SlotReplayView(object(), _Author(1), 10_000)
        await view.replay.callback(_It(1))
        assert len(runs) == 1 and runs[0][1] == 10_000
        assert runs[0][0].author.id == 1
        await view.replay.callback(_It(1))          # same window → blocked
        assert len(runs) == 1
    asyncio.run(scenario())


def test_get_game_streak_counts_rps(monkeypatch):
    """RPS results participate in streaks just like slot/flip/lottery."""
    import types as _types

    ticks = iter([100, 200, 300])            # oldest → newest log timestamps

    class _DT:
        @classmethod
        def now(cls, tz=None):
            return _types.SimpleNamespace(timestamp=lambda: next(ticks))

    monkeypatch.setattr(bank_core, "datetime", _DT)

    async def scenario():
        await _setup(bank_core._db._store)
        from bobcoin.bank import log_history
        await log_history(1, {"cmd": "rps", "net": 800})
        await log_history(1, {"cmd": "rps", "net": 800})
        n, is_win = await gp._get_game_streak(1)
        assert n == 2 and is_win is True
        await log_history(1, {"cmd": "rps", "net": -500})
        n2, _ = await gp._get_game_streak(1)
        assert n2 == 1                                 # loss streak of 1
    asyncio.run(scenario())


# ── Rock Paper Scissors ────────────────────────────────────────────────

class _RPSFakeView:
    def __init__(self, player_id, choice="rock", timed_out=False):
        self.choice = choice if not timed_out else None
        self._t = timed_out

    async def wait(self):
        return self._t


def _rps_view(choice="rock", timed_out=False):
    def _factory(player_id):
        return _RPSFakeView(player_id, choice=choice, timed_out=timed_out)
    return _factory


def test_rps_win_pays_1_8x(speed, monkeypatch):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        monkeypatch.setattr(gp, "_RPSView", _rps_view("rock"))
        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.0      # < win_thresh → bot loses
            await gp._run_rps(ctx, 10_000)
        finally:
            gp.random.random = orig
        await _drain()

        w = (await get_balance(_Author(1)))[0]
        assert w == wallet0 - 10_000 + 18_000      # 1.8x
        assert await get_house_balance() == 10_000_000 + 10_000 - 18_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "rps" and entries[0]["result"] == "win"
        assert entries[0]["pick"] == "rock" and entries[0]["bot"] == "scissors"
        assert entries[0]["net"] == 8_000
        assert (await get_game_stats("rps"))["house_wins"] == 0
    asyncio.run(scenario())


def test_rps_tie_refunds(speed, monkeypatch):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        monkeypatch.setattr(gp, "_RPSView", _rps_view("rock"))
        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.4       # between win & tie thresholds
            await gp._run_rps(ctx, 10_000)
        finally:
            gp.random.random = orig
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0      # refunded
        assert await get_house_balance() == 10_000_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "rps" and entries[0]["result"] == "tie"
        assert entries[0]["bot"] == "rock"
        assert entries[0]["net"] == 0
    asyncio.run(scenario())


def test_rps_loss(speed, monkeypatch):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        monkeypatch.setattr(gp, "_RPSView", _rps_view("rock"))
        orig = gp.random.random
        try:
            gp.random.random = lambda: 0.9       # bot beats player
            await gp._run_rps(ctx, 10_000)
        finally:
            gp.random.random = orig
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0 - 10_000
        entries = await get_history(1, limit=10)
        assert entries[0]["cmd"] == "rps" and entries[0]["result"] == "lose"
        assert entries[0]["bot"] == "paper"
        assert (await get_game_stats("rps"))["house_wins"] == 1
    asyncio.run(scenario())


def test_rps_timeout_refunds(speed, monkeypatch):
    async def scenario():
        store = bank_core._db._store
        await _setup(store)
        ctx = _Ctx(1)
        wallet0 = (await get_balance(_Author(1)))[0]

        monkeypatch.setattr(gp, "_RPSView", _rps_view("rock", timed_out=True))
        await gp._run_rps(ctx, 10_000)
        await _drain()

        assert (await get_balance(_Author(1)))[0] == wallet0      # nothing lost
        assert await get_house_balance() == 10_000_000
        assert await get_history(1) == []                          # no game logged
    asyncio.run(scenario())


def test_rps_blocked_when_no_funds(speed, monkeypatch):
    async def scenario():
        await house_receive(10_000_000)
        await open_account(_Author(1))              # wallet 0
        ctx = _Ctx(1)
        monkeypatch.setattr(gp, "_RPSView", _rps_view("rock"))

        await gp._run_rps(ctx, 5_000)

        assert (await get_balance(_Author(1)))[0] == 0
        assert await get_house_balance() == 10_000_000
        assert await get_history(1) == []
    asyncio.run(scenario())
