import asyncio
import logging

import discord
from discord.ext import commands

from ..settings import COMMAND_PREFIX

logger = logging.getLogger("bobcoin.events")


class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        error = getattr(error, "original", error)

        if isinstance(error, commands.CommandNotFound):
            await ctx.send("ไม่มีคำสั่งนี้")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send("ใจเย็น ลองอีกครั้งใน {:.2f} วิ".format(error.retry_after))
            return
        if isinstance(error, (commands.MissingPermissions, commands.MissingAnyRole)):
            await ctx.send("ไม่มีสิทธิ์ใช้คำสั่งนี้")
            return
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send("รูปแบบคำสั่งไม่ถูกต้อง")
            return

        logger.error(
            "Unhandled command error in %s",
            ctx.command,
            exc_info=(type(error), error, error.__traceback__),
        )
        await ctx.send("คำสั่งนี้มีปัญหา ลองใหม่อีกครั้ง")

    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot:
            return

        if "$reaction" in msg.content:
            await msg.add_reaction("<:Discord:895553740793339986>")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.change_presence(activity=discord.Game(name=f"{COMMAND_PREFIX}command"))


async def setup(bot):
    await bot.add_cog(EventsCog(bot))

