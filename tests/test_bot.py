"""Tests for bobcoin.bot — the bot factory + cog wiring.

create_bot() is a pure constructor (no network — discord.py only connects
when .run() is called), so the smoke test is offline-safe. setup_hook is
verified by intercepting load_extension rather than truly loading cogs
(GuardianCog would otherwise start its 30-min tasks loop).
"""

import asyncio

import bobcoin.bot as bot_module
from bobcoin.bot import create_bot
from bobcoin.settings import BOT_OWNER_ID, COMMAND_PREFIX


def test_create_bot_smoke():
    bot = create_bot()
    assert bot.command_prefix == COMMAND_PREFIX
    assert bot.owner_id == BOT_OWNER_ID
    assert bot.intents.message_content is True
    assert bot.intents.members is True
    assert bot.user is None                  # not logged in


def test_all_eight_cogs_registered():
    expected = {
        "bobcoin.cogs.events",
        "bobcoin.cogs.economy",
        "bobcoin.cogs.fun",
        "bobcoin.cogs.panel",
        "bobcoin.cogs.duel",
        "bobcoin.cogs.media",
        "bobcoin.cogs.info",
        "bobcoin.cogs.guardian",
    }
    assert set(bot_module.COGS) == expected
    assert len(bot_module.COGS) == 8


def test_setup_hook_loads_every_extension(monkeypatch):
    loaded = []

    async def _fake_load(extension):
        loaded.append(extension)

    bot = create_bot()
    monkeypatch.setattr(bot, "load_extension", _fake_load)

    asyncio.run(bot.setup_hook())
    assert loaded == list(bot_module.COGS)


def test_each_cog_extension_has_a_setup_function():
    for ext in bot_module.COGS:
        module = __import__(ext, fromlist=["setup"])
        assert hasattr(module, "setup"), ext


def test_close_drains_background_tasks(monkeypatch):
    """P3 Ops: close() must drain in-flight background tasks before closing."""
    import bobcoin.gameplay as gp
    drained = []

    async def _fake_drain(timeout=5.0):
        drained.append(timeout)
        return 0

    monkeypatch.setattr(gp, "drain_background_tasks", _fake_drain)

    async def scenario():
        bot = create_bot()
        await bot.close()
        assert drained == [5.0]
    asyncio.run(scenario())


def test_close_calls_cog_unload_on_every_cog(monkeypatch):
    """P3 Ops: close() must cancel task loops via each cog's cog_unload."""
    import bobcoin.gameplay as gp
    monkeypatch.setattr(gp, "drain_background_tasks", _fake_drain)

    unloaded = []

    class _FakeCog:
        def __init__(self, name):
            self.name = name

        def cog_unload(self):
            unloaded.append(self.name)

    async def scenario():
        bot = create_bot()
        # discord.py stores cogs in the name-mangled ``__cogs`` dict
        # (mangled against _BotBase, where commands.Bot declares it)
        bot._BotBase__cogs = {"a": _FakeCog("a"), "b": _FakeCog("b")}
        await bot.close()
        assert unloaded == ["a", "b"]
    asyncio.run(scenario())


async def _fake_drain(timeout=5.0):
    return 0
