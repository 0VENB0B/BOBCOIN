"""Tests for bobcoin.cogs.panel — the casino panel UI layer.

Covers the embed builder, registration/cooldown guards, modal input
validation + money wiring, the daily button, refresh and auto-delete —
using fake interactions (no real Discord connection needed).
"""

import asyncio
import types

import pytest

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
