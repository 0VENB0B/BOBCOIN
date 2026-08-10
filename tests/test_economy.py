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
from datetime import UTC, datetime

import discord
from conftest import invoke_command

import bobcoin.cogs.economy as economy
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
from bobcoin.cogs.economy import EconomyCog


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
        self.created_at = datetime(2021, 1, 1, tzinfo=UTC)
        self.joined_at = datetime(2021, 2, 2, tzinfo=UTC)


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
            raise TimeoutError()
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


# ── registration guard (cog_before_invoke) ─────────────────────────────

def _texts(ctx):
    """All text a command sent: content + embed title/description/fields."""
    parts = []
    for args, kw in ctx.sent:
        if args and args[0]:
            parts.append(args[0])
        em = kw.get("embed")
        if em is not None:
            parts.append(str(em.title or ""))
            parts.append(str(em.description or ""))
            parts.extend(f.value for f in em.fields)
    return " | ".join(parts)


def test_cog_before_invoke_blocks_unregistered_except_register():
    import types as _types

    async def scenario():
        cog = _cog(_Bot())
        ctx = _Ctx(_Member(1))                       # not registered
        ctx.command = _types.SimpleNamespace(name="balance")
        try:
            await cog.cog_before_invoke(ctx)
            raise AssertionError("must raise NotRegistered")
        except economy.NotRegistered:
            pass
        assert "ยังไม่มีบัญชี" in _texts(ctx)

        ctx2 = _Ctx(_Member(1))
        ctx2.command = _types.SimpleNamespace(name="register")
        await cog.cog_before_invoke(ctx2)            # allowed
        assert not ctx2.sent
    run(scenario())


# ── lottery ────────────────────────────────────────────────────────────

def test_lottery_validation_messages():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "lottery", ctx)                # no text
        await invoke_command(_cog(_Bot()), "lottery", ctx, "abc")         # non-decimal
        await invoke_command(_cog(_Bot()), "lottery", ctx, "123")         # wrong length
        assert any("ใส่เลขด้วย" in s[0][0] for s in ctx.sent)
        assert any("5 หลัก" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_lottery_valid_runs_game(monkeypatch):
    calls = []

    async def _fake(ctx, cost, text):
        calls.append((ctx, cost, text))

    monkeypatch.setattr(economy, "_run_lottery", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "lottery", ctx, "12345")
        assert calls and calls[0][1:] == (100, "12345")   # default cost 100
        await invoke_command(_cog(_Bot()), "lottery", ctx, "12345", "500")
        assert calls[-1][1:] == (500, "12345")
    run(scenario())


def test_lottery_bad_amount(monkeypatch):
    calls = []

    async def _fake(ctx, cost, text):
        calls.append((ctx, cost, text))

    monkeypatch.setattr(economy, "_run_lottery", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "lottery", ctx, "12345", "0")
        assert any("เงินเดิมพันต้องเป็นตัวเลข" in s[0][0] for s in ctx.sent)
        assert not calls
    run(scenario())


# ── slot / flip / bjgame ───────────────────────────────────────────────

def test_slot_runs_game(monkeypatch):
    calls = []

    async def _fake(ctx, amount):
        calls.append((ctx, amount))

    monkeypatch.setattr(economy, "_run_slot", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "slot", ctx, "500")
        assert calls and calls[0][1] == 500
    run(scenario())


def test_slot_invalid_amount(monkeypatch):
    calls = []

    async def _fake(ctx, amount):
        calls.append((ctx, amount))

    monkeypatch.setattr(economy, "_run_slot", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "slot", ctx, "abc")
        assert any("ใส่เงินที่พนัน" in s[0][0] or "ตัวเลข" in s[0][0] for s in ctx.sent)
        assert not calls
    run(scenario())


def test_flip_requires_valid_side(monkeypatch):
    calls = []

    async def _fake(ctx, amount, side):
        calls.append((ctx, amount, side))

    monkeypatch.setattr(economy, "_run_flip", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "flip", ctx, "3", "500")
        assert any("หัว = 1" in s[0][0] for s in ctx.sent)
        assert not calls
        await invoke_command(_cog(_Bot()), "flip", ctx, "1", "500")
        assert calls and calls[0][1:] == (500, "1")
    run(scenario())


def test_bjgame_runs_and_validates(monkeypatch):
    calls = []

    async def _fake(ctx, amount):
        calls.append((ctx, amount))

    monkeypatch.setattr(economy, "_run_bj", _fake)

    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "bjgame", ctx, "abc")
        assert any("ตัวเลข" in s[0][0] for s in ctx.sent)
        assert not calls
        await invoke_command(_cog(_Bot()), "bjgame", ctx, "2000")
        assert calls and calls[0][1] == 2000
    run(scenario())


def test_withdraw_invalid_amount_message():
    async def scenario():
        await _setup_user(1, 10_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "withdraw", ctx, "abc")
        assert any("จำนวนต้องเป็นตัวเลข" in s[0][0] for s in ctx.sent)
    run(scenario())


# ── daily / level / achievements / interest / streak / house / luck ────

def test_daily_success_then_cooldown():
    async def scenario():
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "daily", ctx)
        assert (await get_balance(_Member(1)))[0] > 0          # reward paid
        assert "รับเงินรายวันสำเร็จ" in _texts(ctx)
        await invoke_command(_cog(_Bot()), "daily", ctx)       # too soon
        assert "เก็บรายวันไปแล้ว" in _texts(ctx)
    run(scenario())


def test_daily_house_empty():
    async def scenario():
        import bobcoin.bank.core as bank_core
        store = bank_core._db._store
        await open_account(_Member(1))
        store["system/bank"] = {"balance": 0, "total_in": 0, "total_out": 0}
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "daily", ctx)
        assert any("ไม่มีเงินพอจ่ายรายวัน" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_level_shows_computed_level():
    async def scenario():
        import bobcoin.bank.core as bank_core
        await _setup_user(1, 0)
        bank_core._db._store["users/1"]["xp"] = 1000          # level 10
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "level", ctx)
        em = ctx.sent[0][1]["embed"]
        assert em.title == "⭐ Level 10"
    run(scenario())


def test_achievements_lists_owned_and_locked():
    async def scenario():
        from bobcoin.bank import grant_achievement
        await _setup_user(1, 0)
        await grant_achievement(1, "jackpot")
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "achievements", ctx)
        em = ctx.sent[0][1]["embed"]
        assert "Jackpot!" in em.description
        assert "🔒" in em.description
        assert "1/8" in em.footer.text
    run(scenario())


def test_interest_command_shows_daily_interest():
    async def scenario():
        await _setup_user(1, 200_000)
        await invoke_command(_cog(_Bot()), "deposit", _Ctx(_Member(1)), "100000")
        ctx2 = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "interest", ctx2)
        em = ctx2.sent[0][1]["embed"]
        values = " ".join(f.value for f in em.fields)
        assert "100,000" in values
        assert "150" in values                              # 0.15% of 100k
    run(scenario())


def test_streak_command_no_history_and_win():
    async def scenario():
        from bobcoin.bank import log_history
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "streak", ctx)
        assert "ยังไม่มีประวัติเกม" in _texts(ctx)

        await log_history(1, {"cmd": "flip", "net": 900})
        ctx2 = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "streak", ctx2)
        em = ctx2.sent[0][1]["embed"]
        assert "Win Streak" in em.fields[0].name
    run(scenario())


def test_streak_command_cold_streak_shows_mercy():
    async def scenario():
        from bobcoin.bank import log_history
        await _setup_user(1, 0)
        for _ in range(3):
            await log_history(1, {"cmd": "slot", "net": -100})
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "streak", ctx)
        em = ctx.sent[0][1]["embed"]
        assert "Cold Streak" in em.fields[0].name
    run(scenario())


def test_house_command_shows_stats():
    async def scenario():
        await house_receive(2_000_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "house", ctx)
        em = ctx.sent[0][1]["embed"]
        names = [f.name for f in em.fields]
        assert "💰 ยอดคงเหลือ" in names
        assert "📊 กำไรสุทธิ" in names
    run(scenario())


def test_luck_command_default_and_modified():
    async def scenario():
        from bobcoin.bank import set_user_luck
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "luck", ctx)
        em = ctx.sent[0][1]["embed"]
        assert "1.0x" in em.fields[0].value
        await set_user_luck(1, 3.5)
        ctx2 = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "luck", ctx2)
        assert "3.5x" in ctx2.sent[0][1]["embed"].fields[0].value
    run(scenario())


# ── admin commands: setluck / seed ─────────────────────────────────────

class _AdminBot:
    async def is_owner(self, user):
        return True


def _admin_cog():
    return _cog(_AdminBot())


def test_setluck_requires_member_and_sets_luck():
    async def scenario():
        from bobcoin.bank import get_user_luck
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        ctx.bot = _AdminBot()
        await invoke_command(_admin_cog(), "setluck", ctx)                 # no member
        assert "ระบุ user" in _texts(ctx)
        ctx2 = _Ctx(_Member(1))
        ctx2.bot = _AdminBot()
        await invoke_command(_admin_cog(), "setluck", ctx2, _Member(1), 8.0)
        assert (await get_user_luck(1)) == 8.0
        assert "luck = **8.0x**" in _texts(ctx2)
    run(scenario())


def test_setluck_clamps_extremes():
    async def scenario():
        from bobcoin.bank import get_user_luck
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        ctx.bot = _AdminBot()
        await invoke_command(_admin_cog(), "setluck", ctx, _Member(1), 999.0)
        assert (await get_user_luck(1)) == 200.0
    run(scenario())


def test_seed_requires_positive_amount_and_seeds_house():
    async def scenario():
        await house_receive(100_000)
        ctx = _Ctx(_Member(1))
        ctx.bot = _AdminBot()
        await invoke_command(_admin_cog(), "seed", ctx)                     # default 0
        assert "ใส่จำนวนเงิน" in _texts(ctx)
        before = await get_house_balance()
        ctx2 = _Ctx(_Member(1))
        ctx2.bot = _AdminBot()
        await invoke_command(_admin_cog(), "seed", ctx2, 250000)
        assert await get_house_balance() == before + 250_000
        assert "250,000" in _texts(ctx2)
    run(scenario())


def test_setluck_and_seed_reject_non_admin():
    from discord.ext import commands as _commands

    async def scenario():
        await _setup_user(1, 0)
        for command, args in [("setluck", (_Member(1), 2.0)), ("seed", (5000,))]:
            ctx = _Ctx(_Member(1))
            try:
                await invoke_command(_cog(_Bot()), command, ctx, *args)
                raise AssertionError(f"{command} must raise CheckFailure for non-admin")
            except _commands.CheckFailure:
                pass
    run(scenario())


# ── history ────────────────────────────────────────────────────────────

def test_history_empty():
    async def scenario():
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "history", ctx)
        assert any("ไม่มีประวัติ" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_history_formats_every_cmd_type():
    async def scenario():
        from bobcoin.bank import log_history
        await _setup_user(1, 0)
        for entry in [
            {"cmd": "slot", "symbols": "🍎 🍊 🍐", "net": -100},
            {"cmd": "flip", "choice": "หัว", "drawn": "ก้อย", "win": False, "net": -500},
            {"cmd": "lottery", "pick": "12345", "drawn": "54321", "match": "ไม่ถูก", "net": -100},
            {"cmd": "deposit", "amount": 1000, "net": 0},
            {"cmd": "withdraw", "amount": 500, "net": 0},
            {"cmd": "give", "amount": 200, "to_name": "Bob", "net": -200},
            {"cmd": "receive", "amount": 300, "from_name": "Alice", "net": 300},
            {"cmd": "interest", "amount": 50, "net": 50},
            {"cmd": "daily", "streak": 3, "reward": 1500, "net": 1500},
            {"cmd": "loan", "amount": 5000, "net": 5000},
            {"cmd": "repay", "amount": 2000, "net": -2000},
            {"cmd": "loan_interest", "amount": 30, "net": -30},
            {"cmd": "bj", "result": "win", "net": 800},
            {"cmd": "weird_cmd", "net": 0},
        ]:
            await log_history(1, entry)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "history", ctx)
        em = ctx.sent[0][1]["embed"]
        for needle in ["Slot", "Flip", "Lottery", "ฝาก", "ถอน", "โอน", "รับโอน",
                       "ดอกเบี้ย", "Daily", "กู้เงิน", "ชำระหนี้", "หนี้เพิ่ม",
                       "Blackjack", "weird_cmd", "คืนทุน"]:
            assert needle in em.description, needle
    run(scenario())


# ── loan / repay ───────────────────────────────────────────────────────

class _Thinking:
    deleted = False

    async def delete(self):
        self.deleted = True


class _LoanCtx:
    def __init__(self, author):
        self.author = author
        self.prefix = "$"
        self.channel = object()
        self.sent = []
        self.thinking = _Thinking()

    async def send(self, *a, **kw):
        self.sent.append((a, kw))
        return self.thinking


def _loan_cog():
    return _cog(_Bot())


def test_loan_info_embed():
    async def scenario():
        await _setup_user(1, 5_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx)
        em = ctx.sent[0][1]["embed"]
        names = [f.name for f in em.fields]
        assert "หนี้คงค้าง" in names and "วงเงินรวม" in names
    run(scenario())


def test_loan_invalid_amount():
    async def scenario():
        await _setup_user(1, 5_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx, "abc")
        assert any("ใส่จำนวนเงินที่ถูกต้อง" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_loan_within_limit_succeeds():
    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx, "10000")    # base limit 50k
        assert "กู้เงินสำเร็จ" in _texts(ctx)
        assert (await get_balance(_Member(1)))[0] == 15_000
    run(scenario())


def test_loan_over_limit_ai_approves(monkeypatch):
    async def _approve(uid, requested):
        return requested                      # AI fully approves
    monkeypatch.setattr(economy, "ai_loan_limit", _approve)

    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx, "100000")    # > base limit
        assert "กู้เงินสำเร็จ" in _texts(ctx)
        assert ctx.thinking.deleted is True
        assert (await get_balance(_Member(1)))[0] == 105_000
    run(scenario())


def test_loan_over_limit_ai_partial_deny(monkeypatch):
    async def _partial(uid, requested):
        return 5_000                              # AI caps far below request
    monkeypatch.setattr(economy, "ai_loan_limit", _partial)

    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx, "100000")
        assert "AI ไม่อนุมัติวงเงินนี้" in _texts(ctx)
        assert (await get_balance(_Member(1)))[0] == 5_000          # untouched
    run(scenario())


def test_loan_over_limit_ai_full_deny(monkeypatch):
    async def _deny(uid, requested):
        return 0
    monkeypatch.setattr(economy, "ai_loan_limit", _deny)

    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        ctx = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx, "100000")
        assert "ธนาคารไม่มีทุนสำรองพอ" in _texts(ctx)
        assert (await get_balance(_Member(1)))[0] == 5_000          # untouched
    run(scenario())


def test_loan_above_available_gets_error():
    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        ctx = _LoanCtx(_Member(1))
        # take a first loan to fill the base limit, then ask for way more
        await invoke_command(_loan_cog(), "loan", ctx, "50000")
        ctx2 = _LoanCtx(_Member(1))
        await invoke_command(_loan_cog(), "loan", ctx2, "999999999")
        # over limit → AI path runs (no key → rejected), request denied
        assert "AI กำลังพิจารณา" in _texts(ctx2)
        assert "ธนาคารไม่มีทุนสำรองพอ" in _texts(ctx2)
    run(scenario())


def test_repay_no_debt_messages():
    async def scenario():
        await _setup_user(1, 5_000)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "repay", ctx)              # no amount, no debt
        assert "ไม่มียอดหนี้ค้างอยู่เลย" in _texts(ctx)
        await invoke_command(_cog(_Bot()), "repay", ctx, "1000")      # amount but no debt
        assert "ไม่มียอดหนี้ค้างนะ" in _texts(ctx)
    run(scenario())


def test_repay_invalid_amount():
    async def scenario():
        await _setup_user(1, 5_000)
        await invoke_command(_loan_cog(), "loan", _LoanCtx(_Member(1)), "10000")
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "repay", ctx, "abc")
        assert any("ใส่จำนวนเงินที่ถูกต้อง" in s[0][0] for s in ctx.sent)
    run(scenario())


def test_repay_all_clears_debt():
    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        await invoke_command(_loan_cog(), "loan", _LoanCtx(_Member(1)), "10000")
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "repay", ctx, "all")
        assert "หนี้หมดแล้ว" in _texts(ctx)
        info = await economy.get_loan_info(1)
        assert info["loan_balance"] == 0
    run(scenario())


def test_repay_insufficient_wallet():
    async def scenario():
        import bobcoin.bank.core as bank_core
        await _setup_user(1, 5_000, house=100_000_000)
        await invoke_command(_loan_cog(), "loan", _LoanCtx(_Member(1)), "10000")
        bank_core._db._store["users/1"]["wallet"] = 0              # totally broke
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "repay", ctx, "5000")
        assert "เงินในกระเป๋าไม่พอ" in _texts(ctx)
    run(scenario())


def test_repay_partial_shows_remaining():
    async def scenario():
        await _setup_user(1, 5_000, house=100_000_000)
        await invoke_command(_loan_cog(), "loan", _LoanCtx(_Member(1)), "10000")
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "repay", ctx, "4000")
        assert "ชำระหนี้สำเร็จ" in _texts(ctx)
        info = await economy.get_loan_info(1)
        assert info["loan_balance"] == 6_000
    run(scenario())


def test_item_command():
    async def scenario():
        await _setup_user(1, 0)
        ctx = _Ctx(_Member(1))
        await invoke_command(_cog(_Bot()), "item", ctx)
        assert any("@watch" in s[0][0] for s in ctx.sent)
    run(scenario())


# ── leaderboard ────────────────────────────────────────────────────────

class _LBUser:
    def __init__(self, uid):
        self.id = uid
        self.display_name = f"User{uid}"


class _LBBot:
    def get_user(self, uid):
        return _LBUser(uid)

    async def fetch_user(self, uid):
        raise AssertionError("get_user should resolve")


def test_leaderboard_ranks_by_total_wealth():
    async def scenario():
        await _setup_user(1, 3_000)
        await _setup_user(2, 9_000)
        await _setup_user(3, 5_000)
        cog = _cog(_LBBot())
        ctx = _Ctx(_Member(1))
        await invoke_command(cog, "leaderboard", ctx, "5")
        em = ctx.sent[0][1]["embed"]
        values = " ".join(f.value for f in em.fields)
        assert "9,000" in values
        assert em.title.startswith("Top 5")
    run(scenario())


def test_leaderboard_defaults_to_3():
    async def scenario():
        await _setup_user(1, 100)
        await _setup_user(2, 200)
        await _setup_user(3, 300)
        await _setup_user(4, 400)
        cog = _cog(_LBBot())
        ctx = _Ctx(_Member(1))
        await invoke_command(cog, "leaderboard", ctx)
        assert len(ctx.sent[0][1]["embed"].fields) == 3
    run(scenario())


# ── _buy_role (shop / BD backend) ──────────────────────────────────────

class _ShopRole:
    def __init__(self, position, name="role"):
        self.position = position
        self.name = name

    def __ge__(self, other):
        return self.position >= other.position


class _ShopMember:
    def __init__(self, uid):
        self.id = uid
        self.display_name = f"M{uid}"
        self.added = []

    async def add_roles(self, role):
        if getattr(self, "fail", False):
            raise discord.Forbidden(_Response(), "nope")
        self.added.append(role)


class _Response:
    status = 403
    reason = "Forbidden"


class _ShopGuild:
    def __init__(self, me=None, owner=None):
        self.me = me
        self.owner = owner


class _ShopCtx:
    def __init__(self, author, guild):
        self.author = author
        self.guild = guild
        if self.guild.owner is None:
            self.guild.owner = author           # skip the top-role guard
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


def test_buy_role_requires_member_and_role():
    async def scenario():
        await _setup_user(1, 10_000)
        cog = _cog(_Bot())
        ctx = _ShopCtx(_Member(1), _ShopGuild())
        await cog._buy_role(ctx, None, _ShopRole(1), 1000)
        assert "ใส่ชื่อที่จะซื้อของให้" in _texts(ctx)
        await cog._buy_role(ctx, _ShopMember(2), None, 1000)
        assert "ใส่สิ่งของที่ต้องการ" in _texts(ctx)
    run(scenario())


def test_buy_role_blocks_role_above_bot():
    async def scenario():
        await _setup_user(1, 10_000)
        cog = _cog(_Bot())
        me = _ShopMember(9)
        me.top_role = _ShopRole(10)
        guild = _ShopGuild(me=me)
        ctx = _ShopCtx(_Member(1), guild)
        await cog._buy_role(ctx, _ShopMember(2), _ShopRole(20), 1000)
        assert "บอทไม่มีสิทธิ์" in _texts(ctx)
    run(scenario())


def test_buy_role_insufficient_funds():
    async def scenario():
        await _setup_user(1, 100)                    # < 1000 price
        cog = _cog(_Bot())
        ctx = _ShopCtx(_Member(1), _ShopGuild())
        await cog._buy_role(ctx, _ShopMember(2), _ShopRole(1), 1000)
        assert "เงินไม่พอ" in _texts(ctx)
        assert (await get_balance(_Member(1))) == [100, 0]
    run(scenario())


def test_buy_role_success_adds_role():
    async def scenario():
        await _setup_user(1, 10_000)
        cog = _cog(_Bot())
        member = _ShopMember(2)
        role = _ShopRole(1)
        ctx = _ShopCtx(_Member(1), _ShopGuild())
        await cog._buy_role(ctx, member, role, 1000)
        assert member.added == [role]
        assert (await get_balance(_Member(1))) == [9_000, 0]
        assert "was given" in _texts(ctx)
    run(scenario())


def test_buy_role_refunds_when_role_assignment_fails():
    async def scenario():
        await _setup_user(1, 10_000)
        cog = _cog(_Bot())
        member = _ShopMember(2)
        member.fail = True                           # add_roles raises Forbidden
        ctx = _ShopCtx(_Member(1), _ShopGuild())
        await cog._buy_role(ctx, member, _ShopRole(1), 1000)
        assert (await get_balance(_Member(1))) == [10_000, 0]    # refunded
        assert "คืนเงินแล้ว" in _texts(ctx)
    run(scenario())
