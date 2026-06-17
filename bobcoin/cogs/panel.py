import asyncio
import time

import discord
from discord.ext import commands

from ..bank import (
    ACHIEVEMENTS,
    _ref,
    calc_interest,
    get_achievements,
    get_bank_data,
    get_history,
    get_house_data,
    get_jackpot_pool,
    get_total_outstanding_loans,
    try_daily,
    user_deposit,
    user_withdraw,
    xp_to_level,
)
from ..helpers import parse_positive_int
from .economy import (
    _PanelCtx,
    _get_game_streak,
    _run_bj,
    _run_flip,
    _run_lottery,
    _run_slot,
    _streak_effects,
)

# Per-user cooldowns (mirrors prefix command limits)
_PANEL_CD = {"slot": 30, "flip": 15, "bj": 20, "lottery": 120}
_panel_cd: dict[int, dict[str, float]] = {}


def _cd_remaining(user_id: int, game: str) -> float:
    return max(_PANEL_CD[game] - (time.time() - _panel_cd.get(user_id, {}).get(game, 0)), 0)


def _cd_set(user_id: int, game: str) -> None:
    _panel_cd.setdefault(user_id, {})[game] = time.time()


async def _is_registered(user_id: int) -> bool:
    return (await _ref(user_id).get()).exists


async def _build_panel_embed() -> discord.Embed:
    hd = await get_house_data()
    jackpot = await get_jackpot_pool()
    bal = hd["balance"]
    if bal >= 10_000_000:
        status, color = "🟢 ร่ำรวยมาก", discord.Color.green()
    elif bal >= 1_000_000:
        status, color = "🟡 พอไปได้", discord.Color.yellow()
    elif bal >= 100_000:
        status, color = "🟠 เริ่มสั่นคลอน", discord.Color.orange()
    else:
        status, color = "🔴 แทบล้มละลาย", discord.Color.red()

    em = discord.Embed(
        title="🎰  G U C O I N   C A S I N O",
        description=(
            "**ยินดีต้อนรับสู่ GUCOIN Casino!**\n"
            "กดปุ่มด้านล่างเพื่อเริ่มได้เลย *(สมัครก่อนด้วย `$register`)*"
        ),
        color=color,
    )
    em.add_field(name="💎 Jackpot Pool", value=f"**{jackpot:,}** 🪙", inline=True)
    em.add_field(name="🏛️ คลังหลวง", value=f"{status}  `{bal:,} 🪙`", inline=True)
    em.add_field(name="​", value="​", inline=True)
    em.add_field(
        name="🎮 เกม",
        value=(
            "🎰 **สล็อต** — 3 ตัวเหมือน Jackpot *(8x / 15x / 20x)*\n"
            "🪙 **หัว/ก้อย** — ทาย 50/50 ชนะ **1.8x**\n"
            "🃏 **แบล็คแจ็ค** — Hit/Stand vs Dealer *(1.8x | BJ 2.5x)*\n"
            "🎟️ **หวย 5 ตัว** — ถูก 5 ตัว **50x** | 4 ตัว 8x | 3 ตัว 3x"
        ),
        inline=False,
    )
    em.add_field(
        name="⚡ Streak",
        value="Win 3+ ติด → +5%/ครั้ง (max +25%)  •  แพ้ 5+ ติด → Mercy 3% คืน",
        inline=False,
    )
    em.set_footer(text="กด 🔄 รีเฟรชข้อมูลสด  •  เดิมพันสูงสุด 1,000,000,000 🪙")
    return em


# ── Modals ──────────────────────────────────────────────────────────────────────

class _SlotModal(discord.ui.Modal, title="🎰 สล็อต"):
    amount = discord.ui.TextInput(label="จำนวนเดิมพัน 🪙", placeholder="เช่น 1000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        bet = parse_positive_int(self.amount.value)
        if not bet:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง (1–1,000,000,000)", ephemeral=True)
            return
        _cd_set(interaction.user.id, "slot")
        await interaction.response.defer(thinking=False)
        await _run_slot(_PanelCtx(interaction.channel, interaction.user), bet)


class _FlipModal(discord.ui.Modal, title="🪙 หัว/ก้อย"):
    side = discord.ui.TextInput(label="เลือก: หัว หรือ ก้อย", placeholder="หัว / ก้อย", min_length=2, max_length=4)
    amount = discord.ui.TextInput(label="จำนวนเดิมพัน 🪙", placeholder="เช่น 1000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        s = self.side.value.strip().lower()
        if "หัว" in s or s in ("head", "h", "1"):
            side = "1"
        elif "ก้อย" in s or s in ("tail", "t", "2"):
            side = "2"
        else:
            await interaction.response.send_message("❓ ต้องพิมพ์ **หัว** หรือ **ก้อย**", ephemeral=True)
            return
        bet = parse_positive_int(self.amount.value)
        if not bet:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return
        _cd_set(interaction.user.id, "flip")
        await interaction.response.defer(thinking=False)
        await _run_flip(_PanelCtx(interaction.channel, interaction.user), bet, side)


class _BJModal(discord.ui.Modal, title="🃏 แบล็คแจ็ค"):
    amount = discord.ui.TextInput(label="จำนวนเดิมพัน 🪙", placeholder="เช่น 5000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        bet = parse_positive_int(self.amount.value)
        if not bet:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return
        _cd_set(interaction.user.id, "bj")
        await interaction.response.defer(thinking=False)
        await _run_bj(_PanelCtx(interaction.channel, interaction.user), bet)


class _LotteryModal(discord.ui.Modal, title="🎟️ หวย 5 ตัว"):
    ticket = discord.ui.TextInput(label="เลข 5 หลัก", placeholder="เช่น 12345", min_length=5, max_length=5)
    amount = discord.ui.TextInput(label="จำนวนเดิมพัน 🪙", placeholder="เช่น 100", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        tkt = self.ticket.value.strip()
        if not tkt.isdecimal() or len(tkt) != 5:
            await interaction.response.send_message("❓ เลขต้องเป็น **5 หลัก**", ephemeral=True)
            return
        bet = parse_positive_int(self.amount.value)
        if not bet:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return
        _cd_set(interaction.user.id, "lottery")
        await interaction.response.defer(thinking=False)
        await _run_lottery(_PanelCtx(interaction.channel, interaction.user), bet, tkt)


class _DepositModal(discord.ui.Modal, title="📥 ฝากเงินเข้าคลัง"):
    amount = discord.ui.TextInput(label="จำนวนที่จะฝาก 🪙", placeholder="เช่น 5000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        amt = parse_positive_int(self.amount.value)
        if not amt:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await user_deposit(interaction.user, amt)
        if result is None:
            await interaction.followup.send("💸 เงินในกระเป๋าไม่พอ", ephemeral=True)
            return
        wallet, deposited = result
        em = discord.Embed(title="📥 ฝากสำเร็จ", color=discord.Color.green())
        em.add_field(name="ฝากเข้า", value=f"**{amt:,}** 🪙", inline=False)
        em.add_field(name="👛 กระเป๋า", value=f"{wallet:,} 🪙", inline=True)
        em.add_field(name="🏛️ คลัง", value=f"{deposited:,} 🪙", inline=True)
        await interaction.followup.send(embed=em, ephemeral=True)


class _WithdrawModal(discord.ui.Modal, title="🏧 ถอนจากคลัง"):
    amount = discord.ui.TextInput(label="จำนวนที่จะถอน 🪙", placeholder="เช่น 5000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        amt = parse_positive_int(self.amount.value)
        if not amt:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await user_withdraw(interaction.user, amt)
        if result is False:
            await interaction.followup.send("⚠️ คลังหลวงแห้ง จ่ายไม่ได้ตอนนี้", ephemeral=True)
            return
        if result is None:
            await interaction.followup.send("💸 ฝากไว้ในคลังไม่พอ", ephemeral=True)
            return
        wallet, deposited = result
        em = discord.Embed(title="🏧 ถอนสำเร็จ", color=discord.Color.blue())
        em.add_field(name="ถอนออก", value=f"**{amt:,}** 🪙", inline=False)
        em.add_field(name="👛 กระเป๋า", value=f"{wallet:,} 🪙", inline=True)
        em.add_field(name="🏛️ คลัง", value=f"{deposited:,} 🪙", inline=True)
        await interaction.followup.send(embed=em, ephemeral=True)


class _DuelModal(discord.ui.Modal, title="⚔️ ท้าดวล"):
    target = discord.ui.TextInput(label="User ID หรือ @mention", placeholder="เช่น 123456789 หรือ <@123456789>", min_length=1, max_length=30)
    game = discord.ui.TextInput(label="เกม (flip / slot / bj)", placeholder="flip  /  slot  /  bj", min_length=2, max_length=4)
    amount = discord.ui.TextInput(label="จำนวนเดิมพัน 🪙", placeholder="เช่น 5000", min_length=1, max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        # Resolve member
        raw = self.target.value.strip().strip("<@!>")
        member = None
        if raw.isdigit():
            try:
                member = interaction.guild.get_member(int(raw)) or await interaction.guild.fetch_member(int(raw))
            except Exception:
                pass
        if member is None:
            await interaction.response.send_message("❌ หา user ไม่เจอ — ใส่ User ID ให้ถูกต้อง", ephemeral=True)
            return

        g = self.game.value.strip().lower()
        if g not in ("flip", "slot", "bj"):
            await interaction.response.send_message("❌ เกมต้องเป็น `flip`, `slot`, หรือ `bj`", ephemeral=True)
            return

        bet = parse_positive_int(self.amount.value)
        if not bet:
            await interaction.response.send_message("💸 จำนวนเงินไม่ถูกต้อง", ephemeral=True)
            return

        if member.bot or member == interaction.user:
            await interaction.response.send_message("❌ ห้ามท้าตัวเองหรือบอท", ephemeral=True)
            return

        await interaction.response.defer(thinking=False)
        duel_cog = interaction.client.get_cog("DuelCog")
        if duel_cog is None:
            await interaction.followup.send("❌ ระบบ Duel ไม่พร้อม", ephemeral=True)
            return
        await duel_cog.run_duel(interaction.channel, interaction.user, member, g, bet)


# ── Panel View ──────────────────────────────────────────────────────────────────

class GamePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _guard(self, interaction: discord.Interaction, game: str | None = None) -> bool:
        if not await _is_registered(interaction.user.id):
            await interaction.response.send_message(
                "❌ ยังไม่มีบัญชี! พิมพ์ **`$register`** ก่อนนะ ฟรีด้วย",
                ephemeral=True,
            )
            return False
        if game:
            rem = _cd_remaining(interaction.user.id, game)
            if rem > 0:
                await interaction.response.send_message(
                    f"⏳ รืออีก **{rem:.0f}** วินาที",
                    ephemeral=True,
                )
                return False
        return True

    # ── Row 0: Games ───────────────────────────────────────────────────────────

    @discord.ui.button(label="🎰 สล็อต", style=discord.ButtonStyle.green, custom_id="panel:slot", row=0)
    async def slot_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction, "slot"):
            return
        await interaction.response.send_modal(_SlotModal())

    @discord.ui.button(label="🪙 หัว/ก้อย", style=discord.ButtonStyle.blurple, custom_id="panel:flip", row=0)
    async def flip_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction, "flip"):
            return
        await interaction.response.send_modal(_FlipModal())

    @discord.ui.button(label="🃏 แบล็คแจ็ค", style=discord.ButtonStyle.green, custom_id="panel:bj", row=0)
    async def bj_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction, "bj"):
            return
        await interaction.response.send_modal(_BJModal())

    @discord.ui.button(label="🎟️ หวย", style=discord.ButtonStyle.blurple, custom_id="panel:lottery", row=0)
    async def lottery_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction, "lottery"):
            return
        await interaction.response.send_modal(_LotteryModal())

    # ── Row 1: Finance ─────────────────────────────────────────────────────────

    @discord.ui.button(label="🎁 Daily", style=discord.ButtonStyle.secondary, custom_id="panel:daily", row=1)
    async def daily_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        result = await try_daily(interaction.user.id)
        if result is False:
            await interaction.followup.send("⚠️ คลังหลวงไม่มีเงินพอจ่ายรายวันตอนนี้", ephemeral=True)
            return
        if result is None:
            doc = await _ref(interaction.user.id).get()
            last = int((doc.to_dict() or {}).get("last_daily", 0))
            await interaction.followup.send(
                f"😴 เก็บไปแล้ว มาอีกที <t:{last + 86400}:R>",
                ephemeral=True,
            )
            return
        reward, streak = result
        bar = "🟨" * min(streak, 7) + "⬛" * max(0, 7 - streak)
        em = discord.Embed(title="🎁 รับเงินรายวันสำเร็จ!", color=discord.Color.green())
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        em.add_field(name="💰 ได้รับ", value=f"**{reward:,}** 🪙", inline=True)
        em.add_field(name="📅 Streak", value=f"**{streak} วัน**", inline=True)
        em.add_field(name="​", value=bar, inline=False)
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="📥 ฝาก", style=discord.ButtonStyle.secondary, custom_id="panel:deposit", row=1)
    async def deposit_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(_DepositModal())

    @discord.ui.button(label="🏧 ถอน", style=discord.ButtonStyle.secondary, custom_id="panel:withdraw", row=1)
    async def withdraw_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(_WithdrawModal())

    @discord.ui.button(label="💰 ยอดเงิน", style=discord.ButtonStyle.secondary, custom_id="panel:balance", row=1)
    async def balance_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        doc = await _ref(interaction.user.id).get()
        d = doc.to_dict() or {}
        wallet = int(d.get("wallet", 0))
        deposited = int(d.get("deposited", d.get("bank", 0)))
        total = wallet + deposited
        xp = int(d.get("xp", 0))
        lv = xp_to_level(xp)
        next_lv_xp = (lv + 1) ** 2 * 10
        cur_lv_xp = lv ** 2 * 10
        filled = int((xp - cur_lv_xp) / max(next_lv_xp - cur_lv_xp, 1) * 12)
        xp_bar = "█" * filled + "░" * (12 - filled)
        loan = int(d.get("loan_balance", 0))
        daily_streak = int(d.get("daily_streak", 0))
        interest = calc_interest(deposited)
        color = (
            discord.Color.gold() if total >= 10_000_000
            else discord.Color.green() if total >= 1_000_000
            else discord.Color.blurple()
        )
        em = discord.Embed(color=color)
        em.set_author(name=f"💼 {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        em.add_field(name="👛 กระเป๋า", value=f"**{wallet:,}** 🪙", inline=True)
        em.add_field(name="🏛️ คลัง", value=f"**{deposited:,}** 🪙", inline=True)
        em.add_field(name="💎 รวม", value=f"**{total:,}** 🪙", inline=True)
        em.add_field(name=f"⭐ Level {lv}", value=f"`{xp_bar}` {xp:,} / {next_lv_xp:,} XP", inline=False)
        tags = []
        if daily_streak > 0:
            tags.append(f"📅 Daily Streak **{daily_streak} วัน**")
        if interest > 0:
            tags.append(f"💹 ดอกเบี้ย **{interest:,}** 🪙/วัน")
        if loan > 0:
            tags.append(f"💳 หนี้ **{loan:,}** 🪙")
        if tags:
            em.add_field(name="​", value="  •  ".join(tags), inline=False)
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="🔄 รีเฟรช", style=discord.ButtonStyle.secondary, custom_id="panel:refresh", row=1)
    async def refresh_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        em = await _build_panel_embed()
        await interaction.response.edit_message(embed=em, view=self)

    # ── Row 2: Stats & Info ────────────────────────────────────────────────────

    @discord.ui.button(label="🔥 Streak", style=discord.ButtonStyle.secondary, custom_id="panel:streak", row=2)
    async def streak_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        n, is_win = await _get_game_streak(interaction.user.id)
        if n == 0:
            await interaction.followup.send("📭 ยังไม่มีประวัติเกม ลองเล่นก่อนสิ!", ephemeral=True)
            return
        icon = "🔥" if is_win else "💀"
        label = "Win Streak" if is_win else "Cold Streak"
        bonus_pct, mercy = _streak_effects(n, is_win, 100)
        color = discord.Color.orange() if is_win else discord.Color.dark_red()
        em = discord.Embed(color=color)
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        em.add_field(name=f"{icon} {label}", value=f"**{n} ครั้งติด**", inline=True)
        if bonus_pct > 0:
            em.add_field(name="✨ Bonus ถัดไป", value=f"**+{int(bonus_pct*100)}%**", inline=True)
        elif mercy > 0:
            em.add_field(name="💊 Mercy Ready", value="3% คืนถ้าแพ้", inline=True)
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="🏆 Achievement", style=discord.ButtonStyle.secondary, custom_id="panel:ach", row=2)
    async def ach_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        owned = await get_achievements(interaction.user.id)
        em = discord.Embed(title=f"🏆 Achievements — {interaction.user.display_name}", color=discord.Color.purple())
        lines = []
        for key, (icon, name, desc) in ACHIEVEMENTS.items():
            if key in owned:
                lines.append(f"{icon} **{name}** — {desc}")
            else:
                lines.append(f"🔒 ~~{name}~~ — {desc}")
        em.description = "\n".join(lines)
        em.set_footer(text=f"ปลดล็อค {len(owned)}/{len(ACHIEVEMENTS)}")
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="📋 ประวัติ", style=discord.ButtonStyle.secondary, custom_id="panel:history", row=2)
    async def history_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        entries = await get_history(interaction.user.id, limit=10)
        if not entries:
            await interaction.followup.send("ไม่มีประวัติเลย 👀", ephemeral=True)
            return
        _CMD_ICON = {
            "slot": "🎰", "flip": "🪙", "lottery": "🎟️", "bj": "🃏",
            "deposit": "📥", "withdraw": "🏧", "give": "💸", "receive": "📨",
            "interest": "💹", "daily": "🎁", "loan": "💳", "repay": "✅",
        }
        lines = []
        for e in entries[:10]:
            cmd = e.get("cmd", "?")
            icon = _CMD_ICON.get(cmd, "📋")
            n = e.get("net", 0)
            net_str = f"`+{n:,} 🪙`" if n > 0 else (f"`{n:,} 🪙`" if n < 0 else "`คืนทุน`")
            ts = e.get("ts", 0)
            t = f"<t:{ts}:R>" if ts else ""
            lines.append(f"{icon} **{cmd}** {net_str} {t}")
        em = discord.Embed(
            title=f"📋 ประวัติล่าสุด",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        em.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="🏛️ คลัง", style=discord.ButtonStyle.secondary, custom_id="panel:house", row=2)
    async def house_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        import asyncio
        (hd, jackpot), outstanding = await asyncio.gather(
            asyncio.gather(get_house_data(), get_jackpot_pool()),
            get_total_outstanding_loans(),
        )
        bal, tin, tout = hd["balance"], hd["total_in"], hd["total_out"]
        profit = (tin - tout) + outstanding
        if bal >= 10_000_000:
            color, status = discord.Color.green(), "🟢 ร่ำรวยมาก"
        elif bal >= 1_000_000:
            color, status = discord.Color.yellow(), "🟡 พอไปได้"
        elif bal >= 100_000:
            color, status = discord.Color.orange(), "🟠 เริ่มสั่นคลอน"
        else:
            color, status = discord.Color.red(), "🔴 แทบล้มละลาย"
        em = discord.Embed(title="🏛️ คลังหลวง GUCOIN", color=color)
        em.add_field(name="💰 ยอดคงเหลือ", value=f"**{bal:,}** 🪙", inline=True)
        em.add_field(name="สถานะ", value=status, inline=True)
        em.add_field(name="💎 Jackpot Pool", value=f"**{jackpot:,}** 🪙", inline=True)
        em.add_field(name="📥 เข้าทั้งหมด", value=f"**{tin:,}** 🪙", inline=True)
        em.add_field(name="📤 ออกทั้งหมด", value=f"**{tout:,}** 🪙", inline=True)
        em.add_field(name="💸 ยอดหนี้ค้างชำระ", value=f"**{outstanding:,}** 🪙", inline=True)
        profit_str = f"{'🟢 +' if profit >= 0 else '🔴 '}{profit:,} 🪙"
        em.add_field(name="📊 กำไรสุทธิ", value=profit_str, inline=True)
        em.set_footer(text="กำไรสุทธิ = รายรับ−รายจ่าย+ยอดหนี้ค้างชำระ (ไม่นับเงินต้นกู้)")
        await interaction.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="🥇 อันดับ", style=discord.ButtonStyle.secondary, custom_id="panel:lb", row=2)
    async def lb_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        users = await get_bank_data()
        totals = sorted(
            ((int(a.get("wallet", 0)) + int(a.get("deposited", a.get("bank", 0))), int(uid)) for uid, a in users.items()),
            reverse=True,
        )
        em = discord.Embed(title="🥇 Top 5 ผู้มั่งคั่ง", color=discord.Color.purple())
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for i, (amt, uid) in enumerate(totals[:5], 1):
            try:
                user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
                name = user.display_name
            except Exception:
                name = f"User {uid}"
            lv = xp_to_level(int(users.get(str(uid), {}).get("xp", 0)))
            em.add_field(
                name=f"{medals.get(i, f'**#{i}**')} {name}",
                value=f"{amt:,} 🪙  •  ⭐ Lv.{lv}",
                inline=False,
            )
        await interaction.followup.send(embed=em, ephemeral=True)

    # ── Row 3: Duel ────────────────────────────────────────────────────────────

    @discord.ui.button(label="⚔️ ท้าดวล", style=discord.ButtonStyle.danger, custom_id="panel:duel", row=3)
    async def duel_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(_DuelModal())


# ── Cog ────────────────────────────────────────────────────────────────────────

class PanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._casino_ids: set[int] = set()
        bot.add_view(GamePanelView())

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for ch in guild.text_channels:
                if "casino" in ch.name.lower():
                    self._casino_ids.add(ch.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.channel.id not in self._casino_ids:
            return
        if not message.author.bot:
            try:
                await message.delete()
            except discord.Forbidden:
                pass
        elif message.author == message.guild.me:
            asyncio.create_task(self._auto_delete(message, delay=45))

    @staticmethod
    async def _auto_delete(message: discord.Message, delay: int):
        await asyncio.sleep(delay)
        try:
            msg = await message.channel.fetch_message(message.id)
            if not msg.pinned:
                await msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    @commands.command(aliases=["แผง", "lobby", "casino"])
    async def panel(self, ctx):
        em = await _build_panel_embed()
        await ctx.send(embed=em, view=GamePanelView())

    @commands.command(aliases=["ตั้งค่าห้อง"])
    async def setup(self, ctx):
        if not (ctx.author.guild_permissions.manage_channels or await ctx.bot.is_owner(ctx.author)):
            await ctx.send("❌ ต้องมีสิทธิ์ **Manage Channels** หรือเป็น bot owner")
            return
        existing = discord.utils.get(ctx.guild.text_channels, name="🎰-casino")
        if existing:
            await ctx.send(f"ห้อง {existing.mention} มีอยู่แล้วนะ 👀")
            return

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(
                send_messages=False, read_messages=True, add_reactions=False
            ),
            ctx.guild.me: discord.PermissionOverwrite(
                send_messages=True, manage_messages=True, embed_links=True
            ),
        }
        channel = await ctx.guild.create_text_channel(
            "🎰-casino",
            overwrites=overwrites,
            category=ctx.channel.category,
            topic="🎰 GUCOIN Casino — กดปุ่มเล่นได้เลย | $register เพื่อเปิดบัญชี",
            reason=f"GUCOIN Casino panel setup by {ctx.author}",
        )
        self._casino_ids.add(channel.id)
        panel_msg = await channel.send(embed=await _build_panel_embed(), view=GamePanelView())
        try:
            await panel_msg.pin()
        except discord.Forbidden:
            pass

        em = discord.Embed(
            title="✅ Casino Panel พร้อมแล้ว!",
            description=(
                f"สร้างห้อง {channel.mention} เรียบร้อย 🎰\n\n"
                "**วิธีใช้:**\n"
                "• User อ่านได้อย่างเดียว ส่งข้อความไม่ได้ (ห้องสะอาด)\n"
                "• กดปุ่มเพื่อเล่นเกม / ดูข้อมูล / ฝากถอนได้เลย\n"
                "• กด 🔄 เพื่ออัปเดตข้อมูลสด\n\n"
                "หากต้องการรีเซ็ต: ลบห้องแล้วรัน `$setup` ใหม่"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=em)


async def setup(bot: commands.Bot):
    await bot.add_cog(PanelCog(bot))
