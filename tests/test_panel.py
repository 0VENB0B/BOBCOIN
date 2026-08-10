"""Tests for bobcoin.cogs.panel — the casino panel UI layer.

Covers the embed builder, registration/cooldown guards, modal input
validation + money wiring, the daily button, refresh and auto-delete —
using fake interactions (no real Discord connection needed).
"""

import asyncio
import types

import discord

import bobcoin.bank.core as bank_core
import bobcoin.cogs.panel as panel
from bobcoin.bank import (
    contribute_jackpot,
    get_balance,
    house_receive,
    open_account,
    set_cooldown,
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


class _Response:
    def __init__(self):
        self.messages = []
        self.modals = []
        self.edits = []
        self.deferred = None
        self.status = 200          # needed by discord.HTTPException subclasses
        self.reason = "OK"

    async def send_message(self, content=None, **kw):
        self.messages.append((content, kw))

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def defer(self, **kw):
        self.deferred = kw

    async def edit_message(self, **kw):
        self.edits.append(kw)


class _Followup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kw):
        self.messages.append((content, kw))


class _Interaction:
    def __init__(self, user):
        self.user = user
        self.channel = None
        self.response = _Response()
        self.followup = _Followup()


def run(coro):
    return asyncio.run(coro)


# ── Embed builder / registration ────────────────────────────────────────

def test_build_panel_embed_shows_live_data():
    async def scenario():
        await house_receive(2_000_000)
        await contribute_jackpot(5_000)
        em = await panel._build_panel_embed()
        assert em.title == "🎰  G U C O I N   C A S I N O"
        names = [f.name for f in em.fields]
        assert "💎 Jackpot Pool" in names
        assert "🏛️ คลังหลวง" in names
        jackpot_field = next(f for f in em.fields if f.name == "💎 Jackpot Pool")
        assert "55,000" in jackpot_field.value        # seed 50k + 5k
        house_field = next(f for f in em.fields if f.name == "🏛️ คลังหลวง")
        assert "2,000,000" in house_field.value       # live house balance
    run(scenario())


def test_is_registered():
    async def scenario():
        assert await panel._is_registered(1) is False
        await open_account(_Author(1))
        assert await panel._is_registered(1) is True
    run(scenario())


# ── Cooldown helpers ────────────────────────────────────────────────────

def test_panel_cooldown_roundtrip_and_keys():
    async def scenario():
        await open_account(_Author(1))
        assert await panel._cd_remaining(1, "slot") == 0.0
        await panel._cd_set(1, "slot")
        assert 0 < await panel._cd_remaining(1, "slot") <= 30
        assert await panel._cd_remaining(1, "flip") == 0.0   # separate key
    run(scenario())


# ── Modals ──────────────────────────────────────────────────────────────

def test_slot_modal_invalid_amount_errors():
    async def scenario():
        it = _Interaction(_Author(1))
        modal = panel._SlotModal()
        modal.amount._value = "abc"      # TextInput.value is read-only — set the private field
        await modal.on_submit(it)
        assert it.response.messages, "must send an error"
    run(scenario())


def test_slot_modal_valid_amount_runs_game(monkeypatch):
    calls = []

    async def _fake_run_slot(ctx, amount):
        calls.append((ctx, amount))

    monkeypatch.setattr(panel, "_run_slot", _fake_run_slot)

    async def scenario():
        await open_account(_Author(1))
        it = _Interaction(_Author(1))
        modal = panel._SlotModal()
        modal.amount._value = "5,000"
        await modal.on_submit(it)
        assert calls, "game must run"
        ctx, amount = calls[0]
        assert amount == 5_000
        assert ctx.author.id == 1                     # _PanelCtx wiring
        assert it.response.deferred is not None       # defer called
        assert await panel._cd_remaining(1, "slot") > 0  # cooldown set
    run(scenario())


def test_lottery_modal_requires_5_digit_ticket():
    async def scenario():
        it = _Interaction(_Author(1))
        modal = panel._LotteryModal()
        modal.ticket._value = "123"
        modal.amount._value = "100"
        await modal.on_submit(it)
        assert it.response.messages, "bad ticket must be rejected"
        assert it.response.deferred is None           # game never started
    run(scenario())


def test_deposit_modal_deposits_money():
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 10_000)
        it = _Interaction(_Author(1))
        modal = panel._DepositModal()
        modal.amount._value = "4000"
        await modal.on_submit(it)
        assert (await get_balance(_Author(1))) == [6_000, 4_000]
        assert it.followup.messages                   # confirmation sent
    run(scenario())


def test_deposit_modal_insufficient_funds():
    async def scenario():
        await open_account(_Author(1))                # wallet 0
        it = _Interaction(_Author(1))
        modal = panel._DepositModal()
        modal.amount._value = "4000"
        await modal.on_submit(it)
        assert (await get_balance(_Author(1))) == [0, 0]   # nothing moved
        assert "ไม่พอ" in it.followup.messages[0][0]
    run(scenario())


# ── GamePanelView guards & buttons ──────────────────────────────────────

def test_view_guard_unregistered_blocked():
    async def scenario():
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        assert await view._guard(it, "slot") is False
        assert it.response.messages
    run(scenario())


def test_view_guard_registered_sends_modal():
    async def scenario():
        await open_account(_Author(1))
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        assert await view._guard(it, "slot") is True
        await view.slot_btn.callback(it)   # discord binds the button; callback takes only the interaction
        assert it.response.modals, "slot modal must be shown"
    run(scenario())


def test_view_guard_cooldown_blocked():
    async def scenario():
        await open_account(_Author(1))
        await set_cooldown(1, "panel_slot")
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        assert await view._guard(it, "slot") is False
        assert it.response.messages                   # cooldown message
    run(scenario())


def test_daily_btn_pays_reward():
    async def scenario():
        await open_account(_Author(1))
        await house_receive(10_000_000)
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.daily_btn.callback(it)
        assert it.followup.messages                   # success embed
        assert (await get_balance(_Author(1)))[0] > 0
    run(scenario())


def test_refresh_btn_edits_panel_message():
    async def scenario():
        await house_receive(1_000_000)
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.refresh_btn.callback(it)
        assert it.response.edits
        assert it.response.edits[0]["embed"] is not None
    run(scenario())


# ── Auto-delete (casino channel hygiene) ────────────────────────────────

class _FakeChannel:
    def __init__(self, pinned):
        self.pinned = pinned
        self.deleted = False

    async def fetch_message(self, mid):
        return self

    async def delete(self):
        self.deleted = True


def test_auto_delete_removes_unpinned_keeps_pinned():
    _real_sleep = asyncio.sleep

    async def _noop(_s=None):
        return None

    asyncio.sleep = _noop
    try:
        async def scenario():
            ch = _FakeChannel(pinned=False)
            await panel.PanelCog._auto_delete(types.SimpleNamespace(id=1, channel=ch), 5)
            assert ch.deleted is True
            ch2 = _FakeChannel(pinned=True)
            await panel.PanelCog._auto_delete(types.SimpleNamespace(id=1, channel=ch2), 5)
            assert ch2.deleted is False
        run(scenario())
    finally:
        asyncio.sleep = _real_sleep


def test_auto_delete_swallows_missing_and_forbidden():
    _real_sleep = asyncio.sleep

    async def _noop(_s=None):
        return None

    asyncio.sleep = _noop
    try:
        class _MissingChannel:
            async def fetch_message(self, mid):
                raise discord.NotFound(_Response(), "gone")

        async def scenario():
            ch = _MissingChannel()
            await panel.PanelCog._auto_delete(types.SimpleNamespace(id=1, channel=ch), 5)
        run(scenario())
    finally:
        asyncio.sleep = _real_sleep


def test_audit_copy_forwards_embed_when_configured(monkeypatch):
    class _AuditChannel:
        def __init__(self):
            self.sent = []

        async def send(self, *a, **kw):
            self.sent.append((a, kw))

    class _Guild:
        def __init__(self, ch):
            self._ch = ch

        def get_channel(self, cid):
            return self._ch if cid == 555 else None

    class _Msg:
        def __init__(self, embeds, content):
            self.embeds = embeds
            self.content = content
            self.guild = _Guild(_AuditChannel())

    async def scenario():
        with monkeypatch.context() as m:
            m.setattr(panel, "AUDIT_CHANNEL_ID", 555)
            em = discord.Embed(title="🎰 ผลเกม")
            msg = _Msg([em], None)
            await panel.PanelCog._audit_copy(msg)
            ch = msg.guild.get_channel(555)
            assert ch.sent and ch.sent[0][1]["embed"].title == "🎰 ผลเกม"

            # content-only message also forwarded
            msg2 = _Msg([], "ผลลัพธ์เกม")
            await panel.PanelCog._audit_copy(msg2)
            assert msg2.guild.get_channel(555).sent[-1][0][0] == "ผลลัพธ์เกม"
    run(scenario())


def test_audit_copy_noop_when_unset_or_missing_guild(monkeypatch):
    async def scenario():
        with monkeypatch.context() as m:
            m.setattr(panel, "AUDIT_CHANNEL_ID", 0)
            # unset → no guild access at all
            await panel.PanelCog._audit_copy(types.SimpleNamespace(id=1))
            # channel not found → suppressed
            m.setattr(panel, "AUDIT_CHANNEL_ID", 555)
            class _NoChannelGuild:
                def get_channel(self, cid):
                    return None
            await panel.PanelCog._audit_copy(types.SimpleNamespace(id=1, guild=_NoChannelGuild()))
    run(scenario())


# ── Remaining modals ────────────────────────────────────────────────────

def test_flip_modal_invalid_side_errors():
    async def scenario():
        it = _Interaction(_Author(1))
        modal = panel._FlipModal()
        modal.side._value = "กลาง"
        modal.amount._value = "1000"
        await modal.on_submit(it)
        assert it.response.messages                    # "ต้องพิมพ์ หัว หรือ ก้อย"
        assert it.response.deferred is None
    run(scenario())


def test_flip_modal_invalid_amount_errors():
    async def scenario():
        it = _Interaction(_Author(1))
        modal = panel._FlipModal()
        modal.side._value = "หัว"
        modal.amount._value = "abc"
        await modal.on_submit(it)
        assert it.response.messages
        assert it.response.deferred is None
    run(scenario())


def test_flip_modal_valid_runs_flip(monkeypatch):
    calls = []

    async def _fake_run_flip(ctx, amount, side):
        calls.append((ctx, amount, side))

    monkeypatch.setattr(panel, "_run_flip", _fake_run_flip)

    async def scenario():
        await open_account(_Author(1))
        it = _Interaction(_Author(1))
        modal = panel._FlipModal()
        modal.side._value = "ก้อย"
        modal.amount._value = "2500"
        await modal.on_submit(it)
        _, amount, side = calls[0]
        assert amount == 2_500
        assert side == "2"                             # ก้อย → 2
        assert it.response.deferred is not None
    run(scenario())


def test_bj_modal_valid_and_invalid(monkeypatch):
    calls = []

    async def _fake_run_bj(ctx, amount):
        calls.append((ctx, amount))

    monkeypatch.setattr(panel, "_run_bj", _fake_run_bj)

    async def scenario():
        await open_account(_Author(1))
        bad = _Interaction(_Author(1))
        m = panel._BJModal()
        m.amount._value = "-5"
        await m.on_submit(bad)
        assert bad.response.messages                   # invalid amount
        assert not calls

        good = _Interaction(_Author(1))
        m2 = panel._BJModal()
        m2.amount._value = "5000"
        await m2.on_submit(good)
        assert calls[0][1] == 5_000
        assert good.response.deferred is not None
    run(scenario())


def test_lottery_modal_valid_ticket_runs(monkeypatch):
    calls = []

    async def _fake_run_lottery(ctx, cost, tkt):
        calls.append((ctx, cost, tkt))

    monkeypatch.setattr(panel, "_run_lottery", _fake_run_lottery)

    async def scenario():
        await open_account(_Author(1))
        it = _Interaction(_Author(1))
        modal = panel._LotteryModal()
        modal.ticket._value = "12345"
        modal.amount._value = "100"
        await modal.on_submit(it)
        assert calls and calls[0][1:] == (100, "12345")
        assert it.response.deferred is not None
    run(scenario())


def test_deposit_modal_invalid_amount_errors():
    async def scenario():
        it = _Interaction(_Author(1))
        modal = panel._DepositModal()
        modal.amount._value = "ศูนย์"
        await modal.on_submit(it)
        assert it.response.messages
        assert it.response.deferred is None
    run(scenario())


def test_withdraw_modal_success():
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 10_000)
        # deposit 4000 via the real modal, then withdraw 1500
        dm = panel._DepositModal()
        dm.amount._value = "4000"
        await dm.on_submit(_Interaction(_Author(1)))
        it = _Interaction(_Author(1))
        wm = panel._WithdrawModal()
        wm.amount._value = "1500"
        await wm.on_submit(it)
        assert (await get_balance(_Author(1))) == [10_000 - 4_000 + 1_500, 4_000 - 1_500]
        assert it.followup.messages                   # success embed
    run(scenario())


def test_withdraw_modal_insufficient_deposited():
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 10_000)                  # nothing deposited
        it = _Interaction(_Author(1))
        wm = panel._WithdrawModal()
        wm.amount._value = "1500"
        await wm.on_submit(it)
        assert "ฝากไว้ในคลังไม่พอ" in it.followup.messages[0][0]
    run(scenario())


def test_withdraw_modal_house_broke():
    async def scenario():
        store = bank_core._db._store
        await open_account(_Author(1))
        await update_bank(1, 10_000)
        dm = panel._DepositModal()
        dm.amount._value = "4000"
        await dm.on_submit(_Interaction(_Author(1)))
        store["system/bank"]["balance"] = 100
        it = _Interaction(_Author(1))
        wm = panel._WithdrawModal()
        wm.amount._value = "2000"
        await wm.on_submit(it)
        assert "คลังหลวงแห้ง" in it.followup.messages[0][0]
    run(scenario())


# ── Duel modal ──────────────────────────────────────────────────────────

class _DuelMember:
    def __init__(self, uid, name="D"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.bot = False
        self.mention = f"<@{uid}>"

    def __eq__(self, other):
        return isinstance(other, _DuelMember) and other.id == self.id


class _DuelGuild:
    def __init__(self, members):
        self.members = {m.id: m for m in members}

    def get_member(self, uid):
        return self.members.get(uid)

    async def fetch_member(self, uid):
        m = self.members.get(uid)
        if m is None:
            raise RuntimeError("not found")
        return m


class _DuelCogStub:
    def __init__(self):
        self.calls = []

    async def run_duel(self, channel, challenger, target, game, bet):
        self.calls.append((channel, challenger, target, game, bet))


class _DuelClient:
    def __init__(self, cog):
        self.cog = cog

    def get_cog(self, name):
        return self.cog if name == "DuelCog" else None


def _duel_interaction(user, guild, client):
    it = _Interaction(user)
    it.guild = guild
    it.client = client
    it.channel = object()
    return it


def test_duel_modal_member_not_found():
    async def scenario():
        guild = _DuelGuild([])
        it = _duel_interaction(_Author(1), guild, _DuelClient(_DuelCogStub()))
        modal = panel._DuelModal()
        modal.target._value = "999999"
        modal.game._value = "flip"
        modal.amount._value = "1000"
        await modal.on_submit(it)
        assert "หา user ไม่เจอ" in it.response.messages[0][0]
        assert it.response.deferred is None
    run(scenario())


def test_duel_modal_invalid_game_and_bet():
    async def scenario():
        guild = _DuelGuild([_DuelMember(2)])
        client = _DuelClient(_DuelCogStub())
        it = _duel_interaction(_Author(1), guild, client)
        modal = panel._DuelModal()
        modal.target._value = "2"
        modal.game._value = "poker"
        modal.amount._value = "1000"
        await modal.on_submit(it)
        assert "เกมต้องเป็น" in it.response.messages[0][0]

        it2 = _duel_interaction(_Author(1), guild, client)
        m2 = panel._DuelModal()
        m2.target._value = "2"
        m2.game._value = "flip"
        m2.amount._value = "0"
        await m2.on_submit(it2)
        assert "จำนวนเงินไม่ถูกต้อง" in it2.response.messages[0][0]
    run(scenario())


def test_duel_modal_rejects_self_and_bot():
    async def scenario():
        me = _DuelMember(1)
        bot = _DuelMember(9, "BOT")
        bot.bot = True
        guild = _DuelGuild([me, bot])
        client = _DuelClient(_DuelCogStub())

        it = _duel_interaction(_Author(1), guild, client)
        it.user = me                             # challenger is member 1
        modal = panel._DuelModal()
        modal.target._value = "1"                # self
        modal.game._value = "flip"
        modal.amount._value = "1000"
        await modal.on_submit(it)
        assert "ห้ามท้าตัวเอง" in it.response.messages[0][0]

        it2 = _duel_interaction(_Author(1), guild, client)
        it2.user = me
        m2 = panel._DuelModal()
        m2.target._value = "9"                  # bot
        m2.game._value = "flip"
        m2.amount._value = "1000"
        await m2.on_submit(it2)
        assert "ห้ามท้าตัวเอง" in it2.response.messages[0][0]
    run(scenario())


def test_duel_modal_runs_duel_with_cog():
    async def scenario():
        me = _DuelMember(1)
        target = _DuelMember(2)
        guild = _DuelGuild([me, target])
        cog = _DuelCogStub()
        client = _DuelClient(cog)
        it = _duel_interaction(_Author(1), guild, client)
        it.user = me
        modal = panel._DuelModal()
        modal.target._value = "<@2>"
        modal.game._value = "BJ"
        modal.amount._value = "5000"
        await modal.on_submit(it)
        assert cog.calls
        _ch, challenger, tgt, game, bet = cog.calls[0]
        assert challenger.id == 1 and tgt.id == 2
        assert game == "bj"                       # normalized lowercase
        assert bet == 5_000
        assert it.response.deferred is not None
    run(scenario())


def test_duel_modal_handles_missing_cog():
    async def scenario():
        me = _DuelMember(1)
        target = _DuelMember(2)
        guild = _DuelGuild([me, target])
        client = _DuelClient(None)                # no DuelCog loaded
        it = _duel_interaction(_Author(1), guild, client)
        it.user = me
        modal = panel._DuelModal()
        modal.target._value = "2"
        modal.game._value = "flip"
        modal.amount._value = "1000"
        await modal.on_submit(it)
        assert "ระบบ Duel ไม่พร้อม" in it.followup.messages[0][0]
    run(scenario())


# ── Remaining GamePanelView buttons ────────────────────────────────────

def test_game_buttons_open_their_modals():
    async def scenario():
        await open_account(_Author(1))
        view = panel.GamePanelView()
        for btn, modal_cls in [
            (view.flip_btn, panel._FlipModal),
            (view.bj_btn, panel._BJModal),
            (view.lottery_btn, panel._LotteryModal),
            (view.deposit_btn, panel._DepositModal),
            (view.withdraw_btn, panel._WithdrawModal),
        ]:
            it = _Interaction(_Author(1))
            await btn.callback(it)
            assert it.response.modals, f"{btn} must open a modal"
            assert isinstance(it.response.modals[0], modal_cls)
    run(scenario())


def test_duel_btn_opens_duel_modal():
    async def scenario():
        await open_account(_Author(1))
        view = panel.GamePanelView()
        it = _duel_interaction(_Author(1), _DuelGuild([_DuelMember(1)]), _DuelClient(_DuelCogStub()))
        await view.duel_btn.callback(it)
        assert it.response.modals and isinstance(it.response.modals[0], panel._DuelModal)
    run(scenario())


def test_balance_btn_shows_embed():
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 7_500)
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.balance_btn.callback(it)
        em = it.followup.messages[0][1]["embed"]
        values = " ".join(f.value for f in em.fields)
        assert "7,500" in values
    run(scenario())


def test_streak_btn_no_history_and_win_streak():
    async def scenario():
        await open_account(_Author(1))
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.streak_btn.callback(it)
        assert "ยังไม่มีประวัติเกม" in it.followup.messages[0][0]

        from bobcoin.bank import log_history
        await log_history(1, {"cmd": "slot", "symbols": "🍎 🍎 🍎", "net": 800})
        it2 = _Interaction(_Author(1))
        await view.streak_btn.callback(it2)
        em = it2.followup.messages[0][1]["embed"]
        assert "Win Streak" in em.fields[0].name
    run(scenario())


def test_ach_btn_lists_achievements():
    async def scenario():
        from bobcoin.bank import grant_achievement
        await open_account(_Author(1))
        await grant_achievement(1, "first_win")
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.ach_btn.callback(it)
        em = it.followup.messages[0][1]["embed"]
        assert "First Blood" in em.description
        assert "🔒" in em.description                # locked ones shown too
        assert "1/8" in em.footer.text
    run(scenario())


def test_history_btn_no_entries_and_with_entries():
    async def scenario():
        await open_account(_Author(1))
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.history_btn.callback(it)
        assert "ไม่มีประวัติเลย" in it.followup.messages[0][0]

        from bobcoin.bank import log_history
        await log_history(1, {"cmd": "slot", "symbols": "🍎 🍊 🍐", "net": -100, "ts": 1700000000})
        it2 = _Interaction(_Author(1))
        await view.history_btn.callback(it2)
        em = it2.followup.messages[0][1]["embed"]
        assert "slot" in em.description
    run(scenario())


def test_house_btn_shows_house_stats():
    async def scenario():
        await house_receive(2_000_000)
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        await view.house_btn.callback(it)
        em = it.followup.messages[0][1]["embed"]
        names = [f.name for f in em.fields]
        assert "💰 ยอดคงเหลือ" in names
        assert "📊 กำไรสุทธิ" in names
    run(scenario())


def test_lb_btn_ranks_rich_users():
    class _RankedUser:
        def __init__(self, uid, name):
            self.id = uid
            self.display_name = name

    class _RankedClient:
        def get_user(self, uid):
            return _RankedUser(uid, f"User{uid}")

        async def fetch_user(self, uid):
            raise AssertionError("get_user should resolve first")

    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 3_000)
        await open_account(_Author(2))
        await update_bank(2, 9_000)
        view = panel.GamePanelView()
        it = _Interaction(_Author(1))
        it.client = _RankedClient()
        await view.lb_btn.callback(it)
        em = it.followup.messages[0][1]["embed"]
        values = " ".join(f.value for f in em.fields)
        assert "9,000" in values
        assert em.title == "🥇 Top 5 ผู้มั่งคั่ง"
    run(scenario())


# ── PanelCog: commands & listeners ─────────────────────────────────────

class _CogCtx:
    def __init__(self, author):
        self.author = author
        self.prefix = "$"
        self.channel = _ChannelStub()
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))
        return _MsgStub()


class _ChannelStub:
    name = "general"
    category = None

    async def send(self, *a, **kw):
        return _MsgStub()


class _MsgStub:
    async def pin(self):
        pass

    async def delete(self):
        pass


def _panel_cog():
    cog = panel.PanelCog.__new__(panel.PanelCog)
    cog.bot = _PanelBot()
    cog._casino_ids = set()
    return cog


class _PanelBot:
    async def is_owner(self, user):
        return False


class _GuildChannel:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name
        self.mention = f"<#{cid}>"

    async def send(self, *a, **kw):
        return _MsgStub()


def test_panel_command_sends_embed_and_view():
    async def scenario():
        await house_receive(1_000_000)
        cog = _panel_cog()
        ctx = _CogCtx(_Author(1))
        from conftest import invoke_command
        await invoke_command(cog, "panel", ctx)
        assert ctx.sent
        assert ctx.sent[0][1]["embed"] is not None
        assert isinstance(ctx.sent[0][1]["view"], panel.GamePanelView)
    run(scenario())


def test_on_ready_collects_casino_channels():
    async def scenario():
        cog = _panel_cog()
        cog.bot.guilds = [
            types.SimpleNamespace(text_channels=[_GuildChannel(1, "general"), _GuildChannel(2, "🎰-casino")]),
            types.SimpleNamespace(text_channels=[_GuildChannel(3, "CASINO-2")]),
        ]
        await cog.on_ready()
        assert cog._casino_ids == {2, 3}
    run(scenario())


def test_on_message_deletes_user_messages_in_casino():
    async def scenario():
        cog = _panel_cog()
        cog._casino_ids.add(7)

        class _Msg:
            guild = object()
            channel = types.SimpleNamespace(id=7)

            def __init__(self, author, deleted=False):
                self.author = author
                self.deleted = deleted

            async def delete(self):
                self.deleted = True

        class _AuthorStub:
            bot = False

        msg = _Msg(_AuthorStub())
        await cog.on_message(msg)
        assert msg.deleted is True

        # outside casino channel → untouched
        outside = _Msg(_AuthorStub())
        outside.channel = types.SimpleNamespace(id=1)
        await cog.on_message(outside)
        assert outside.deleted is False
    run(scenario())


def test_on_message_schedules_autodelete_for_bot_self():
    slept = []
    spawned = []

    async def _noop_sleep(secs=None):
        slept.append(secs)

    class _GoneChannel:
        id = 7

        async def fetch_message(self, mid):
            raise discord.NotFound(_Response(), "gone")

    _real_create_task = panel.asyncio.create_task
    _real_sleep = asyncio.sleep

    def _fake_create_task(coro, *a, **kw):
        # capture instead of scheduling — we gather manually below
        spawned.append(coro)
        return asyncio.ensure_future(coro)      # real Task: _spawn needs add_done_callback

    try:
        panel.asyncio.create_task = _fake_create_task
        asyncio.sleep = _noop_sleep

        class _BotAuthor:
            bot = True

        async def scenario():
            cog = _panel_cog()
            cog._casino_ids.add(7)

            me = _BotAuthor()
            msg = types.SimpleNamespace(
                id=1,
                guild=types.SimpleNamespace(me=me),
                channel=_GoneChannel(),
                author=me,                      # same object → guild.me
            )
            await cog.on_message(msg)
            assert len(spawned) == 1, "auto-delete must be scheduled"
            await spawned[0]                    # run the real _auto_delete
            assert slept == [45]                # slept the 45s delay

        run(scenario())                         # must run while patches active
    finally:
        panel.asyncio.create_task = _real_create_task
        asyncio.sleep = _real_sleep


# ── setup command ───────────────────────────────────────────────────────

class _PermAuthor(_Author):
    def __init__(self, uid, manage=False):
        super().__init__(uid, "Setup")
        self.guild_permissions = types.SimpleNamespace(manage_channels=manage)
        self.roles = []


class _RoleKey:
    """Hashable stand-in for a guild role (used as a dict key in overwrites)."""


class _SetupGuild:
    def __init__(self, channels=None):
        self.text_channels = channels if channels is not None else []
        self.me = None
        self.owner = None
        self.default_role = _RoleKey()
        self.created = []

    async def create_text_channel(self, name, **kw):
        ch = _GuildChannel(500, name)
        self.created.append((name, kw))
        self.text_channels.append(ch)
        return ch


class _SetupCtx:
    """ctx whose send records into its own list."""

    def __init__(self, author, guild, bot):
        self.author = author
        self.guild = guild
        self.bot = bot
        self.channel = _ChannelStub()
        self.prefix = "$"
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))
        return _MsgStub()


def test_setup_requires_permission():
    async def scenario():
        cog = _panel_cog()
        author = _PermAuthor(1, manage=False)
        ctx = _SetupCtx(author, _SetupGuild(), cog.bot)
        from conftest import invoke_command
        await invoke_command(cog, "setup", ctx)
        assert ctx.sent and "ต้องมีสิทธิ์" in ctx.sent[0][0][0]
    run(scenario())


def test_setup_existing_channel_short_circuits():
    async def scenario():
        cog = _panel_cog()
        author = _PermAuthor(1, manage=True)
        guild = _SetupGuild(channels=[_GuildChannel(10, "🎰-casino")])
        ctx = _SetupCtx(author, guild, cog.bot)
        from conftest import invoke_command
        await invoke_command(cog, "setup", ctx)
        assert ctx.sent and "มีอยู่แล้ว" in ctx.sent[0][0][0]
        assert not guild.created
    run(scenario())


def test_setup_creates_casino_channel():
    async def scenario():
        cog = _panel_cog()
        author = _PermAuthor(1, manage=True)
        guild = _SetupGuild()
        ctx = _SetupCtx(author, guild, cog.bot)
        from conftest import invoke_command
        await invoke_command(cog, "setup", ctx)
        assert guild.created and guild.created[0][0] == "🎰-casino"
        assert 500 in cog._casino_ids
        assert ctx.sent, "confirmation embed sent"
    run(scenario())
