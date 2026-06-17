import asyncio
import json
import random
import re

import discord
from discord.ext import commands

from ..ai import call_ai
from ..bank import add_xp, update_bank
from ..helpers import is_bot_admin
from ..movies import recommend_movie, tmdb_configured
from ..settings import BOT_ICON_URL, LIKE_ICON_URL, MOVIE_RECOMMENDATIONS

_QUIZ_SYSTEM = (
    "คุณคือผู้ออกข้อสอบ สร้างโจทย์ภาษาไทย 1 ข้อ ระดับปานกลางถึงยาก "
    "อาจเป็นคณิตศาสตร์, ปริศนา, ความรู้ทั่วไป, หรือตรรกศาสตร์ "
    "ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น: "
    '{"question":"โจทย์","answer":"คำตอบสั้นๆ","reward":1000} '
    "reward: ง่าย=500 กลาง=1000 ยาก=2000"
)


async def _get_ai_question() -> dict | None:
    raw = await call_ai(_QUIZ_SYSTEM, [{"role": "user", "content": "ออกโจทย์ใหม่"}], max_tokens=200)
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


def _answers_match(expected: str, given: str) -> bool:
    e = re.sub(r"\s+", " ", expected.strip().lower())
    g = re.sub(r"\s+", " ", given.strip().lower())
    if not e or not g:
        return False
    if e == g:
        return True
    e_num, g_num = e.replace(",", ""), g.replace(",", "")
    return e_num.isdecimal() and g_num.isdecimal() and int(e_num) == int(g_num)


def _safe_reward(value) -> int:
    try:
        reward = int(value)
    except (TypeError, ValueError):
        return 1000
    return max(500, min(reward, 2000))


def _positive_number(value) -> float | None:
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _format_number(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


_TEXT_EMOJI = {
    "!": "❗",
    "?": "❓",
    "+": "➕",
    "-": "➖",
    "*": "✖️",
    "x": "✖️",
    "/": "➗",
    ".": "⏺️",
    ",": "⏸️",
    "<": "◀️",
    ">": "▶️",
}


def _emojify_text(text: str) -> str | None:
    num2emo = {str(i): f":{'zero one two three four five six seven eight nine'.split()[i]}:" for i in range(10)}
    result = []
    for char in text.lower():
        if char.isdecimal():
            result.append(num2emo[char])
        elif "a" <= char <= "z":
            result.append(f":regional_indicator_{char}:")
        elif char.isspace():
            result.append(":heavy_minus_sign:")
        elif char in _TEXT_EMOJI:
            result.append(_TEXT_EMOJI[char])
        else:
            return None
    output = "".join(result)
    return output if len(output) <= 1900 else None


class FunCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["calR"])
    async def calr(self, ctx, width=None, height=None):
        width_num = _positive_number(width)
        height_num = _positive_number(height)
        if width_num is None or height_num is None:
            await ctx.send(f"ใช้แบบนี้: `{ctx.prefix}calr <width> <height>` เช่น `{ctx.prefix}calr 12 5`")
            return

        area = width_num * height_num
        em = discord.Embed(title="พื้นที่สี่เหลี่ยม", color=discord.Color.green())
        em.add_field(name="สูตร", value="width × height", inline=False)
        em.add_field(name="ค่า", value=f"{_format_number(width_num)} × {_format_number(height_num)}", inline=True)
        em.add_field(name="ผลลัพธ์", value=f"**{_format_number(area)}**", inline=True)
        em.set_footer(text="$calr <width> <height>", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command(aliases=["calT"])
    async def calt(self, ctx, base=None, height=None):
        base_num = _positive_number(base)
        height_num = _positive_number(height)
        if base_num is None or height_num is None:
            await ctx.send(f"ใช้แบบนี้: `{ctx.prefix}calt <base> <height>` เช่น `{ctx.prefix}calt 10 6`")
            return

        area = base_num * height_num / 2
        em = discord.Embed(title="พื้นที่สามเหลี่ยม", color=discord.Color.green())
        em.add_field(name="สูตร", value="base × height ÷ 2", inline=False)
        em.add_field(name="ค่า", value=f"{_format_number(base_num)} × {_format_number(height_num)} ÷ 2", inline=True)
        em.add_field(name="ผลลัพธ์", value=f"**{_format_number(area)}**", inline=True)
        em.set_footer(text="$calt <base> <height>", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    async def mrp(self, ctx, *, query=None):
        async with ctx.typing():
            movie = await recommend_movie(query)

        if movie is not None:
            em = discord.Embed(
                title=f"หนังแนะนำ: {movie.title}",
                url=movie.tmdb_url,
                description=movie.overview[:900],
                color=discord.Color.green(),
            )
            if movie.original_title != movie.title:
                em.add_field(name="Original title", value=movie.original_title, inline=True)
            em.add_field(name="ปี", value=movie.year, inline=True)
            em.add_field(name="คะแนน", value=f"{movie.rating:.1f}/10 ({movie.vote_count:,} votes)", inline=True)
            em.set_footer(text=f"{movie.source_label} • $mrp [ชื่อหนัง|genre]", icon_url=LIKE_ICON_URL)
            if movie.poster_url:
                em.set_thumbnail(url=movie.poster_url)
            await ctx.send(embed=em)
            return

        movie_name = random.choice(MOVIE_RECOMMENDATIONS)
        em = discord.Embed(title=f"หนังแนะนำ: {movie_name}", color=discord.Color.green())
        em.add_field(name="Source", value="รายการสำรองในบอท", inline=True)
        if not tmdb_configured():
            em.add_field(name="API", value="ตั้ง `TMDB_ACCESS_TOKEN` หรือ `TMDB_API_KEY` เพื่อใช้ TMDb", inline=False)
        else:
            em.add_field(name="API", value="TMDb ใช้งานไม่ได้ชั่วคราว เลยใช้ fallback", inline=False)
        em.set_footer(text="$mrp [ชื่อหนัง|genre]", icon_url=LIKE_ICON_URL)
        await ctx.send(embed=em)

    @commands.command()
    @commands.check(is_bot_admin)
    async def cool(self, ctx):
        await ctx.send("You are cool indeed")

    @commands.command()
    async def wait(self, ctx):
        await ctx.send("wait what")
        await asyncio.sleep(5)
        await ctx.send("wait what")

    @commands.command()
    async def emoji(self, ctx, *, text=None):
        if not text:
            await ctx.send(f"ใช้แบบนี้: `{ctx.prefix}emoji <text>` เช่น `{ctx.prefix}emoji hello 123!`")
            return
        if len(text) > 80:
            await ctx.send("ข้อความยาวเกินไป")
            return

        result = _emojify_text(text)
        if result is None:
            await ctx.send("รองรับตัวอักษรอังกฤษ a-z, ตัวเลข, เว้นวรรค และเครื่องหมาย ! ? + - * / . , < >")
            return
        await ctx.send(result)

    @commands.command(aliases=["calC"])
    async def calc(self, ctx, radius=None):
        radius_num = _positive_number(radius)
        if radius_num is None:
            await ctx.send(f"ใช้แบบนี้: `{ctx.prefix}calc <radius>` เช่น `{ctx.prefix}calc 7`")
            return

        area = 3.14159 * radius_num ** 2
        em = discord.Embed(title="พื้นที่วงกลม", color=discord.Color.green())
        em.add_field(name="สูตร", value="π × r²", inline=False)
        em.add_field(name="ค่า", value=f"π × {_format_number(radius_num)}²", inline=True)
        em.add_field(name="ผลลัพธ์", value=f"**{_format_number(area)}**", inline=True)
        em.set_footer(text="$calc <radius>", icon_url=BOT_ICON_URL)
        await ctx.send(embed=em)

    @commands.command(aliases=["QM", "qm"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def quiz(self, ctx):
        member = ctx.author

        async with ctx.typing():
            q_data = await _get_ai_question()

        if q_data and "question" in q_data and "answer" in q_data:
            question = q_data["question"]
            answer = str(q_data["answer"])
            reward = _safe_reward(q_data.get("reward", 1000))
        else:
            # fallback: simple math
            a, b = random.randint(200, 800), random.randint(10000, 99999)
            question = f"{a} + {b}"
            answer = str(a + b)
            reward = 500

        em = discord.Embed(title="🧠 โจทย์ท้าทาย", description=question, color=discord.Color.gold())
        em.set_footer(text=f"รางวัล: {reward} 🪙 | ตอบภายใน 30 วิ")
        await ctx.send(embed=em)

        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == member and m.channel == ctx.channel,
                timeout=30,
            )
        except asyncio.TimeoutError:
            await ctx.send(f"⏰ หมดเวลา! คำตอบที่ถูกคือ **{answer}**")
            return

        if _answers_match(answer, msg.content):
            if await update_bank(ctx.author, reward) is None:
                await ctx.send(f"✅ ถูกต้อง! แต่ยังไม่มีบัญชี เลยยังรับรางวัลไม่ได้ พิมพ์ `{ctx.prefix}register` ก่อนนะ")
                return
            await add_xp(ctx.author.id, max(reward // 500, 1))
            await ctx.send(f"✅ ถูกต้อง! ได้รับ **{reward}** เหรียญ 🪙")
        else:
            await ctx.send(f"❌ ผิด! คำตอบที่ถูกคือ **{answer}** ไม่ได้อะไรเลย 💸")


async def setup(bot):
    await bot.add_cog(FunCog(bot))
