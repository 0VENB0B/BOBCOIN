import asyncio
from datetime import datetime

import discord
from discord.ext import commands

from ..components import CommandMenuView
from ..settings import BOT_ICON_URL, COMMAND_PREFIX, INVITE_URL, MAX_PURGE_MESSAGES


class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_any_role("WATCH")
    async def watch(self, ctx):
        await ctx.send(datetime.today().strftime("Day %d Month %m Year %Y | Time %H:%M"))

    @commands.command()
    @commands.has_any_role("Profile")
    async def profile(self, ctx, member: discord.Member = None):
        member = ctx.author if member is None else member
        em = discord.Embed(colour=member.color, timestamp=ctx.message.created_at)
        em.set_author(name=f"{member.display_name}'s profile")
        em.set_thumbnail(url=str(member.display_avatar))
        em.add_field(name="🆔 ID", value=str(member.id))
        em.add_field(name="📅 สร้างบัญชี", value=member.created_at.strftime("%-d %B %Y"))
        joined_at = member.joined_at.strftime("%-d %B %Y") if member.joined_at else "Unknown"
        em.add_field(name="📥 เข้า Server", value=joined_at)
        await ctx.send(embed=em)

    @commands.command()
    async def invite(self, ctx):
        em = discord.Embed(title="🤖 เชิญ GUCOIN Bot", color=discord.Color.purple())
        em.add_field(name="ลิงก์เชิญ", value=f"[คลิกที่นี่]({INVITE_URL})", inline=False)
        em.set_thumbnail(url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def command(self, ctx):
        await ctx.send(view=CommandMenuView(COMMAND_PREFIX))

    @commands.command()
    async def ping(self, ctx):
        ms = round(self.bot.latency * 1000)
        if ms < 100:
            color, status = discord.Color.green(), "🟢 ดีมาก"
        elif ms < 200:
            color, status = discord.Color.yellow(), "🟡 พอใช้"
        else:
            color, status = discord.Color.red(), "🔴 ช้า"
        em = discord.Embed(title="🏓 Pong!", color=color)
        em.add_field(name="Latency", value=f"**{ms} ms**", inline=True)
        em.add_field(name="สถานะ", value=status, inline=True)
        await ctx.send(embed=em)

    @commands.command()
    async def botinfo(self, ctx):
        em = discord.Embed(title="🤖 GUCOIN", color=discord.Color.blurple())
        em.set_thumbnail(url=self.bot.user.display_avatar.url)
        em.add_field(name="ชื่อ", value="GUCOIN", inline=True)
        em.add_field(name="ID", value=str(self.bot.user.id), inline=True)
        em.add_field(name="ระบบ", value="GUCOIN v2.0", inline=True)
        em.add_field(name="Prefix", value=f"`{COMMAND_PREFIX}`", inline=True)
        em.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        em.add_field(name="สถานะ", value="✅ ออนไลน์", inline=True)
        await ctx.send(embed=em)

    @commands.command()
    async def serverinfo(self, ctx):
        if ctx.guild is None:
            await ctx.send("ใช้คำสั่งนี้ได้เฉพาะใน server")
            return
        g = ctx.guild
        em = discord.Embed(title=f"🏠 {g.name}", color=discord.Color.blurple(), timestamp=ctx.message.created_at)
        em.add_field(name="🆔 ID", value=str(g.id), inline=True)
        em.add_field(name="👑 เจ้าของ Server", value=str(g.owner), inline=True)
        em.add_field(name="👥 สมาชิก", value=f"{g.member_count:,}", inline=True)
        em.add_field(name="📅 สร้างเมื่อ", value=g.created_at.strftime("%-d %B %Y"), inline=True)
        em.add_field(name="💬 ช่อง", value=str(len(g.channels)), inline=True)
        em.add_field(name="😀 Emoji", value=str(len(g.emojis)), inline=True)
        em.add_field(name="🆕 เจ้าของบอทคนใหม่", value="<@836652703412387850>", inline=True)
        em.add_field(name="🏛️ เจ้าของบอทคนเก่า", value="<@587624224525385761>", inline=True)
        if g.icon:
            em.set_thumbnail(url=g.icon.url)
        await ctx.send(embed=em)

    @commands.command()
    async def TOKEN(self, ctx):
        await ctx.send("มึงอย่าแม้แต่จะคิด")

    @commands.command()
    async def github(self, ctx):
        await ctx.send("https://github.com/DEVPOB/BOBCOIN")

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int = 100):
        amount = max(1, min(amount, MAX_PURGE_MESSAGES))
        await ctx.send("https://tenor.com/view/thanos-just-the-snap-avengers-infinity-war-gif-12393235")
        await asyncio.sleep(1.3)
        await ctx.channel.purge(limit=amount)


async def setup(bot):
    await bot.add_cog(InfoCog(bot))
