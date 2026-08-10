"""Tests for bobcoin.cogs.info — the info/admin commands.

ping/botinfo/serverinfo/TOKEN/github/invite are exercised with a fake ctx;
the role-gated commands (watch/profile) are proven to reject users without
the role; `clear` covers both the permission gate and the clamped purge.

Commands are invoked through ``conftest.invoke_command`` (discord.py 2.7
Command objects need to be bound to their cog + checks run manually).
"""

import asyncio
from datetime import datetime, timezone

import discord
import pytest
from discord.ext import commands

import bobcoin.cogs.info as info
from bobcoin.cogs.info import InfoCog
from bobcoin.settings import INVITE_URL, MAX_PURGE_MESSAGES
from conftest import invoke_command


class _Avatar:
    url = "http://avatar.example/x.png"


class _BotUser:
    id = 880963590289498142

    @property
    def display_avatar(self):
        return _Avatar()


class _Bot:
    latency = 0.05
    user = _BotUser()
    guilds = ["guild-one"]

    def __init__(self):
        self.presence = []

    async def change_presence(self, **kw):
        self.presence.append(kw)


class _Role:
    def __init__(self, name, rid=0):
        self.name = name
        self.id = rid


class _Perms:
    def __init__(self, manage_messages=False):
        self.manage_messages = manage_messages


class _Author:
    def __init__(self, uid, roles=None, joined_at=datetime(2021, 2, 2, tzinfo=timezone.utc)):
        self.id = uid
        self.name = "bob"
        self.display_name = "bob"
        self.roles = roles or []
        self.display_avatar = _Avatar()
        self.created_at = datetime(2020, 6, 15, tzinfo=timezone.utc)
        self.joined_at = joined_at
        self.color = discord.Color.blue()


class _Message:
    created_at = datetime(2021, 5, 5, tzinfo=timezone.utc)


class _Guild:
    name = "Test Guild"
    id = 123
    owner = "owner-name"
    member_count = 10
    created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    channels = ["c1", "c2"]
    emojis = ["😀", "🎰"]
    icon = None


class _Ctx:
    """Fake ctx with the attributes the check predicates read."""

    def __init__(self, author, guild=None, bot=None, permissions=None):
        self.author = author
        self.guild = guild
        self.bot = bot
        self.permissions = permissions if permissions is not None else _Perms()
        self.message = _Message()
        self.channel = None
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class _Channel:
    def __init__(self):
        self.purged = []

    async def purge(self, limit=None):
        self.purged.append(limit)


def run(coro):
    return asyncio.run(coro)


def _cog(bot):
    return InfoCog(bot)


def _embed_of(ctx):
    return ctx.sent[0][1].get("embed")


def _fields(ctx):
    return {f.name: f.value for f in _embed_of(ctx).fields}


# ── ping ──────────────────────────────────────────────────────────────

def _ping_with(latency):
    async def scenario():
        bot = _Bot()
        bot.latency = latency
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "ping", ctx)
        return _fields(ctx)
    return run(scenario())


def test_ping_green():
    assert _ping_with(0.05)["สถานะ"] == "🟢 ดีมาก"


def test_ping_yellow():
    assert _ping_with(0.15)["สถานะ"] == "🟡 พอใช้"


def test_ping_red():
    assert _ping_with(0.25)["สถานะ"] == "🔴 ช้า"


def test_ping_embed_title():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "ping", ctx)
        assert _embed_of(ctx).title == "🏓 Pong!"
    run(scenario())


# ── botinfo / serverinfo / static replies ─────────────────────────────

def test_botinfo_fields():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "botinfo", ctx)
        fields = _fields(ctx)
        assert _embed_of(ctx).title == "🤖 GUCOIN"
        assert fields["Servers"] == "1"
        assert fields["Prefix"] == "`$`"
        assert fields["สถานะ"] == "✅ ออนไลน์"
    run(scenario())


def test_serverinfo_requires_guild():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)              # guild None
        await invoke_command(_cog(bot), "serverinfo", ctx)
        assert ctx.sent[0][0][0] == "ใช้คำสั่งนี้ได้เฉพาะใน server"
    run(scenario())


def test_serverinfo_with_guild():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), guild=_Guild(), bot=bot)
        await invoke_command(_cog(bot), "serverinfo", ctx)
        fields = _fields(ctx)
        assert _embed_of(ctx).title == "🏠 Test Guild"
        assert fields["👥 สมาชิก"] == "10"
        assert fields["💬 ช่อง"] == "2"
        assert fields["😀 Emoji"] == "2"
    run(scenario())


def test_token_refused():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "TOKEN", ctx)
        assert ctx.sent[0][0][0] == "มึงอย่าแม้แต่จะคิด"
    run(scenario())


def test_github_link():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "github", ctx)
        assert ctx.sent[0][0][0] == "https://github.com/DEVPOB/BOBCOIN"
    run(scenario())


def test_invite_embed_contains_url():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot)
        await invoke_command(_cog(bot), "invite", ctx)
        assert INVITE_URL in _embed_of(ctx).fields[0].value
    run(scenario())


# ── role-gated commands ───────────────────────────────────────────────

def test_watch_rejects_without_role():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), guild=_Guild(), bot=bot)   # no WATCH role
        with pytest.raises(commands.MissingAnyRole):
            await invoke_command(_cog(bot), "watch", ctx)
        assert ctx.sent == []                        # nothing sent
    run(scenario())


def test_profile_rejects_without_role():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), guild=_Guild(), bot=bot)   # no Profile role
        with pytest.raises(commands.MissingAnyRole):
            await invoke_command(_cog(bot), "profile", ctx)
        assert ctx.sent == []
    run(scenario())


def test_watch_with_role_succeeds():
    async def scenario():
        bot = _Bot()
        author = _Author(1, roles=[_Role("WATCH")])
        ctx = _Ctx(author, guild=_Guild(), bot=bot)
        await invoke_command(_cog(bot), "watch", ctx)
        assert ctx.sent                          # date string sent
    run(scenario())


def test_profile_with_role_succeeds():
    async def scenario():
        bot = _Bot()
        author = _Author(1, roles=[_Role("Profile")])
        ctx = _Ctx(author, guild=_Guild(), bot=bot)
        await invoke_command(_cog(bot), "profile", ctx)
        em = _embed_of(ctx)
        assert em.author.name == "bob's profile"
        fields = {f.name: f.value for f in em.fields}
        assert fields["🆔 ID"] == "1"
        assert fields["📅 สร้างบัญชี"] == "15 June 2020"
        assert fields["📥 เข้า Server"] == "2 February 2021"
    run(scenario())


def test_profile_with_explicit_member():
    async def scenario():
        bot = _Bot()
        author = _Author(1, roles=[_Role("Profile")])
        ctx = _Ctx(author, guild=_Guild(), bot=bot)
        other = _Author(7, roles=[_Role("Profile")], joined_at=None)
        await invoke_command(_cog(bot), "profile", ctx, other)
        fields = {f.name: f.value for f in _embed_of(ctx).fields}
        assert fields["🆔 ID"] == "7"
        assert fields["📥 เข้า Server"] == "Unknown"     # joined_at None
    run(scenario())


# ── clear (permission gate + purge) ───────────────────────────────────

def test_clear_rejects_without_permission():
    async def scenario():
        bot = _Bot()
        ctx = _Ctx(_Author(1), bot=bot,
                   permissions=_Perms(manage_messages=False))
        with pytest.raises(commands.MissingPermissions):
            await invoke_command(_cog(bot), "clear", ctx)
        assert ctx.sent == []
    run(scenario())


def test_clear_purges_clamped_amount(monkeypatch):
    async def _fast(*_a):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)

    async def scenario():
        bot = _Bot()
        channel = _Channel()
        ctx = _Ctx(_Author(1), bot=bot,
                   permissions=_Perms(manage_messages=True))
        ctx.channel = channel
        await invoke_command(_cog(bot), "clear", ctx, 1000)   # over MAX
        assert channel.purged == [MAX_PURGE_MESSAGES]
        assert "thanos" in ctx.sent[0][0][0]         # the snap gif first
    run(scenario())


def test_clear_floor_at_one(monkeypatch):
    async def _fast(*_a):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)

    async def scenario():
        bot = _Bot()
        channel = _Channel()
        ctx = _Ctx(_Author(1), bot=bot,
                   permissions=_Perms(manage_messages=True))
        ctx.channel = channel
        await invoke_command(_cog(bot), "clear", ctx, 0)       # floored to 1
        assert channel.purged == [1]
    run(scenario())
