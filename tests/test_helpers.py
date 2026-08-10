"""Tests for bobcoin.helpers: parse_positive_int, parse_amount_or_reply, is_bot_admin."""

import asyncio
import types

import bobcoin.helpers as helpers
from bobcoin.settings import MAX_BET


def run(coro):
    return asyncio.run(coro)


# ── parse_positive_int ──────────────────────────────────────────────────

def test_valid_amounts():
    assert helpers.parse_positive_int("1000") == 1000
    assert helpers.parse_positive_int("1,000") == 1000
    assert helpers.parse_positive_int(" 500 ") == 500
    assert helpers.parse_positive_int(500) == 500
    assert helpers.parse_positive_int("1") == 1
    assert helpers.parse_positive_int("1,234,567") == 1_234_567


def test_invalid_amounts():
    assert helpers.parse_positive_int("0") is None
    assert helpers.parse_positive_int("-5") is None
    assert helpers.parse_positive_int("abc") is None
    assert helpers.parse_positive_int("") is None
    assert helpers.parse_positive_int(None) is None
    assert helpers.parse_positive_int(True) is None
    assert helpers.parse_positive_int(False) is None
    assert helpers.parse_positive_int(0) is None
    assert helpers.parse_positive_int(-10) is None
    assert helpers.parse_positive_int("12ab") is None
    assert helpers.parse_positive_int("1 000") is None  # inner space not a comma


def test_max_bet_boundary():
    assert helpers.parse_positive_int("1000000000") == 1_000_000_000  # exactly MAX_BET
    assert helpers.parse_positive_int("1000000001") is None  # over MAX_BET
    assert helpers.parse_positive_int(str(MAX_BET)) == MAX_BET
    assert helpers.parse_positive_int(str(MAX_BET + 1)) is None
    # absurdly long input must be rejected before int() parsing
    assert helpers.parse_positive_int("9" * 30) is None


def test_custom_max_value():
    assert helpers.parse_positive_int("5", max_value=5) == 5
    assert helpers.parse_positive_int("6", max_value=5) is None
    assert helpers.parse_positive_int("10", max_value=5) is None
    assert helpers.parse_positive_int("1", max_value=1) == 1


def test_float_inputs_coerced_via_int():
    assert helpers.parse_positive_int(12.9) == 12
    assert helpers.parse_positive_int(0.5) is None  # int(0.5) == 0 → rejected


# ── parse_amount_or_reply ───────────────────────────────────────────────

class _FakeMsg:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


def _fake_ctx():
    return types.SimpleNamespace(author=types.SimpleNamespace(id=1))


def test_parse_amount_or_reply_missing_message():
    ctx = _fake_ctx()
    msg = _FakeMsg()
    ctx.send = msg.send
    assert run(helpers.parse_amount_or_reply(ctx, None, "กรุณาใส่จำนวน!")) is None
    assert msg.sent == ["กรุณาใส่จำนวน!"]


def test_parse_amount_or_reply_invalid_default_message():
    ctx = _fake_ctx()
    msg = _FakeMsg()
    ctx.send = msg.send
    result = run(helpers.parse_amount_or_reply(ctx, "abc", "กรุณาใส่จำนวน!"))
    assert result is None
    assert msg.sent == [f"จำนวนต้องเป็นตัวเลข 1 ถึง {MAX_BET:,}"]


def test_parse_amount_or_reply_invalid_custom_message():
    ctx = _fake_ctx()
    msg = _FakeMsg()
    ctx.send = msg.send
    result = run(helpers.parse_amount_or_reply(ctx, "-5", "x", "ต้องเป็นบวก!"))
    assert result is None
    assert msg.sent == ["ต้องเป็นบวก!"]


def test_parse_amount_or_reply_success_no_message():
    ctx = _fake_ctx()
    msg = _FakeMsg()
    ctx.send = msg.send
    assert run(helpers.parse_amount_or_reply(ctx, "500", "x")) == 500
    assert msg.sent == []


# ── is_bot_admin ────────────────────────────────────────────────────────

class _Role:
    def __init__(self, rid):
        self.id = rid


class _Author:
    def __init__(self, uid, role_ids=()):
        self.id = uid
        self.roles = [_Role(r) for r in role_ids]


class _Bot:
    def __init__(self, is_owner_result=False, raise_on_owner=False):
        self._res = is_owner_result
        self._raise = raise_on_owner

    async def is_owner(self, author):
        if self._raise:
            raise RuntimeError("no connection")
        return self._res


def _ctx(author, bot):
    return types.SimpleNamespace(author=author, bot=bot)


def test_is_bot_admin_owner_id():
    old = helpers.BOT_OWNER_ID
    try:
        helpers.BOT_OWNER_ID = 123
        ctx = _ctx(_Author(123), _Bot())
        assert run(helpers.is_bot_admin(ctx)) is True
    finally:
        helpers.BOT_OWNER_ID = old


def test_is_bot_admin_discord_owner():
    old = helpers.BOT_OWNER_ID
    try:
        helpers.BOT_OWNER_ID = 999  # not the direct owner
        ctx = _ctx(_Author(1), _Bot(is_owner_result=True))
        assert run(helpers.is_bot_admin(ctx)) is True
    finally:
        helpers.BOT_OWNER_ID = old


def test_is_bot_admin_owner_check_error_falls_through():
    old = helpers.BOT_OWNER_ID
    try:
        helpers.BOT_OWNER_ID = 999
        ctx = _ctx(_Author(1), _Bot(raise_on_owner=True))
        assert run(helpers.is_bot_admin(ctx)) is False
    finally:
        helpers.BOT_OWNER_ID = old


def test_is_bot_admin_admin_role():
    old = helpers.BOT_OWNER_ID
    old_roles = helpers.BOT_ADMIN_ROLE_IDS
    try:
        helpers.BOT_OWNER_ID = 999
        helpers.BOT_ADMIN_ROLE_IDS = frozenset()
        ctx = _ctx(_Author(1, role_ids=[10, 20]), _Bot())
        assert run(helpers.is_bot_admin(ctx)) is False  # no admin roles configured
        helpers.BOT_ADMIN_ROLE_IDS = frozenset({20})
        assert run(helpers.is_bot_admin(ctx)) is True   # matches role id
        assert run(helpers.is_bot_admin(_ctx(_Author(2, role_ids=[30]), _Bot()))) is False
    finally:
        helpers.BOT_OWNER_ID = old
        helpers.BOT_ADMIN_ROLE_IDS = old_roles


def test_is_bot_admin_no_roles_no_match():
    old = helpers.BOT_OWNER_ID
    old_roles = helpers.BOT_ADMIN_ROLE_IDS
    try:
        helpers.BOT_OWNER_ID = 999
        helpers.BOT_ADMIN_ROLE_IDS = frozenset({7})
        ctx = _ctx(_Author(1), _Bot())  # author has no roles at all
        assert run(helpers.is_bot_admin(ctx)) is False
    finally:
        helpers.BOT_OWNER_ID = old
        helpers.BOT_ADMIN_ROLE_IDS = old_roles


# ── settings parsing (P2 #12 / #11 / #8) ───────────────────────────────

def test_parse_optional_int_bad_value_returns_zero(monkeypatch):
    import bobcoin.settings as settings
    assert settings._parse_optional_int("") == 0
    assert settings._parse_optional_int("abc") == 0
    assert settings._parse_optional_int(None) == 0
    assert settings._parse_optional_int("555") == 555


def test_settings_defaults_and_env_override(monkeypatch):
    import bobcoin.settings as settings
    assert settings.AUDIT_CHANNEL_ID == 0            # default disabled
    assert settings.SLOT_JACKPOT_BASE == 8 / 512     # default 1.56%


def test_env_vars_drive_settings(monkeypatch):
    import importlib

    import bobcoin.settings as settings
    monkeypatch.setenv("GUCOIN_AUDIT_CHANNEL_ID", "555")
    monkeypatch.setenv("GUCOIN_SLOT_JACKPOT_BASE", "0.02")
    importlib.reload(settings)
    try:
        assert settings.AUDIT_CHANNEL_ID == 555
        assert settings.SLOT_JACKPOT_BASE == 0.02
    finally:
        monkeypatch.delenv("GUCOIN_AUDIT_CHANNEL_ID", raising=False)
        monkeypatch.delenv("GUCOIN_SLOT_JACKPOT_BASE", raising=False)
        importlib.reload(settings)


def test_env_vars_ignore_garbage(monkeypatch):
    import importlib

    import bobcoin.settings as settings
    monkeypatch.setenv("GUCOIN_AUDIT_CHANNEL_ID", "not-a-number")
    importlib.reload(settings)
    try:
        assert settings.AUDIT_CHANNEL_ID == 0
    finally:
        monkeypatch.delenv("GUCOIN_AUDIT_CHANNEL_ID", raising=False)
        importlib.reload(settings)
