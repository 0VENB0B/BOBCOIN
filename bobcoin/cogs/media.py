import re
from datetime import datetime

import discord
from discord.ext import commands
from PIL import Image, ImageDraw

from ..images import asset_path, avatar_image, image_file, load_font


class MediaCog(commands.Cog):
    @commands.command()
    async def stonk(self, ctx, user: discord.Member = None):
        user = user or ctx.author
        rich = Image.open(asset_path("pic.jpg")).convert("RGBA")
        pfp = await avatar_image(user, size=128)
        pfp = pfp.resize((177, 177))
        rich.paste(pfp, (342, 0), pfp)
        await ctx.send(file=image_file(rich, "picture.png"))

    @commands.command()
    async def DTC(self, ctx, *, text="No text entered"):
        text = text[:500]
        img = Image.open(asset_path("white.png")).convert("RGB")
        draw = ImageDraw.Draw(img)
        font = load_font(20)
        draw.text((0, 0), text, (0, 0, 0), font=font)
        await ctx.send(file=image_file(img, "text.png"))

    @commands.command()
    async def ind(self, ctx, textA=None, textB=None):
        if textA is None or textB is None:
            await ctx.send("กรอกข้อมูลให้ครบไอเด็กเหี้ย")
            await ctx.send("1.ชื่อเล่น(แนะนำเป็นชื่อภาษาอังกฤษ) 2.อายุ ")
            return

        if not re.fullmatch(r"[A-Za-z]{1,7}", textA):
            await ctx.send("ชื่อเล่นมึงต้องมีแค่อังกฤษเท่านั้นไอเด็กเหี้ย")
            return

        if not textB.isdecimal():
            await ctx.send("ใส่ตัวเลขไอเด็กเหี้ย")
            return

        age = int(textB)
        if age < 1 or age > 120:
            await ctx.send("อายุต้องอยู่ระหว่าง 1-120")
            return

        time = datetime.today().strftime("%d/%m/%Y")
        rich = Image.open(asset_path("ID.jpg")).convert("RGBA")
        pfp = await avatar_image(ctx.author, size=128)
        pfp = pfp.resize((188, 202))
        rich.paste(pfp, (54, 102), pfp)

        draw = ImageDraw.Draw(rich)
        draw.text((54, 331.4), ctx.author.name, (16, 29, 143), font=load_font(36))
        draw.text((155.86, 387.42), textA, (16, 29, 143), font=load_font(30))
        draw.text((133.72, 425.42), textB, (16, 29, 143), font=load_font(30))
        draw.text((12, 480.96), time, (16, 29, 143), font=load_font(15))
        await ctx.send(file=image_file(rich, "TID.png"))


async def setup(bot):
    await bot.add_cog(MediaCog())

