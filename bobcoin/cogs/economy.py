import asyncio
import logging
import random

import discord
from discord.ext import commands, tasks

from ..bank import (
    ACHIEVEMENTS,
    _ref,
    accrue_loan_interest,
    ai_loan_limit,
    calc_interest,
    charge_wallet,
    get_achievements,
    get_balance,
    get_cooldown,
    get_game_stats,
    get_history,
    get_house_data,
    get_jackpot_pool,
    get_leaderboard,
    get_loan_info,
    get_total_outstanding_loans,
    get_user_luck,
    has_transfer_relation,
    house_receive,
    house_status_band,
    log_history,
    open_account,
    pay_interest_all,
    repay_loan,
    rob_transfer,
    set_cooldown,
    set_user_luck,
    take_loan,
    transfer_to_user,
    try_daily,
    update_bank,
    user_deposit,
    user_withdraw,
    xp_to_level,
)
from ..gameplay import (
    _HOUSE_BAND_COLORS,
    _get_game_streak,
    _post_game,
    _run_bj,
    _run_flip,
    _run_lottery,
    _run_slot,
    _spawn,
)
from ..games import _streak_effects
from ..helpers import is_bot_admin, parse_amount_or_reply, parse_positive_int
from ..settings import SLOT_JACKPOT_BASE

logger = logging.getLogger("bobcoin.economy")


class NotRegistered(Exception):
    pass


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._interest_loop.start()

    def cog_unload(self):
        self._interest_loop.cancel()

    @tasks.loop(hours=24)
    async def _interest_loop(self):
        n, total = await pay_interest_all()
        logger.info("Daily interest paid: %d users, %d total", n, total)
        lu, li = await accrue_loan_interest()
        if lu:
            logger.info("Loan interest charged: %d users, %d total", lu, li)

    @_interest_loop.before_loop
    async def _before_interest(self):
        await self.bot.wait_until_ready()

    async def cog_before_invoke(self, ctx):
        if ctx.command.name == "register":
            return
        doc = await _ref(ctx.author.id).get()
        if not doc.exists:
            em = discord.Embed(
                title="❌ ยังไม่มีบัญชี",
                description=f"พิมพ์ **`{ctx.prefix}register`** ก่อนนะ ฟรีด้วย ทำเลย",
                color=discord.Color.red(),
            )
            await ctx.send(embed=em)
            raise NotRegistered()

    @commands.command(aliases=["สมัคร"])
    async def register(self, ctx):
        if (await _ref(ctx.author.id).get()).exists:
            await ctx.send("มีบัญชีอยู่แล้วนะ 😅")
            return

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        # step 1: ชื่อเล่น
        await ctx.send(
            f"👋 ยินดีต้อนรับ **{ctx.author.display_name}**!\n"
            "**[1/2]** อยากให้เรียกว่าอะไรใน GUCOIN? (พิมพ์ชื่อเล่นได้เลย)"
        )
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30)
            nickname = msg.content.strip()[:32]
        except TimeoutError:
            await ctx.send("⏰ หมดเวลา ลองสมัครใหม่อีกทีนะ")
            return

        # step 2: คำอธิบายตัวเอง
        await ctx.send("**[2/2]** แนะนำตัวเองสั้นๆ หน่อย (หรือพิมพ์ `-` เพื่อข้าม)")
        try:
            msg2 = await self.bot.wait_for("message", check=check, timeout=60)
            bio = msg2.content.strip()[:100]
            if bio == "-":
                bio = ""
        except TimeoutError:
            bio = ""

        # เก็บข้อมูล Discord + ข้อมูลที่กรอก
        member = ctx.author
        extra = {
            "discord_id": str(member.id),
            "username": member.name,
            "display_name": member.display_name,
            "nickname": nickname,
            "bio": bio,
            "avatar_url": str(member.display_avatar.url),
            "created_at": member.created_at.isoformat(),
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
        }

        await open_account(member, extra)

        em = discord.Embed(
            title="🎉 ยินดีต้อนรับสู่ GUCOIN!",
            description=f"**{nickname}** เปิดบัญชีสำเร็จแล้ว 🪙",
            color=discord.Color.green(),
        )
        em.set_thumbnail(url=member.display_avatar.url)
        em.add_field(name="👤 ชื่อเล่น", value=nickname, inline=True)
        em.add_field(name="🏷️ Discord", value=f"`{member.name}`", inline=True)
        if bio:
            em.add_field(name="📝 แนะนำตัว", value=bio, inline=False)
        em.add_field(name="👛 กระเป๋า", value="**0** 🪙", inline=True)
        em.add_field(name="🏛️ คลังหลวง", value="**0** 🪙", inline=True)
        em.add_field(
            name="🚀 เริ่มต้นยังไง",
            value=f"`{ctx.prefix}daily` รับเงินฟรีรายวัน\n`{ctx.prefix}slot` เล่นสล็อต\n`{ctx.prefix}command` ดูคำสั่งทั้งหมด",
            inline=False,
        )
        em.set_footer(text="⭐ Level 0 • XP 0 • ขอให้โชคดี")
        await ctx.send(embed=em)

    @commands.command(aliases=["bal", "backpack", "Backpack"])
    async def balance(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        doc = await _ref(target.id).get()
        d = doc.to_dict() or {}
        wallet = int(d.get("wallet", 0))
        deposited = int(d.get("deposited", d.get("bank", 0)))
        total = wallet + deposited
        xp = int(d.get("xp", 0))
        lv = xp_to_level(xp)
        next_lv_xp = (lv + 1) ** 2 * 10
        cur_lv_xp = lv ** 2 * 10
        progress = xp - cur_lv_xp
        needed = next_lv_xp - cur_lv_xp
        filled = int(progress / needed * 12) if needed > 0 else 12
        xp_bar = "█" * filled + "░" * (12 - filled)
        daily_streak = int(d.get("daily_streak", 0))
        luck = float(d.get("luck", 1.0))
        loan_balance = int(d.get("loan_balance", 0))

        color = discord.Color.gold() if total >= 10_000_000 else (discord.Color.green() if total >= 1_000_000 else discord.Color.blurple())
        em = discord.Embed(color=color)
        em.set_author(name=f"💰 {target.display_name}", icon_url=target.display_avatar.url)
        em.add_field(name="👛 กระเป๋า", value=f"**{wallet:,}** 🪙", inline=True)
        em.add_field(name="🏛️ คลังหลวง", value=f"**{deposited:,}** 🪙", inline=True)
        em.add_field(name="💎 รวม", value=f"**{total:,}** 🪙", inline=True)
        if loan_balance > 0:
            em.add_field(name="💳 หนี้คงค้าง", value=f"**{loan_balance:,}** 🪙 *(0.3%/วัน)*", inline=False)
        em.add_field(name=f"⭐ Level {lv}", value=f"`{xp_bar}` {xp:,} / {next_lv_xp:,} XP", inline=False)
        tags = []
        if daily_streak > 0:
            tags.append(f"📅 Daily Streak **{daily_streak} วัน**")
        if luck != 1.0:
            tags.append(f"{'🍀' if luck > 1 else '💀'} Luck **{luck}x**")
        if tags:
            em.add_field(name="​", value="  •  ".join(tags), inline=False)
        em.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=em)

    @commands.command()
    @commands.cooldown(1, 120, commands.BucketType.user)
    async def lottery(self, ctx, text=None, amount=None):
        if text is None:
            await ctx.send("ใส่เลขด้วยสิเฮ้ย (ตัวเลข 5 หลัก)")
            return
        if not text.isdecimal() or len(text) != 5:
            await ctx.send("ใส่ตัวเลข 5 หลักโว้ยยย")
            return
        ticket_cost = parse_positive_int(amount or 100)
        if ticket_cost is None:
            await ctx.send("เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000,000")
            return
        await _run_lottery(ctx, ticket_cost, text)

    @commands.command(pass_context=True)
    @commands.has_permissions(manage_messages=True)
    async def shop(self, ctx, member: discord.Member = None, *, role: discord.Role = None):
        await self._buy_role(ctx, member, role, price=1000)

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    @commands.has_any_role("Profile")
    async def BD(self, ctx, member: discord.Member = None, *, role: discord.Role = None):
        await self._buy_role(ctx, member, role, price=100000)

    async def _buy_role(self, ctx, member, role, price: int):
        if member is None:
            await ctx.send("ใส่ชื่อที่จะซื้อของให้"); return
        if role is None:
            await ctx.send("ใส่สิ่งของที่ต้องการ"); return
        me = ctx.guild.me
        if me and role >= me.top_role:
            await ctx.send("บอทไม่มีสิทธิ์ให้ role นี้"); return
        if ctx.guild.owner != ctx.author and role >= ctx.author.top_role:
            await ctx.send("คุณให้ role ที่สูงกว่าหรือเท่ากับตัวเองไม่ได้"); return
        if await charge_wallet(ctx.author, price) is None:
            await ctx.send("เงินไม่พอ # จ น"); return
        try:
            await member.add_roles(role)
        except (discord.Forbidden, discord.HTTPException):
            await update_bank(ctx.author, price)
            await ctx.send("ให้ role ไม่สำเร็จ คืนเงินแล้ว"); return
        await ctx.send(f"{member} was given {role}")

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def slot(self, ctx, amount=None):
        amount = await parse_amount_or_reply(ctx, amount, "ใส่เงินที่พนันด้วยสิเฮ้ย!")
        if amount is None:
            return
        await _run_slot(ctx, amount)

    @commands.command()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def withdraw(self, ctx, amount=None):
        amount = await parse_amount_or_reply(ctx, amount, "ใส่จำนวนเงินที่จะถอนด้วยสิเฮ้ย!")
        if amount is None:
            return
        result = await user_withdraw(ctx.author, amount)
        if result is False:
            await ctx.send("⚠️ คลังหลวงแห้ง จ่ายไม่ได้ตอนนี้")
            return
        if result is None:
            await ctx.send("ฝากไว้ในคลังไม่พอจะถอน ดู `$balance` ก่อนนะ")
            return
        wallet, deposited = result
        em = discord.Embed(title="🏧 ถอนจากคลังหลวงสำเร็จ", color=discord.Color.blue())
        em.add_field(name="ถอนออก", value=f"**{amount:,}** 🪙", inline=False)
        em.add_field(name="👛 กระเป๋าเงิน", value=f"{wallet:,} 🪙", inline=True)
        em.add_field(name="🏛️ ฝากในคลัง", value=f"{deposited:,} 🪙", inline=True)
        await ctx.send(embed=em)
        _spawn(log_history(ctx.author.id, {"cmd": "withdraw", "amount": amount}))

    @commands.command(aliases=["lb"])
    async def leaderboard(self, ctx, x=3):
        x = parse_positive_int(x, max_value=10) or 3
        # indexed query on the denormalized `total` field — no full-collection scan
        entries = await get_leaderboard(x)
        em = discord.Embed(
            title=f"Top {x} จตุรเทพแห่งความมั่งคั่ง",
            color=discord.Color.purple(),
        )
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for index, (uid, data) in enumerate(entries, start=1):
            medal = medals.get(index, f"**#{index}**")
            amt = int(data.get("wallet", 0)) + int(data.get("deposited", data.get("bank", 0)))
            lv = xp_to_level(int(data.get("xp", 0)))
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    em.add_field(name=f"{medal} User {uid}", value=f"{amt:,} 🪙  •  ⭐ Lv.{lv}", inline=False)
                    continue
            em.add_field(name=f"{medal} {user.display_name}", value=f"{amt:,} 🪙  •  ⭐ Lv.{lv}", inline=False)
        await ctx.send(embed=em)

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def deposit(self, ctx, amount=None):
        amount = await parse_amount_or_reply(ctx, amount, "ใส่จำนวนเงินที่จะฝากด้วยสิเฮ้ย!")
        if amount is None:
            return
        result = await user_deposit(ctx.author, amount)
        if result is None:
            await ctx.send("เงินในกระเป๋าไม่พอ # จ น")
            return
        wallet, deposited = result
        em = discord.Embed(title="🏛️ ฝากเข้าคลังหลวงสำเร็จ", color=discord.Color.green())
        em.add_field(name="ฝากเข้า", value=f"**{amount:,}** 🪙", inline=False)
        em.add_field(name="👛 กระเป๋าเงิน", value=f"{wallet:,} 🪙", inline=True)
        em.add_field(name="🏛️ ฝากในคลัง", value=f"{deposited:,} 🪙", inline=True)
        em.set_footer(text="ถอนกลับได้ด้วย $withdraw (ขึ้นอยู่กับสถานะคลัง)")
        await ctx.send(embed=em)
        _spawn(log_history(ctx.author.id, {"cmd": "deposit", "amount": amount}))

    @commands.command(aliases=["flipcoin", "filpcoin"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def flip(self, ctx, text=None, amount=None):
        if text is None or text not in {"1", "2"}:
            await ctx.send("กรุณาใส่เลขที่จะทาย\nหัว = 1\nก้อย = 2")
            return
        amount = await parse_amount_or_reply(ctx, amount, "ใส่เงินที่พนันด้วยสิเฮ้ย!")
        if amount is None:
            return
        await _run_flip(ctx, amount, text)

    @commands.command(aliases=["pay", "send"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def give(self, ctx, member: discord.Member = None, amount=None):
        if member is None:
            await ctx.send("ระบุผู้รับด้วย @mention เลย")
            return
        if member == ctx.author:
            await ctx.send("โอนให้ตัวเองไม่ได้น้า 🤨")
            return
        if member.bot:
            await ctx.send("โอนให้บอทไม่ได้ 🤖")
            return

        amount = await parse_amount_or_reply(ctx, amount, "ใส่จำนวนเงินที่จะโอนด้วย!")
        if amount is None:
            return

        result = await transfer_to_user(ctx.author, member, amount)
        if result is False:
            await ctx.send(
                f"❌ **{member.display_name}** ยังไม่ได้เปิดบัญชี!\n"
                f"บอกให้ไปพิมพ์ `{ctx.prefix}register` ก่อนนะ"
            )
            return
        if result is None:
            await ctx.send("💸 เงินในกระเป๋าไม่พอ # จ น")
            return

        em = discord.Embed(title="✅ โอนเงินสำเร็จ", color=discord.Color.green())
        em.add_field(name="จาก", value=ctx.author.display_name, inline=True)
        em.add_field(name="ถึง", value=member.display_name, inline=True)
        em.add_field(name="จำนวน", value=f"**{amount:,}** 🪙", inline=False)
        em.add_field(name="👛 กระเป๋าที่เหลือ", value=f"{result:,} 🪙", inline=False)
        em.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=em)
        _spawn(log_history(ctx.author.id, {"cmd": "give", "amount": amount, "to_id": str(member.id), "to_name": member.display_name, "net": -amount}))
        _spawn(log_history(member.id, {"cmd": "receive", "amount": amount, "from_id": str(ctx.author.id), "from_name": ctx.author.display_name, "net": amount}))

    @commands.command(aliases=["รายวัน", "เช็คอิน"])
    async def daily(self, ctx):
        result = await try_daily(ctx.author.id)
        if result is False:
            await ctx.send("⚠️ คลังหลวงไม่มีเงินพอจ่ายรายวันตอนนี้ ลองใหม่ภายหลัง")
            return
        if result is None:
            doc = await _ref(ctx.author.id).get()
            last = int((doc.to_dict() or {}).get("last_daily", 0))
            next_ts = last + 86_400
            em = discord.Embed(
                description=f"เก็บรายวันไปแล้ว มาใหม่ได้ <t:{next_ts}:R> 😴",
                color=discord.Color.red(),
            )
            await ctx.send(embed=em)
            return
        reward, streak = result
        streak_bar = "🟨" * min(streak, 7) + "⬛" * max(0, 7 - streak)
        em = discord.Embed(title="🎁 รับเงินรายวันสำเร็จ!", color=discord.Color.green())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="💰 ได้รับ", value=f"**{reward:,}** 🪙", inline=True)
        em.add_field(name="📅 Streak", value=f"**{streak} วัน**", inline=True)
        em.add_field(name="ความต่อเนื่อง", value=streak_bar, inline=False)
        if streak >= 7:
            em.add_field(name="🔥", value="ครบ 7 วัน! ไม่เกรียนก็มีวินัย", inline=False)
        em.set_footer(text="มาทุกวันได้เงินเพิ่ม • streak หายถ้าห่างเกิน 48 ชม.")
        await ctx.send(embed=em)
        if streak >= 7:
            _spawn(_post_game(ctx, 0, True, 0, False, ["daily_7"]))

    @commands.command(aliases=["เลเวล", "lv", "lvl"])
    async def level(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        doc = await _ref(target.id).get()
        d = doc.to_dict() or {}
        xp = int(d.get("xp", 0))
        lv = xp_to_level(xp)
        next_lv_xp = (lv + 1) ** 2 * 10
        progress = xp - lv ** 2 * 10
        needed = next_lv_xp - lv ** 2 * 10
        bar_filled = int(progress / needed * 10) if needed > 0 else 10
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        em = discord.Embed(title=f"⭐ Level {lv}", color=discord.Color.gold())
        em.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        em.add_field(name="XP", value=f"{xp:,} / {next_lv_xp:,}", inline=True)
        em.add_field(name="Progress", value=f"`{bar}` {int(progress/needed*100) if needed else 100}%", inline=False)
        em.set_footer(text="XP ได้จากการเล่นเกม • ยิ่งเดิมพันเยอะยิ่งได้ XP เยอะ")
        await ctx.send(embed=em)

    @commands.command(aliases=["badge", "badges", "ach"])
    async def achievements(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        owned = await get_achievements(target.id)
        em = discord.Embed(title=f"🏆 Achievements — {target.display_name}", color=discord.Color.purple())
        lines = []
        for key, (icon, name, desc) in ACHIEVEMENTS.items():
            if key in owned:
                lines.append(f"{icon} **{name}** — {desc}")
            else:
                lines.append(f"🔒 ~~{name}~~ — {desc}")
        em.description = "\n".join(lines)
        em.set_footer(text=f"ปลดล็อคแล้ว {len(owned)}/{len(ACHIEVEMENTS)}")
        await ctx.send(embed=em)

    @commands.command(aliases=["ดอกเบี้ย"])
    async def interest(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        _, deposited = await get_balance(target)
        daily = calc_interest(deposited)
        if deposited >= 1_000_000:
            rate_str = "0.20%"
        elif deposited >= 100_000:
            rate_str = "0.15%"
        else:
            rate_str = "0.10%"
        em = discord.Embed(title="💹 ดอกเบี้ยรายวัน", color=discord.Color.green())
        em.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        em.add_field(name="🏛️ ฝากในคลัง", value=f"{deposited:,} 🪙", inline=True)
        em.add_field(name="อัตรา", value=rate_str + "/วัน", inline=True)
        em.add_field(name="💰 ได้รับทุกวัน", value=f"**{daily:,}** 🪙", inline=False)
        em.set_footer(text="จ่ายอัตโนมัติทุก 24 ชม. • ขึ้นอยู่กับสถานะคลังหลวง")
        await ctx.send(embed=em)

    @commands.command(aliases=["สตรีค"])
    async def streak(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        n, is_win = await _get_game_streak(target.id)
        if n == 0:
            em = discord.Embed(description=f"**{target.display_name}** ยังไม่มีประวัติเกม 📭", color=discord.Color.greyple())
            await ctx.send(embed=em)
            return
        icon = "🔥" if is_win else "💀"
        label = "Win Streak" if is_win else "Cold Streak"
        bonus_pct, mercy = _streak_effects(n, is_win, 100)
        color = discord.Color.orange() if is_win else discord.Color.dark_red()
        em = discord.Embed(color=color)
        em.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        em.add_field(name=f"{icon} {label}", value=f"**{n} ครั้งติด**", inline=True)
        if bonus_pct > 0:
            em.add_field(name="✨ Next Win Bonus", value=f"**+{int(bonus_pct*100)}%**", inline=True)
        elif mercy > 0:
            em.add_field(name="💊 Mercy Ready", value="**3%** คืนถ้าแพ้", inline=True)
        await ctx.send(embed=em)

    @commands.command(aliases=["bank", "คลัง"])
    async def house(self, ctx):
        hd, outstanding = await asyncio.gather(
            get_house_data(),
            get_total_outstanding_loans(),
        )
        bal, tin, tout = hd["balance"], hd["total_in"], hd["total_out"]
        # profit excludes loan principal: outstanding loans are receivables, not losses
        # interest accrues into loan_balance (not house total_in), so it's captured via outstanding
        profit = (tin - tout) + outstanding
        tier, status_label, status_icon = house_status_band(bal)
        color = _HOUSE_BAND_COLORS[tier]
        status = f"{status_icon} {status_label}"
        em = discord.Embed(title="🏛️ คลังหลวง GUCOIN", color=color)
        em.add_field(name="💰 ยอดคงเหลือ", value=f"**{bal:,}** 🪙", inline=True)
        em.add_field(name="สถานะ", value=status, inline=True)
        em.add_field(name="💎 Jackpot Pool", value=f"**{await get_jackpot_pool():,}** 🪙", inline=True)
        em.add_field(name="📥 เข้าทั้งหมด", value=f"**{tin:,}** 🪙", inline=True)
        em.add_field(name="📤 ออกทั้งหมด", value=f"**{tout:,}** 🪙", inline=True)
        em.add_field(name="💸 ยอดหนี้ค้างชำระ", value=f"**{outstanding:,}** 🪙", inline=True)
        profit_str = f"{'🟢 +' if profit >= 0 else '🔴 '}{profit:,} 🪙"
        em.add_field(name="📊 กำไรสุทธิ", value=profit_str, inline=True)
        em.set_footer(text="กำไรสุทธิ = รายรับ−รายจ่าย+ยอดหนี้ค้างชำระ (ไม่นับเงินต้นกู้)")
        await ctx.send(embed=em)

    @commands.command()
    @commands.check(is_bot_admin)
    async def setluck(self, ctx, member: discord.Member = None, luck: float = 1.0):
        """DEV: set per-user slot luck multiplier. 1.0=normal, 0=never win, 64=always jackpot."""
        if member is None:
            await ctx.send("ระบุ user ด้วย `$setluck @user <multiplier>`")
            return
        luck = max(0.0, min(luck, 200.0))
        await set_user_luck(member.id, luck)
        jp_rate = min(SLOT_JACKPOT_BASE * luck, 0.99) * 100
        await ctx.send(
            f"✅ **{member.display_name}** luck = **{luck}x**\n"
            f"Jackpot rate: **{jp_rate:.2f}%**/spin (ปกติ 1.56%)"
        )

    @commands.command(aliases=["สถิติ"])
    async def stats(self, ctx):
        """$stats — สถิติ house win rate ต่อเกม (ใช้วัดความสมดุล)"""
        data = await get_game_stats()
        if not data:
            await ctx.send("📭 ยังไม่มีสถิติเกมเลย เล่นก่อนสิ!")
            return
        _ICON = {"slot": "🎰", "flip": "🪙", "lottery": "🎟️", "bj": "🃏"}
        em = discord.Embed(title="📊 สถิติ House Win Rate", color=discord.Color.blurple())
        total_games = sum(int(g.get("games", 0)) for g in data.values())
        for game, g in sorted(data.items()):
            games = int(g.get("games", 0))
            if games == 0:
                continue
            wins = int(g.get("house_wins", 0))
            net = int(g.get("house_net", 0))
            pct = wins / games * 100
            icon = _ICON.get(game, "🎮")
            em.add_field(
                name=f"{icon} {game}",
                value=f"**{games:,}** เกม • House ชนะ **{pct:.1f}%** • Net **{net:+,}** 🪙",
                inline=False,
            )
        em.set_footer(text=f"รวม {total_games:,} เกม • jackpot base {SLOT_JACKPOT_BASE:.4%}")
        await ctx.send(embed=em)

    @commands.command(aliases=["โชค"])
    async def luck(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        lk = await get_user_luck(target.id)
        jp_rate = min(SLOT_JACKPOT_BASE * lk, 0.99) * 100
        color = discord.Color.green() if lk >= 1.0 else discord.Color.red()
        em = discord.Embed(color=color)
        em.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        em.add_field(name="🍀 Luck Modifier", value=f"**{lk}x**", inline=True)
        em.add_field(name="🎰 Jackpot Rate", value=f"**{jp_rate:.2f}%** / spin", inline=True)
        base_rate = SLOT_JACKPOT_BASE * 100
        diff = jp_rate - base_rate
        em.add_field(name="vs ปกติ", value=f"{'▲' if diff >= 0 else '▼'} {abs(diff):.2f}%", inline=True)
        await ctx.send(embed=em)

    @commands.command(aliases=["bj", "blackjack", "แบล็คแจ็ค"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def bjgame(self, ctx, amount=None):
        amount = await parse_amount_or_reply(ctx, amount, "ใส่เงินเดิมพันด้วย!", "เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000,000")
        if amount is None:
            return
        await _run_bj(ctx, amount)

    @commands.command(aliases=["ปล้น", "steal"])
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def rob(self, ctx, member: discord.Member = None):
        if not member or member.bot or member == ctx.author:
            await ctx.send("ระบุ @user ที่จะปล้น (ห้ามปล้นตัวเอง ห้ามปล้นบอท)")
            return

        # anti self-farming: don't allow robbing accounts you've funded via $give
        if await has_transfer_relation(ctx.author.id, member.id):
            await ctx.send(
                f"❌ ปล้น **{member.display_name}** ไม่ได้ เพราะเคยโอนเงินให้กัน 🤝\n"
                "(กันการเอาเงินเข้าบัญชีสำรองแล้วปล้นตัวเอง)"
            )
            return

        # cooldown persisted in Firestore (survives restarts)
        cooldown_left = await get_cooldown(ctx.author.id, f"rob_{member.id}", 7200)
        if cooldown_left > 0:
            h, m = int(cooldown_left // 3600), int((cooldown_left % 3600) // 60)
            await ctx.send(f"🕐 ยังปล้น **{member.display_name}** ไม่ได้ รออีก **{h}ชม. {m}นาที**")
            return

        target_wallet, _ = await get_balance(member)
        if target_wallet < 500:
            await ctx.send(f"**{member.display_name}** จนเกินปล้น 💸 (ต้องมีอย่างน้อย 500 🪙)")
            return

        await set_cooldown(ctx.author.id, f"rob_{member.id}")
        robber_wallet, _ = await get_balance(ctx.author)
        success = random.random() < 0.35

        if success:
            pct = random.uniform(0.05, 0.15)
            stolen = max(int(target_wallet * pct), 1)
            if not await rob_transfer(ctx.author, member, stolen):
                await ctx.send("มีบางอย่างผิดพลาด ลองใหม่")
                return
            _spawn(log_history(ctx.author.id, {"cmd": "rob", "amount": stolen, "to_id": str(member.id), "to_name": member.display_name, "net": stolen}))
            _spawn(log_history(member.id, {"cmd": "robbed", "amount": stolen, "from_id": str(ctx.author.id), "from_name": ctx.author.display_name, "net": -stolen}))
            em = discord.Embed(title="🦹 ปล้นสำเร็จ!!", color=discord.Color.green())
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            em.set_thumbnail(url=member.display_avatar.url)
            em.add_field(name="เหยื่อ", value=member.display_name, inline=True)
            em.add_field(name="ได้ไป", value=f"**+{stolen:,}** 🪙 ({pct*100:.0f}%)", inline=True)
            em.set_footer(text="ปล้นคนเดิมได้อีกใน 2 ชม.")
        else:
            penalty = min(int(robber_wallet * 0.10), 500_000)
            got_paid = ""
            if penalty > 0 and await rob_transfer(member, ctx.author, penalty):
                got_paid = f"\n**{member.display_name}** ได้รับค่าเสียหาย **{penalty:,}** 🪙"
                _spawn(log_history(ctx.author.id, {"cmd": "rob", "amount": penalty, "to_id": str(member.id), "to_name": member.display_name, "net": -penalty}))
                _spawn(log_history(member.id, {"cmd": "robbed", "amount": penalty, "from_id": str(ctx.author.id), "from_name": ctx.author.display_name, "net": penalty}))
            em = discord.Embed(
                title="🚨 โดนจับ!",
                description=f"**{ctx.author.display_name}** ล้มเหลว เสีย **{penalty:,}** 🪙{got_paid}",
                color=discord.Color.red(),
            )

        await ctx.send(embed=em)

    @commands.command()
    @commands.check(is_bot_admin)
    async def seed(self, ctx, amount: int = 0):
        if amount <= 0:
            await ctx.send("ใส่จำนวนเงินที่จะ seed เข้าคลังด้วย")
            return
        new_bal = await house_receive(amount)
        await ctx.send(f"✅ seed **{amount:,}** เหรียญ เข้าคลังหลวง\nยอดปัจจุบัน: **{new_bal:,}** 🪙")

    @commands.command(aliases=["hist", "ประวัติ"])
    async def history(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        entries = await get_history(target.id)

        if not entries:
            await ctx.send(f"ไม่มีประวัติของ **{target.display_name}** เลยนะ 👀")
            return

        _CMD_ICON = {
            "slot": "🎰", "flip": "🪙", "lottery": "🎟️", "bj": "🃏",
            "deposit": "📥", "withdraw": "📤", "give": "💸",
            "receive": "📨", "interest": "💹", "daily": "🎁",
            "loan": "💳", "repay": "✅", "loan_interest": "📈",
            "rob": "🦹", "robbed": "🚨",
        }

        def _net(e):
            n = e.get("net", 0)
            if n > 0: return f"`+{n:,} 🪙`"
            if n < 0: return f"`{n:,} 🪙`"
            return "`คืนทุน`"

        lines = []
        for e in entries:
            cmd = e.get("cmd", "?")
            icon = _CMD_ICON.get(cmd, "📋")
            ts = e.get("ts", 0)
            t = f"<t:{ts}:R>" if ts else ""

            if cmd == "slot":
                lines.append(f"{icon} **Slot** {e.get('symbols','')} • {_net(e)} • {t}")
            elif cmd == "flip":
                ri = "✅" if e.get("win") else "❌"
                lines.append(f"{icon} **Flip** {e.get('choice','')}→{e.get('drawn','')} {ri} • {_net(e)} • {t}")
            elif cmd == "lottery":
                lines.append(f"{icon} **Lottery** `{e.get('pick','')}` → `{e.get('drawn','')}` [{e.get('match','ไม่ถูก')}] • {_net(e)} • {t}")
            elif cmd == "deposit":
                lines.append(f"{icon} **ฝาก** `+{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "withdraw":
                lines.append(f"{icon} **ถอน** `-{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "give":
                lines.append(f"{icon} **โอน** → {e.get('to_name','?')} `{e.get('net',0):,} 🪙` • {t}")
            elif cmd == "receive":
                lines.append(f"{icon} **รับโอน** จาก {e.get('from_name','?')} `+{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "interest":
                lines.append(f"{icon} **ดอกเบี้ย** `+{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "daily":
                lines.append(f"{icon} **Daily** streak {e.get('streak',0)} `+{e.get('reward', e.get('amount',0)):,} 🪙` • {t}")
            elif cmd == "loan":
                lines.append(f"{icon} **กู้เงิน** `+{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "repay":
                lines.append(f"{icon} **ชำระหนี้** `-{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "loan_interest":
                lines.append(f"{icon} **หนี้เพิ่ม** (ดอกเบี้ย) `+{e.get('amount',0):,} 🪙` • {t}")
            elif cmd == "bj":
                res = e.get("result", "?")
                res_label = {"win": "✅ ชนะ", "lose": "❌ แพ้", "bust": "💥 Bust", "push": "🤝 Push", "blackjack": "🃏 BJ!"}.get(res, res)
                lines.append(f"{icon} **Blackjack** {res_label} • {_net(e)} • {t}")
            else:
                lines.append(f"📋 `{cmd}` {_net(e)} • {t}")

        em = discord.Embed(
            title=f"📋 ประวัติของ {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        em.set_thumbnail(url=target.display_avatar.url)
        em.set_footer(text=f"แสดง {len(entries)} รายการล่าสุด")
        await ctx.send(embed=em)

    @commands.command(aliases=["กู้", "borrow"])
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def loan(self, ctx, amount=None):
        """$loan [amount] — กู้เงินจากธนาคาร หรือดูวงเงินถ้าไม่ใส่จำนวน"""
        info = await get_loan_info(ctx.author.id)

        if amount is None:
            lb   = info["loan_balance"]
            used = info["loan_limit"] - info["available"]
            pct  = int(used / max(info["loan_limit"], 1) * 100)
            bar  = "█" * (pct // 10) + "░" * (10 - pct // 10)

            color = discord.Color.green() if lb == 0 else (discord.Color.orange() if pct >= 70 else discord.Color.blue())
            em = discord.Embed(title="💳 สถานะวงเงินกู้", color=color)
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            em.add_field(name="หนี้คงค้าง",   value=f"**{lb:,}** 🪙",                    inline=True)
            em.add_field(name="กู้ได้อีก",    value=f"**{info['available']:,}** 🪙",      inline=True)
            em.add_field(name="วงเงินรวม",    value=f"**{info['loan_limit']:,}** 🪙",     inline=True)
            em.add_field(name="การใช้งาน",    value=f"`{bar}` {pct}%",                    inline=False)
            if lb > 0:
                em.add_field(name="ดอกเบี้ย/วัน", value=f"**{info['daily_interest']:,}** 🪙 (0.3%)", inline=True)
                ts = info["loan_taken_at"]
                if ts:
                    em.add_field(name="กู้ครั้งแรก", value=f"<t:{ts}:R>", inline=True)
            em.add_field(
                name="วงเงินมาจากไหน",
                value=f"ฐาน 50,000 + Level {info['level']}×10,000 + ฝาก×0.3\n🤖 กู้เกินวงเงิน AI พิจารณาได้สูงสุด 20% ของคลัง",
                inline=False,
            )
            em.set_footer(text="$loan <จำนวน> เพื่อกู้ • $repay <จำนวน|all> เพื่อชำระ")
            await ctx.send(embed=em)
            return

        parsed = parse_positive_int(amount)
        if parsed is None:
            await ctx.send("ใส่จำนวนเงินที่ถูกต้องนะ")
            return

        ai_amt = 0
        if parsed > info["available"]:
            thinking = await ctx.send("🤖 วงเงินเกิน AI กำลังพิจารณา...")
            ai_amt = await ai_loan_limit(ctx.author.id, parsed)
            await thinking.delete()
            if ai_amt < parsed:
                em = discord.Embed(
                    description=(
                        f"❌ AI ไม่อนุมัติวงเงินนี้\nสูงสุดที่ได้รับ: **{ai_amt:,}** 🪙"
                        if ai_amt > 0 else
                        "❌ ธนาคารไม่มีทุนสำรองพอ หรือ AI ประเมินความเสี่ยงสูงเกิน"
                    ),
                    color=discord.Color.red(),
                )
                await ctx.send(embed=em)
                return

        error = await take_loan(ctx.author.id, parsed, ai_approved=info["loan_balance"] + ai_amt)
        if error:
            em = discord.Embed(description=f"❌ {error}", color=discord.Color.red())
            await ctx.send(embed=em)
            return

        new_info = await get_loan_info(ctx.author.id)
        em = discord.Embed(
            title="💳 กู้เงินสำเร็จ",
            description=f"ได้รับ **{parsed:,}** 🪙 เข้ากระเป๋าแล้ว",
            color=discord.Color.blue(),
        )
        em.add_field(name="หนี้ทั้งหมด",  value=f"**{new_info['loan_balance']:,}** 🪙",      inline=True)
        em.add_field(name="กู้ได้อีก",    value=f"**{new_info['available']:,}** 🪙",          inline=True)
        em.add_field(name="ดอกเบี้ย/วัน", value=f"**{new_info['daily_interest']:,}** 🪙",     inline=True)
        em.set_footer(text="ดอกเบี้ย 0.3%/วัน ทบอัตโนมัติ • $repay all เพื่อชำระทั้งหมด")
        await ctx.send(embed=em)

    @commands.command(aliases=["ชำระ", "paydebt"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def repay(self, ctx, amount=None):
        """$repay <จำนวน|all> — ชำระหนี้คืนธนาคาร"""
        info = await get_loan_info(ctx.author.id)

        if amount is None:
            # ไม่ใส่ args → แสดง loan info เหมือน $loan
            if info["loan_balance"] == 0:
                await ctx.send("ไม่มียอดหนี้ค้างอยู่เลยนะ 😄 ชีวิตดีงาม")
                return
            em = discord.Embed(
                title="💳 ยอดหนี้ปัจจุบัน",
                description=f"หนี้คงค้าง **{info['loan_balance']:,}** 🪙\nดอกเบี้ย **{info['daily_interest']:,}** 🪙/วัน",
                color=discord.Color.orange(),
            )
            em.set_footer(text="$repay <จำนวน> หรือ $repay all เพื่อชำระ")
            await ctx.send(embed=em)
            return

        if info["loan_balance"] <= 0:
            await ctx.send("ไม่มียอดหนี้ค้างนะ 😄")
            return

        if amount.lower() == "all":
            parsed = info["loan_balance"]
        else:
            parsed = parse_positive_int(amount)
            if parsed is None:
                await ctx.send("ใส่จำนวนเงินที่ถูกต้องนะ")
                return

        actual, error = await repay_loan(ctx.author.id, parsed)
        if error:
            em = discord.Embed(description=f"❌ {error}", color=discord.Color.red())
            await ctx.send(embed=em)
            return

        new_info = await get_loan_info(ctx.author.id)
        paid_off  = new_info["loan_balance"] == 0
        color     = discord.Color.green() if paid_off else discord.Color.teal()
        em = discord.Embed(
            title="✅ ชำระหนี้สำเร็จ" + (" 🎉" if paid_off else ""),
            description=f"ชำระ **{actual:,}** 🪙 เรียบร้อย" + (" หนี้หมดแล้ว ชีวิตปลอดหนี้!" if paid_off else ""),
            color=color,
        )
        if not paid_off:
            em.add_field(name="หนี้คงเหลือ",  value=f"**{new_info['loan_balance']:,}** 🪙",  inline=True)
            em.add_field(name="ดอกเบี้ย/วัน", value=f"**{new_info['daily_interest']:,}** 🪙", inline=True)
        await ctx.send(embed=em)

    @commands.command()
    async def item(self, ctx):
        await ctx.send("@watch | @Profile")


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
