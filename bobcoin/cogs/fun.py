import asyncio
import contextlib
import functools
import json
import random
import re

import discord
from discord.ext import commands

from ..ai import call_ai
from ..bank import add_xp, update_bank
from ..helpers import is_bot_admin
from ..movies import recommend_movie, tmdb_configured
from ..roblox import GENRE_BUCKETS, RobloxGame, recommend_roblox_game
from ..settings import BOT_ICON_URL, LIKE_ICON_URL, MOVIE_RECOMMENDATIONS

_QUIZ_SYSTEM = (
    "คุณคือผู้ออกข้อสอบ สร้างโจทย์ภาษาไทย 1 ข้อ ระดับปานกลางถึงยาก "
    "อาจเป็นคณิตศาสตร์, ปริศนา, ความรู้ทั่วไป, หรือตรรกศาสตร์ "
    "ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น: "
    '{"question":"โจทย์","answer":"คำตอบสั้นๆ","options":["ตัวเลือก 1","ตัวเลือก 2","ตัวเลือก 3","ตัวเลือก 4"],"reward":1000} '
    "options ต้องมี 4 ตัวเลือก และตัวที่ถูกต้องต้องตรงกับ answer เป๊ะๆ  reward: ง่าย=500 กลาง=1000 ยาก=2000"
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


def _make_distractors(answer: str, correct_numeric: int | None = None) -> list[str]:
    """Three plausible-but-wrong options for a quiz answer (MCQ mode)."""
    base = correct_numeric if correct_numeric is not None else (int(answer) if str(answer).isdecimal() else None)
    if base is None:
        return ["ไม่รู้สิ", "ผิดจ้า", "ไม่มีข้อถูก"]
    wrong: set[str] = set()
    guard = 0
    while len(wrong) < 3 and guard < 50:
        guard += 1
        offset = random.randint(1, max(2, base // 10))
        candidate = base + random.choice([-1, 1]) * offset
        if candidate != base and candidate > 0:
            wrong.add(str(candidate))
    return list(wrong)


def _build_quiz_data(q_data: dict | None) -> tuple[str, str, list[str], int]:
    """Turn raw AI JSON (or None) into (question, answer, 4 options, reward)."""
    if q_data and "question" in q_data and "answer" in q_data:
        question = str(q_data["question"])
        answer = str(q_data["answer"])
        reward = _safe_reward(q_data.get("reward", 1000))
        options = [str(o) for o in (q_data.get("options") or []) if str(o).strip()][:4]
        if len(options) < 2 or not any(_answers_match(answer, o) for o in options):
            options = [answer, *_make_distractors(answer)]
        return question, answer, options, reward

    # fallback: simple math with generated wrong options
    a, b = random.randint(200, 800), random.randint(10000, 99999)
    question = f"{a} + {b}"
    answer = str(a + b)
    options = [answer, *_make_distractors(answer, correct_numeric=a + b)]
    return question, answer, options, 500


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
    num2emo = {str(i): f":{['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine'][i]}:" for i in range(10)}
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

    @commands.command(aliases=["rbx", "roblox", "แมพ", "แมป"])
    async def roblox(self, ctx, *, query=None):
        """$roblox [แนวเกม|ชื่อแมพ] — แนะนำแมพ Roblox ชั้นนำ (ข้อมูลสด) พร้อมชวนเพื่อนเล่น"""
        async with ctx.typing():
            game = await recommend_roblox_game(query)
        if game is None:
            await ctx.send("หาแมพไม่เจอ ลองใหม่อีกทีนะ 😅")
            return
        view = RobloxView(game, ctx.author.id, (query or "").strip() or None)
        await ctx.send(embed=_build_roblox_embed(game), view=view)

    @commands.command(aliases=["QM", "qm"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def quiz(self, ctx):
        member = ctx.author

        async with ctx.typing():
            q_data = await _get_ai_question()
        question, answer, options, reward = _build_quiz_data(q_data)

        em = discord.Embed(title="🧠 โจทย์ท้าทาย", description=question, color=discord.Color.gold())
        em.set_footer(text=f"รางวัล: {reward} 🪙 | กดปุ่มตอบภายใน 30 วิ")
        view = _QuizView(answer, options, member.id)
        msg = await ctx.send(embed=em, view=view)

        timed_out = await view.wait()
        if timed_out:
            for child in getattr(view, "children", ()):
                child.disabled = True
            with contextlib.suppress(discord.HTTPException):
                await msg.edit(embed=em, view=view)
            await ctx.send(f"⏰ หมดเวลา! คำตอบที่ถูกคือ **{answer}**")
            return

        if view.correct:
            if await update_bank(ctx.author, reward) is None:
                await ctx.send(f"✅ ถูกต้อง! แต่ยังไม่มีบัญชี เลยยังรับรางวัลไม่ได้ พิมพ์ `{ctx.prefix}register` ก่อนนะ")
                return
            await add_xp(ctx.author.id, max(reward // 500, 1))
            await ctx.send(f"✅ ถูกต้อง! ได้รับ **{reward}** เหรียญ 🪙")
        else:
            await ctx.send(f"❌ ผิด! คำตอบที่ถูกคือ **{answer}** ไม่ได้อะไรเลย 💸")


class _QuizView(discord.ui.View):
    """Multiple-choice quiz: 4 buttons, correct one turns green, wrong red."""

    def __init__(self, answer: str, options: list[str], player_id: int):
        super().__init__(timeout=30)
        self.answer = answer
        self.player_id = player_id
        self.correct: bool | None = None
        self.picked: str | None = None
        shuffled = list(options)
        random.shuffle(shuffled)
        for idx, opt in enumerate(shuffled):
            button = discord.ui.Button(label=f"{['🇦', '🇧', '🇨', '🇩'][idx % 4]} {opt}", style=discord.ButtonStyle.secondary, row=idx // 4)
            button.callback = functools.partial(self._pick, opt=opt)
            self.add_item(button)

    async def _pick(self, interaction: discord.Interaction, opt: str):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่โจทย์ของแก!", ephemeral=True)
            return
        self.picked = opt
        self.correct = _answers_match(self.answer, opt)
        for child in self.children:
            child.disabled = True
            label = getattr(child, "label", "") or ""
            option_text = label.split(" ", 1)[1] if " " in label else label
            if _answers_match(self.answer, option_text):
                child.style = discord.ButtonStyle.success
            elif option_text == opt:
                child.style = discord.ButtonStyle.danger
        self.stop()
        await interaction.response.edit_message(view=self)


# ── Roblox top-map recommendations ───────────────────────────────────────────

def _build_roblox_embed(game: RobloxGame) -> discord.Embed:
    em = discord.Embed(
        title=f"🎮 {game.name}",
        description=(game.description or game.blurb)[:900],
        color=discord.Color.red(),
    )
    em.add_field(name="🏷️ แนว", value=game.genre_label, inline=True)
    em.add_field(name="🎮 กำลังเล่น", value=f"**{game.playing:,}** คน" if game.playing is not None else "—", inline=True)
    em.add_field(name="👀 ยอดเข้าชม", value=f"**{game.visits:,}**" if game.visits is not None else "—", inline=True)
    if game.favorites is not None:
        em.add_field(name="⭐ Favorites", value=f"**{game.favorites:,}**", inline=True)
    em.add_field(name="🏢 ผู้สร้าง", value=game.creator, inline=True)
    if game.price:
        em.add_field(name="💰 ราคา", value=f"**{game.price:,}** Robux", inline=True)
    if game.codes:
        shown = " · ".join(f"`{c}`" for c in game.codes[:8])
        if len(game.codes) > 8:
            shown += f"\n…และอีก {len(game.codes) - 8} โค้ด"
        em.add_field(name="🎁 โค้ดเกม", value=f"{shown}\nโค้ดหมดอายุได้ไว รีบใช้!", inline=False)
    if game.thumb_url:
        em.set_thumbnail(url=game.thumb_url)
    src = "ข้อมูลสดจาก Roblox API" if game.source_label == "live" else "รายการสำรองในบอท"
    em.set_footer(text=f"{src} • $roblox [แนวเกม|ชื่อแมพ] • 🎮 เปิดเกม 👥 ชวนเพื่อน 🎁 โค้ด")
    return em


def _build_invite_embed(game: RobloxGame, inviter, friends) -> discord.Embed:
    mentions = " ".join(f.mention for f in friends)
    playing = f"คนกำลังเล่นอยู่ **{game.playing:,}** คน" if game.playing is not None else "คนกำลังเล่นเยอะมาก"
    em = discord.Embed(
        title=f"🎮 {inviter.display_name} ชวนมาเล่น **{game.name}** กัน!",
        description=f"{mentions}\n\nมาเล่นด้วยกันเร็ว! ตอนนี้{playing} 👀\nกดปุ่มด้านล่างเพื่อเข้าเกมได้เลย 🚀",
        color=discord.Color.red(),
    )
    em.set_footer(text=f"ชวนโดย {inviter.display_name} • Roblox")
    return em


class RobloxView(discord.ui.View):
    """Interactive $roblox result — reroll, genre filter, invite friends, open game."""

    def __init__(self, game: RobloxGame, author_id: int, query: str | None = None):
        super().__init__(timeout=300)
        self.game = game
        self.author_id = author_id
        self.query = query
        self._invite_mode = False
        self._build()

    def _build(self) -> None:
        self.clear_items()

        # NOTE: a Select fills its entire row (width 5) in Discord's layout,
        # so it gets a row of its own; buttons share the rows around it.
        reroll = discord.ui.Button(label="🔄 สุ่มแมพใหม่", style=discord.ButtonStyle.blurple, row=0, custom_id="rbx:reroll")
        reroll.callback = self._on_reroll
        self.add_item(reroll)

        invite = discord.ui.Button(label="👥 ชวนเพื่อน", style=discord.ButtonStyle.success, row=0, custom_id="rbx:invite")
        invite.callback = self._on_invite
        self.add_item(invite)

        self.add_item(discord.ui.Button(label="🎮 เปิดเกม", style=discord.ButtonStyle.link, url=self.game.url, row=0))

        if self.game.codes:
            codes = discord.ui.Button(label="🎁 โค้ดเกม", style=discord.ButtonStyle.secondary, row=0, custom_id="rbx:codes")
            codes.callback = self._on_codes
            self.add_item(codes)

        genre_options = [discord.SelectOption(label="🔀 สุ่ม", value="สุ่ม", description="แมพชั้นนำแบบสุ่ม")]
        genre_options.extend(discord.SelectOption(label=label, value=label) for label in GENRE_BUCKETS)
        genre = discord.ui.Select(placeholder="📂 แนวเกม", options=genre_options, row=1, custom_id="rbx:genre")
        genre.callback = self._on_genre
        self.add_item(genre)

        if self._invite_mode:
            friends = discord.ui.UserSelect(
                placeholder="เลือกเพื่อนที่จะชวน (สูงสุด 5)",
                min_values=1,
                max_values=5,
                row=2,
                custom_id="rbx:friends",
            )
            friends.callback = self._on_friends
            self.add_item(friends)

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("กดเองก่อนสิ ถึงจะสุ่ม/ชวนได้ 🤨", ephemeral=True)
        return False

    async def _re_pick(self, interaction: discord.Interaction, query: str | None) -> None:
        """Pick a fresh map (avoid repeating the current one) and refresh the embed."""
        await interaction.response.defer()
        game = await recommend_roblox_game(query, exclude={self.game.universe_id})
        game = game or await recommend_roblox_game(query)
        if game is None:
            await interaction.followup.send("หาแมพไม่เจอ ลองใหม่อีกทีนะ 😅", ephemeral=True)
            return
        self.game = game
        self.query = query
        self._invite_mode = False
        self._build()
        await interaction.edit_original_response(embed=_build_roblox_embed(game), view=self)

    async def _on_reroll(self, interaction: discord.Interaction):
        if not await self._owner_only(interaction):
            return
        await self._re_pick(interaction, self.query)

    async def _on_codes(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._owner_only(interaction):
            return
        if not self.game.codes:
            await interaction.response.send_message("เกมนี้ยังไม่มีโค้ดในตอนนี้ 😅", ephemeral=True)
            return
        body = "\n".join(f"`{c}`" for c in self.game.codes)
        em = discord.Embed(
            title=f"🎁 โค้ดเกมของ {self.game.name}",
            description=f"โค้ดหมดอายุได้ไว ลองใช้ดูเร็วๆ นะ!\n\n{body}",
            color=discord.Color.gold(),
        )
        em.set_footer(text="โค้ดบางตัวอาจหมดอายุแล้ว ตรวจสอบในเกมได้เลย")
        await interaction.response.send_message(embed=em, ephemeral=True)

    async def _on_genre(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not await self._owner_only(interaction):
            return
        value = select.values[0]
        query = None if value == "สุ่ม" else value
        await self._re_pick(interaction, query)

    async def _on_invite(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._owner_only(interaction):
            return
        self._invite_mode = True
        self._build()
        await interaction.response.edit_message(view=self)

    async def _on_friends(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        if not await self._owner_only(interaction):
            return
        friends = select.values
        await interaction.response.defer()
        em = _build_invite_embed(self.game, interaction.user, friends)
        join_view = discord.ui.View()
        join_view.add_item(discord.ui.Button(label="🎮 เข้าร่วมเลย", style=discord.ButtonStyle.link, url=self.game.url))
        await interaction.channel.send(embed=em, view=join_view, allowed_mentions=discord.AllowedMentions(users=True))
        self._invite_mode = False
        self._build()
        await interaction.edit_original_response(view=self)


async def setup(bot):
    await bot.add_cog(FunCog(bot))
