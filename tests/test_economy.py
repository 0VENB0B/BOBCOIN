"""Tests for bobcoin.cogs.economy — command-level money flows.

Unlike the bank-layer tests, these exercise the actual command handlers with
a fake ctx (via conftest.invoke_command, which bypasses discord's converter
machinery but runs the real callback). They verify the P0 security fixes at
the user-facing level: rob's transfer-relation block, Firestore cooldown and
atomic rob_transfer, plus register/deposit/withdraw/give end-to-end.

The cog is instantiated with __new__ so the 24h interest tasks loop never
starts.
"""

import asyncio
from datetime import datetime, timezone

import pytest

import bobcoin.cogs.economy as economy
from bobcoin.cogs.economy import EconomyCog
from bobcoin.bank import (
    get_balance,
    get_history,
    get_house_balance,
    house_receive,
    is_registered,
    open_account,
    set_cooldown,
    update_bank,
)
from conftest import invoke_command


class _Avatar:
    url = "http://avatar.example/x.png"


class _Member:
    def __init__(self, uid, name="U"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = False
        self.mention = f"<@{uid}>"
        self.display_avatar = _Avatar()
        self.created_at = datetime(2021, 1, 1, tzinfo=timezone.utc)
        self.joined_at = datetime(2021, 2, 2, tzinfo=timezone.utc)


class _BotMember(_Member):
    def __init__(self, uid):
        super().__init__(uid, "BOT")
        self.bot = True


class _Message:
    def __init__(self, content):
        self.content = content


class _Ctx:
    def __init__(self, author):
        self.author = author
        self.prefix = "$"
        self.channel = object()
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class _Bot:
    """wait_for queue — raises TimeoutError when exhausted."""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = []

    async def wait_for(self, event, check=None, timeout=None):
        self.calls.append((event, timeout))
        if not self.answers:
            raise asyncio.TimeoutError()
        return _Message(self.answers.pop(0))


def run(coro):
    return asyncio.run(coro)


def _cog(bot=None):
    cog = EconomyCog.__new__(EconomyCog)   # skip __init__ (no tasks loop)
    cog.bot = bot
    return cog


def _text(ctx, index=-1):
    return ctx.sent[index][0][0]


async def _drain():
    for _ in range(20):
        await asyncio.sleep(0)


async def _setup_user(uid, wallet, house=10_000_000):
    await house_receive(house)
    await open_account(_Member(uid))
    await update_bank(uid, wallet)


# ── register ──────────────────────────────────────────────────────────

def test_register_already_registered():
    async def scenario():
        await open_account(_Member(1))
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "register", ctx)
        assert "มีบัญชีอยู่แล้ว" in _text(ctx)
    run(scenario())


def test_register_full_flow_opens_account():
    async def scenario():
        ctx = _Ctx(_Member(1))
        bot = _Bot(["บอส", "ชอบเล่นเกม"])
        await invoke_command(_cog(bot), "register", ctx)
        assert await is_registered(1)
        doc = (await economy._ref(1).get()).to_dict()
        assert doc["nickname"] == "บอส"
        assert doc["bio"] == "ชอบเล่นเกม"
        assert doc["username"] == "U"
    run(scenario())


def test_register_timeout_on_nickname():
    async def scenario():
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "register", ctx)   # no answers
        assert any("หมดเวลา" in s[0][0] for s in ctx.sent)
        assert not await is_registered(1)
    run(scenario())


# ── balance ───────────────────────────────────────────────────────────

def test_balance_shows_wallet_and_deposited():
    async def scenario():
        await _setup_user(1, 7_500)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "balance", ctx)
        em = ctx.sent[0][1]["embed"]
        values = " ".join(f.value for f in em.fields)
        assert "7,500" in values
    run(scenario())


def test_balance_other_member():
    async def scenario():
        await _setup_user(1, 1_000)
        await _setup_user(2, 3_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "balance", ctx, _Member(2))
        em = ctx.sent[0][1]["embed"]
        assert em.author.name == "💰 U"
    run(scenario())


# ── deposit ───────────────────────────────────────────────────────────

def test_deposit_success():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "deposit", ctx, "4000")
        await _drain()
        assert (await get_balance(_Member(1))) == [6_000, 4_000]
        assert await get_house_balance() == 10_000_000 + 4_000
    run(scenario())


def test_deposit_insufficient_funds():
    async def scenario():
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "deposit", ctx, "4000")
        assert "เงินในกระเป๋าไม่พอ" in _text(ctx)
    run(scenario())


def test_deposit_invalid_amount():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "deposit", ctx, "abc")
        assert ctx.sent, "must reply with an error"
        assert (await get_balance(_Member(1))) == [10_000, 0]   # untouched
    run(scenario())


# ── withdraw ──────────────────────────────────────────────────────────

def test_withdraw_success():
    async def scenario():
        await _setup_user(1, 10_000)
        await invoke_command(_cog(_Bot()), "deposit", ctx := _Ctx(_Member(1)), "4000")
        await invoke_command(_cog(_Bot()), "withdraw", ctx, "1500")
        await _drain()
        assert (await get_balance(_Member(1))) == [10_000 - 4_000 + 1_500, 4_000 - 1_500]
    run(scenario())


def test_withdraw_insufficient_deposited():
    async def scenario():
        await _setup_user(1, 10_000)                 # nothing deposited
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "withdraw", ctx, "1500")
        assert "ฝากไว้ในคลังไม่พอ" in _text(ctx)
    run(scenario())


def test_withdraw_house_broke():
    async def scenario():
        import bobcoin.bank.core as bank_core
        store = bank_core._db._store
        await _setup_user(1, 10_000)
        # deposit then drain the house below the amount
        await invoke_command(_cog(_Bot()), "deposit", ctx := _Ctx(_Member(1)), "5000")
        store["system/bank"]["balance"] = 100
        await invoke_command(_cog(_Bot()), "withdraw", ctx, "2000")
        assert "คลังหลวงแห้ง" in _text(ctx)
    run(scenario())


# ── give ──────────────────────────────────────────────────────────────

def test_give_success_logs_both_sides():
    async def scenario():
        await _setup_user(1, 10_000)
        await _setup_user(2, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "give", ctx, _Member(2), "3000")
        await _drain()
        assert (await get_balance(_Member(1))) == [7_000, 0]
        assert (await get_balance(_Member(2))) == [3_000, 0]
        give = await get_history(1)
        recv = await get_history(2)
        assert give[0]["cmd"] == "give" and give[0]["net"] == -3_000
        assert recv[0]["cmd"] == "receive" and recv[0]["net"] == 3_000
    run(scenario())


def test_give_unregistered_recipient():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "give", ctx, _Member(2), "1000")
        assert "ยังไม่ได้เปิดบัญชี" in _text(ctx)
        assert (await get_balance(_Member(1))) == [10_000, 0]   # untouched
    run(scenario())


def test_give_self_and_bot_rejected():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        # self-check is identity-based (member == ctx.author) like real dispatch
        await invoke_command(_cog(_Bot()), "give", ctx, ctx.author, "1000")
        assert "โอนให้ตัวเองไม่ได้" in _text(ctx)
        await invoke_command(_cog(_Bot()), "give", ctx, _BotMember(9), "1000")
        assert "โอนให้บอทไม่ได้" in _text(ctx)
    run(scenario())


def test_give_insufficient_sender():
    async def scenario():
        await _setup_user(1, 100)
        await _setup_user(2, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "give", ctx, _Member(2), "1000")
        assert "เงินในกระเป๋าไม่พอ" in _text(ctx)
    run(scenario())


# ── rob ───────────────────────────────────────────────────────────────

class _Random:
    def __init__(self, value, uniform_value=0.10):
        self.value = value
        self.uniform_value = uniform_value

    def random(self):
        return self.value

    def uniform(self, _a, _b):
        return self.uniform_value


def test_rob_requires_valid_target():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx)                    # None
        await invoke_command(_cog(_Bot()), "rob", ctx, ctx.author)        # self
        await invoke_command(_cog(_Bot()), "rob", ctx, _BotMember(9))     # bot
        assert all("ระบุ @user" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_rob_blocked_by_transfer_relation():
    async def scenario():
        from bobcoin.bank import log_history
        await _setup_user(1, 10_000)
        await _setup_user(2, 5_000)
        await log_history(1, {"cmd": "give", "amount": 100, "to_id": "2", "net": -100})
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx, _Member(2))
        assert "เคยโอนเงินให้กัน" in _text(ctx)
        assert (await get_balance(_Member(2))) == [5_000, 0]   # nothing moved
    run(scenario())


def test_rob_respects_persisted_cooldown():
    async def scenario():
        await _setup_user(1, 10_000)
        await _setup_user(2, 5_000)
        await set_cooldown(1, "rob_2")
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx, _Member(2))
        assert "ยังปล้น" in _text(ctx)
        assert (await get_balance(_Member(2))) == [5_000, 0]
    run(scenario())


def test_rob_target_too_poor():
    async def scenario():
        await _setup_user(1, 10_000)
        await _setup_user(2, 300)                    # < 500 minimum
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx, _Member(2))
        assert "จนเกินปล้น" in _text(ctx)
    run(scenario())


def test_rob_success_moves_money_atomically(monkeypatch):
    monkeypatch.setattr(economy, "random", _Random(0.0))      # < 0.35 → success
    async def scenario():
        await _setup_user(1, 10_000)
        await _setup_user(2, 5_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx, _Member(2))
        await _drain()
        stolen = max(int(5_000 * 0.10), 1)                    # uniform → 0.10
        assert (await get_balance(_Member(1))) == [10_000 + stolen, 0]
        assert (await get_balance(_Member(2))) == [5_000 - stolen, 0]
        rob = await get_history(1)
        robbed = await get_history(2)
        assert rob[0]["cmd"] == "rob" and rob[0]["net"] == stolen
        assert robbed[0]["cmd"] == "robbed" and robbed[0]["net"] == -stolen
    run(scenario())


def test_rob_failure_pays_penalty_to_target(monkeypatch):
    monkeypatch.setattr(economy, "random", _Random(0.9))      # ≥ 0.35 → caught
    async def scenario():
        await _setup_user(1, 10_000)
        await _setup_user(2, 5_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "rob", ctx, _Member(2))
        await _drain()
        penalty = min(int(10_000 * 0.10), 500_000)            # 1_000
        assert (await get_balance(_Member(1))) == [10_000 - penalty, 0]
        assert (await get_balance(_Member(2))) == [5_000 + penalty, 0]
        rob = await get_history(1)
        robbed = await get_history(2)
        assert rob[0]["cmd"] == "rob" and rob[0]["net"] == -penalty
        assert robbed[0]["cmd"] == "robbed" and robbed[0]["net"] == penalty
    run(scenario())
