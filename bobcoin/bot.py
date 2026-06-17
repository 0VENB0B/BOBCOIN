import discord
from discord.ext import commands

from .settings import BOT_OWNER_ID, COMMAND_PREFIX

COGS = (
    "bobcoin.cogs.events",
    "bobcoin.cogs.economy",
    "bobcoin.cogs.fun",
    "bobcoin.cogs.panel",
    "bobcoin.cogs.duel",
    "bobcoin.cogs.media",
    "bobcoin.cogs.info",
    "bobcoin.cogs.guardian",
)


class GUCoinBot(commands.Bot):
    async def setup_hook(self):
        for extension in COGS:
            await self.load_extension(extension)


def create_bot():
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return GUCoinBot(command_prefix=COMMAND_PREFIX, intents=intents, owner_id=BOT_OWNER_ID)
