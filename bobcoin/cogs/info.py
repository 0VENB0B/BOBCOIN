import asyncio
from datetime import datetime

import discord
from discord.ext import commands

from ..components import CommandMenuView
from ..images import avatar_url
from ..settings import BOT_ICON_URL, COMMAND_PREFIX, INVITE_URL, MAX_PURGE_MESSAGES


class InfoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_any_role("WATCH")
    async def watch(self, ctx):
        await ctx.send(datetime.today().strftime("Day %d Month %m Year %Y |Time %H:%M"))

    @commands.command()
    @commands.has_any_role("Profile")
    async def profile(self, ctx, member: discord.Member = None):
        member = ctx.author if member is None else member
        em = discord.Embed(colour=member.color, timestamp=ctx.message.created_at)
        em.set_author(name=f"{member}'s profile")
        em.set_thumbnail(url=avatar_url(member))
        em.add_field(name="ID", value=member.id)
        em.add_field(name="Account created at", value=member.created_at.strftime("%a, %#d %B %Y, %I:%M %p UTC"))
        joined_at = member.joined_at.strftime("%a, %#d %B %Y, %I:%M %p UTC") if member.joined_at else "Unknown"
        em.add_field(name="Joined at", value=joined_at)
        await ctx.send(embed=em)

    @commands.command()
    async def invite(self, ctx):
        em = discord.Embed(title="BOB's BOBCOIN", color=discord.Color.purple())
        em.add_field(name="BOBCOIN", value=INVITE_URL)
        em.set_thumbnail(url=BOT_ICON_URL)
        em.set_footer(text="บ อ ท แ ห่ ง ช น ชั้ น", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def ER(self, ctx):
        message = await ctx.send("hello")
        await asyncio.sleep(1)
        await message.edit(content="newcontent")

    @commands.command()
    async def TC(self, ctx):
        em = discord.Embed(title="BOB's BOBCOIN", description="Test Command For Develop New Feature", colour=discord.Color.green())
        em.set_author(name="Discord.py Command", icon_url=BOT_ICON_URL)
        em.add_field(name="DTC ตามด้วยข้อความ [TC]", value="Test image(TYPE TEXT) manipulation", inline=False)
        em.add_field(name="stonk ตามด้วย@user [TC]", value="Test image(TYPE USER) manipulation", inline=False)
        em.add_field(name="ER [TC]", value="Test Message Edit", inline=False)
        em.add_field(name="TestJson [TC]", value="Test Money Given System", inline=False)
        em.add_field(name="wait [TC]", value="Test Asyncio", inline=False)
        em.add_field(name="reaction [TC]", value="Test Reaction", inline=False)
        em.set_thumbnail(url="https://i.pinimg.com/originals/e1/59/25/e15925c931a81678a3c2e0c0a40db781.gif")
        em.set_footer(text="| บ อ ท แ ห่ ง ช น ชั้ น น |", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def ECO(self, ctx):
        em = discord.Embed(title="BOB's BOBCOIN", description="BOB Economy command", colour=discord.Color.orange())
        em.set_author(name="Economy Command", icon_url=BOT_ICON_URL)
        em.add_field(name="deposit [ECO]", value="Deposit BOBCOIN Feature", inline=False)
        em.add_field(name="withdraw [ECO]", value="Withdraw From BOB Bank Feature", inline=False)
        em.add_field(name="Backpack [ECO]", value="Check Your Backpack Feature", inline=False)
        em.add_field(name="lottery ตามด้วยตัวเลข 5 หลัก และเงินเดิมพัน [ECO]", value="Lottery Feature", inline=False)
        em.add_field(name="slot ตามด้วยเงินพนัน [ECO]", value="Slot Feature", inline=False)
        em.add_field(name="leaderboard [FT]", value="Leader board Feature", inline=False)
        em.add_field(name="shop @user @item[FT]", value="Shop Feature", inline=False)
        em.add_field(name="filpcoin/flipcoin หัว/ก้อย และเงินเดิมพัน [FT]", value="Flip Coin Feature", inline=False)
        em.add_field(name="item [FT]", value="Item Feature", inline=False)
        em.add_field(name="QM [FT]", value="Quick Math Feature", inline=False)
        em.set_thumbnail(url="https://i.pinimg.com/originals/de/4a/90/de4a9060d587b1e7d18d2048c1eec080.gif")
        em.set_footer(text="| บ อ ท แ ห่ ง ช น ชั้ น น |", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def FT(self, ctx):
        em = discord.Embed(title="BOB's BOBCOIN", description="BOB Feature Command", colour=discord.Color.light_grey())
        em.add_field(name="emoji ตามด้วยข้อความ(ENG ONLY) [FT]", value="Convert Text To Emoji Feature", inline=False)
        em.add_field(name="mrp [FT]", value="Recommend Moive Feature", inline=False)
        em.add_field(name="calR ตามด้วย กว้าง(ตัวเลข) และ ยาว(ตัวเลข) [FT]", value="Calculator Rectangle Feature", inline=False)
        em.add_field(name="calT ตามด้วย ผลบวกด้านคู่ขนาน(ตัวเลข) และ สูง(ตัวเลข) [FT]", value="Calculator Trapezoid Feature", inline=False)
        em.add_field(name="calC ตามด้วย รัศมี(ตัวเลข) [FT]", value="Calculator Circle Feature", inline=False)
        em.add_field(name="ind [FT]", value="Introduce To Make Profile Card", inline=False)
        em.add_field(name="botinfo [FT]", value="Information About BOB's BOBCOIN", inline=False)
        em.set_thumbnail(url="http://shardacomputerngp.com/images/header/horoscope.gif")
        em.set_footer(text="| บ อ ท แ ห่ ง ช น ชั้ น น |", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def INFO(self, ctx):
        em = discord.Embed(title="BOB's BOBCOIN", description="BOB Information Command", colour=discord.Color.dark_green())
        em.add_field(name="botinfo [INFO]", value="Information About BOB's BOBCOIN", inline=False)
        em.add_field(name="invite [INFO]", value="Invite BOB's BOBCOIN", inline=False)
        em.add_field(name="ping [INFO]", value="Check Bot's Ping", inline=False)
        em.add_field(name="github [INFO]", value="Github BOB's BOBCOIN Feature", inline=False)
        em.set_thumbnail(url="https://i.pinimg.com/originals/f8/5f/55/f85f55221962f0c1100496ffc0898d40.gif")
        em.set_footer(text="| บ อ ท แ ห่ ง ช น ชั้ น น |)", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def command(self, ctx):
        await ctx.send(view=CommandMenuView(COMMAND_PREFIX))

    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f"ping {round(self.bot.latency * 1000)} ms")

    @commands.command()
    async def botinfo(self, ctx):
        em = discord.Embed(title="ข้อมูลบอท", color=discord.Color.dark_blue())
        em.add_field(name="ชื่อ", value=self.bot.user.name, inline=False)
        em.add_field(name="รหัส", value=self.bot.user.id, inline=False)
        em.add_field(name="รุ่น", value=getattr(self.bot.user, "discriminator", "0"), inline=False)
        em.add_field(name="เวอร์ชั่น", value="1.0.0", inline=False)
        em.add_field(name="สถานะการทำงาน", value="ทำงานได้อย่างเต็มที่", inline=False)
        await ctx.send(embed=em)

    @commands.command()
    async def serverinfo(self, ctx):
        if ctx.guild is None:
            await ctx.send("ใช้คำสั่งนี้ได้เฉพาะใน server")
            return

        em = discord.Embed(title="ข้อมูลServer", color=discord.Color.dark_blue())
        em.add_field(name="ชื่อ", value=ctx.guild.name, inline=False)
        em.add_field(name="รหัส", value=ctx.guild.id, inline=False)
        em.add_field(name="จำนวนผู้ใช้", value=ctx.guild.member_count, inline=False)
        icon_url = getattr(ctx.guild, "icon_url", None)
        if not icon_url:
            icon = getattr(ctx.guild, "icon", None)
            icon_url = str(icon) if icon else ""
        em.set_thumbnail(url=str(icon_url))
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

