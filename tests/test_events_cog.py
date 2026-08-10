"""Tests for bobcoin.cogs.events — intent handling + message listeners.

_handle_game_intent rewrites the message into a real command and replays it
through process_commands (slot/flip/lottery, all-in/half/no-amount, missing
ticket); on_message covers bot-ping chat, $reaction, replied-to-bot context
and swear roasts; on_command_error maps every error type to the right reply.
"""

import asyncio
import types

import discord
import pytest
from discord.ext import commands

import bobcoin.cogs.events as events
from bobcoin.cogs.events import EventsCog
from bobcoin.settings import COMMAND_PREFIX
from bobcoin.bank import get_balance, open_account, update_bank


class _Avatar:
    url = "http://avatar.example/x.png"


class _Author:
    def __init__(self, uid, name="User"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.display_avatar = _Avatar()
        self.bot = False


class _BotAuthor(_Author):
    def __init__(self, uid):
        super().__init__(uid, "BOB")
        self.bot = True


class _BotUser:
    def __init__(self, uid):
        self.id = uid
        self.mention = f"<@{uid}>"


class _Bot:
    def __init__(self, uid=100):
        self.user = _BotUser(uid)
        self.processed = []
        self.presence = []

    async def process_commands(self, msg):
        self.processed.append(msg)

    async def change_presence(self, **kw):
        self.presence.append(kw)


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Channel:
    def __init__(self):
        self.sent = []
        self.typing_used = 0

    def typing(self):
        self.typing_used += 1
        return _Typing()

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class _Msg:
    def __init__(self, content, author, mentions=None, reference=None):
        self.content = content
        self.author = author
        self.mentions = mentions if mentions is not None else []
        self.reference = reference
        self.channel = _Channel()
        self.reactions = []
        self.replies = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def reply(self, content=None, **kw):
        self.replies.append((content, kw))


class _Reference:
    def __init__(self, resolved):
        self.resolved = resolved


class _Ctx:
    def __init__(self):
        self.sent = []
        self.command = None

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


def _mention(uid):
    return f"<@{uid}>"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []

    async def _ai(system, messages, fallback="", max_tokens=150):
        calls.append((system, messages, fallback, max_tokens))
        return fallback or "ตอบกลับ"

    monkeypatch.setattr(events, "call_ai", _ai)
    return calls


def _cog(bot=None):
    return EventsCog(bot or _Bot())


# ── on_message: basic routing ─────────────────────────────────────────

def test_on_message_ignores_bot_messages(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg("$reaction สล็อต", _BotAuthor(200), mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert msg.reactions == []
        assert msg.replies == []
        assert bot.processed == []
        assert fake_ai == []                        # nothing even reached AI
    run(scenario())


def test_on_message_adds_reaction_to_keyword(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg("$reaction ช่วยหน่อย", _Author(1))
        await _cog(bot).on_message(msg)
        assert msg.reactions == ["<:Discord:895553740793339986>"]
    run(scenario())


def test_on_message_swear_gets_roast(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg("มึงโง่เหรอ", _Author(1))
        await _cog(bot).on_message(msg)
        assert msg.replies, "must roast the swearer"
        assert fake_ai, "roast must come from AI"
    run(scenario())


# ── on_message: bot mention / reply chat ──────────────────────────────

def test_on_message_mention_generic_chat(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} วันนี้เป็นไง", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert msg.replies and msg.replies[0][0] == "..."   # chat fallback
        assert msg.channel.typing_used == 1
        user_text = fake_ai[0][1][-1]["content"]
        assert "วันนี้เป็นไง" in user_text
    run(scenario())


def test_on_message_bare_mention_defaults_to_salute(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(_mention(bot.user.id), _Author(1), mentions=[bot.user])
        await _cog(bot).on_message(msg)
        user_text = fake_ai[0][1][-1]["content"]
        assert user_text == "สวัสดี"
    run(scenario())


def test_on_message_reply_to_bot_uses_previous_context(fake_ai):
    async def scenario():
        bot = _Bot()
        prev = discord.Message.__new__(discord.Message)   # passes isinstance
        prev.author = bot.user
        prev.content = "ก่อนหน้าบอกไว้"
        msg = _Msg("อธิบายหน่อย", _Author(1),
                   reference=_Reference(prev))
        await _cog(bot).on_message(msg)
        assert fake_ai
        history = fake_ai[0][1]
        assert history[0]["role"] == "assistant"
        assert history[0]["content"] == "ก่อนหน้าบอกไว้"
        assert history[-1]["content"] == "อธิบายหน่อย"
    run(scenario())


# ── _handle_game_intent (via on_message) ──────────────────────────────

def test_slot_intent_rewrites_and_processes(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} สล็อต 500", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}slot 500"
        assert msg.replies, "confirm message must be sent"
    run(scenario())


def test_slot_allin_uses_full_wallet(fake_ai):
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 5000)
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} สล็อต all in", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}slot 5000"
    run(scenario())


def test_slot_half_uses_half_wallet(fake_ai):
    async def scenario():
        await open_account(_Author(1))
        await update_bank(1, 5000)
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} สล็อต half", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}slot 2500"
    run(scenario())


def test_slot_no_amount_defaults_100(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} สล็อต", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}slot 100"
    run(scenario())


def test_lottery_without_ticket_replies_no_process(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} หวย", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == []                          # game not started
        assert msg.replies
        assert "เลข 5 หลัก" in msg.replies[0][0]
    run(scenario())


def test_lottery_with_ticket_rewrites(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} หวย 12345 100", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}lottery 12345 100"
    run(scenario())


def test_flip_head_side_rewrites(fake_ai):
    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} ทอยเหรียญ หัว 100", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}flip 1 100"
    run(scenario())


def test_flip_no_side_randomly_picked(fake_ai, monkeypatch):
    monkeypatch.setattr(events.random, "choice", lambda seq: "1")

    async def scenario():
        bot = _Bot()
        msg = _Msg(f"{_mention(bot.user.id)} ทอยเหรียญ", _Author(1),
                   mentions=[bot.user])
        await _cog(bot).on_message(msg)
        assert bot.processed == [msg]
        assert msg.content == f"{COMMAND_PREFIX}flip 1 100"
    run(scenario())


# ── on_command_error ──────────────────────────────────────────────────

def _error_reply(error):
    async def scenario():
        ctx = _Ctx()
        await _cog().on_command_error(ctx, error)
        return ctx.sent
    return run(scenario())


def test_error_command_not_found():
    sent = _error_reply(commands.CommandNotFound("x"))
    assert sent[0][0][0] == "ไม่มีคำสั่งนี้"


def test_error_cooldown_shows_retry_after():
    cd = commands.CommandOnCooldown(commands.Cooldown(1, 15), 2.5, commands.BucketType.user)
    sent = _error_reply(cd)
    assert "2.50" in sent[0][0][0]


@pytest.mark.parametrize("error", [
    commands.MissingPermissions([]),
    commands.MissingAnyRole([]),
    commands.CheckFailure(),
])
def test_error_permission_denied(error):
    sent = _error_reply(error)
    assert sent[0][0][0] == "ไม่มีสิทธิ์ใช้คำสั่งนี้"


@pytest.mark.parametrize("error", [
    commands.BadArgument(),
    commands.MissingRequiredArgument(types.SimpleNamespace(name="x", displayed_name=None)),
])
def test_error_bad_usage(error):
    sent = _error_reply(error)
    assert sent[0][0][0] == "รูปแบบคำสั่งไม่ถูกต้อง"


def test_error_unhandled_generic():
    sent = _error_reply(Exception("boom"))
    assert sent[0][0][0] == "คำสั่งนี้มีปัญหา ลองใหม่อีกครั้ง"


def test_error_not_registered_is_silent():
    from bobcoin.cogs.economy import NotRegistered
    sent = _error_reply(NotRegistered())
    assert sent == []                                  # message already sent


def test_error_unwraps_original():
    wrapped = types.SimpleNamespace(original=commands.CommandNotFound("y"))
    sent = _error_reply(wrapped)
    assert sent[0][0][0] == "ไม่มีคำสั่งนี้"


# ── on_ready ──────────────────────────────────────────────────────────

def test_on_ready_sets_game_presence():
    async def scenario():
        bot = _Bot()
        await _cog(bot).on_ready()
        assert bot.presence
        activity = bot.presence[0]["activity"]
        assert isinstance(activity, discord.Game)
        assert activity.name == f"{COMMAND_PREFIX}command"
    run(scenario())
