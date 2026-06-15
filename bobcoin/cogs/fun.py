import asyncio
import random

import discord
from discord.ext import commands

from ..bank import open_account, update_bank
from ..settings import BOT_ICON_URL, LIKE_ICON_URL, MOVIE_RECOMMENDATIONS


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def calR(self, ctx, a: int, b: int):
        width = a
        height = b
        cal = width * height
        em = discord.Embed(title="$calR = width * height", color=discord.Color.green())
        em.set_footer(
            text=f"พื้นที่ของรูปสี่เหลื่ยม{width} * {height} = {cal}",
            icon_url=BOT_ICON_URL,
        )
        await ctx.send(embed=em)

    @commands.command()
    async def calT(self, ctx, a: int, b: int):
        side = a
        height = b
        cal = side * height / 2
        em = discord.Embed(title="$cal", color=discord.Color.green())
        em.set_footer(
            text=f"พื้นที่รูปสี่เหลี่ยมคางหมู{side} * {height} = {cal}",
            icon_url=BOT_ICON_URL,
        )
        await ctx.send(embed=em)

    @commands.command()
    async def mrp(self, ctx):
        movie = random.choice(MOVIE_RECOMMENDATIONS)
        rec = f"หนังน่าดู {movie} บอกเลยว่าสนุก"
        em = discord.Embed(title=f"หนังที่ดีสำหรับ {ctx.author.name}", color=discord.Color.green())
        em.add_field(name="ห นั ง คุ ณ ภ า พ", value=rec)
        em.set_thumbnail(url="https://image.freepik.com/free-vector/isometric-cinema-icon-set_1284-18691.jpg")
        em.set_footer(text="ขอให้สนุกน้าาาาา", icon_url=LIKE_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    @commands.has_any_role("DEV")
    async def cool(self, ctx):
        await ctx.send("You are cool indeed")

    @commands.command()
    async def wait(self, ctx):
        await ctx.send("wait what")
        await asyncio.sleep(5)
        await ctx.send("wait what")

    @commands.command()
    async def emoji(self, ctx, *, text):
        if len(text) > 80:
            await ctx.send("ข้อความยาวเกินไป")
            return

        emoji = []
        num2emo = {
            "0": ":zero:",
            "1": ":one:",
            "2": ":two:",
            "3": ":three:",
            "4": ":four:",
            "5": ":five:",
            "6": ":six:",
            "7": ":seven:",
            "8": ":eight:",
            "9": ":nine:",
        }
        for char in text.lower():
            if char.isdecimal():
                emoji.append(num2emo.get(char, ""))
            elif "a" <= char <= "z":
                emoji.append(":regional_indicator_" + char + ":")
            elif char.isspace():
                emoji.append(":heavy_minus_sign:")
            else:
                await ctx.send("กูไม่มีอีโมจิ ไอเด็กเหี้ยนี้")
                return

        await ctx.send("".join(emoji))

    @commands.command()
    async def calC(self, ctx, *, text):
        r = float(text)
        cal = 3.14 * r**2
        em = discord.Embed(title="$calC = pi * radius^2", color=discord.Color.green())
        em.set_footer(text=f"พื้นที่วงกลม {r} * {3.14} = {cal}", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def QM(self, ctx):
        await open_account(ctx.author)
        member = ctx.author
        ran3 = random.randint(200, 800)
        ran4 = random.randint(10000, 99999)
        question = "%d + %d" % (ran3, ran4)
        answer = ran3 + ran4

        await ctx.send(f"คำถามคือ {question}")
        try:
            message = await self.bot.wait_for(
                "message",
                check=lambda message: message.author == member and message.channel == ctx.channel,
                timeout=30,
            )
        except asyncio.TimeoutError:
            await ctx.send("หมดเวลา")
            return

        if message.content.strip() == str(answer):
            await ctx.send("แกก็เก่งเหมือนกันนี้")
            await update_bank(ctx.author, 500)
            return

        await ctx.send("อ่อนหัด พยายามแค่ไหนก็ยังอ่อนหัด")
        await ctx.send("แต่ไม่เป็นไรรางวัลปลอบใจ 10 บาท")
        await update_bank(ctx.author, 10)


async def setup(bot):
    await bot.add_cog(FunCog(bot))

