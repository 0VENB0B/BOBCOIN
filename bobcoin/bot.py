import logging
import time

import discord
from discord.ext import commands

from .settings import BOT_OWNER_ID, COMMAND_PREFIX

logger = logging.getLogger("bobcoin.bot")

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
        started = time.perf_counter()
        for extension in COGS:
            await self.load_extension(extension)
        logger.info("Loaded %d cogs in %.2fs", len(COGS), time.perf_counter() - started)

    async def close(self):
        """Graceful shutdown (P3 Ops): cancel task loops, drain background tasks.

        discord.py's default close() tears down immediately, which can cut off
        in-flight fire-and-forget writes (history, XP, achievements). We first
        cancel every cog's tasks loop (cog_unload) then wait briefly for the
        ``_spawn``ed background tasks to drain before closing the connection.

        NOTE: super().close() also unloads every extension (→ remove_cog →
        cog_unload), so cog_unload runs twice here. That is safe: the only
        cog_unload implementations cancel a ``tasks.loop``, which is idempotent
        — but a future cog with non-idempotent cleanup would need care.
        """
        logger.info("Shutting down… cancelling loops and draining background tasks")
        for cog in list(self.cogs.values()):
            unload = getattr(cog, "cog_unload", None)
            if unload is not None:
                try:
                    unload()
                except Exception:
                    logger.exception("cog_unload failed for %s", type(cog).__name__)
        from .gameplay import drain_background_tasks

        still = await drain_background_tasks(timeout=5.0)
        if still:
            logger.warning("%d background tasks were still running at shutdown (cancelled)", still)
        await super().close()


def create_bot():
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    return GUCoinBot(command_prefix=COMMAND_PREFIX, intents=intents, owner_id=BOT_OWNER_ID)
