import discord
from discord.ext import commands

from .settings import COMMAND_PREFIX

COGS = (
    "bobcoin.cogs.events",
    "bobcoin.cogs.economy",
    "bobcoin.cogs.fun",
    "bobcoin.cogs.media",
    "bobcoin.cogs.info",
)


class BOBCoinBot(commands.Bot):
    async def setup_hook(self):
        for extension in COGS:
            await self.load_extension(extension)


def create_bot():
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return BOBCoinBot(command_prefix=COMMAND_PREFIX, intents=intents)

