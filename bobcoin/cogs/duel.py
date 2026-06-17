import asyncio
import random

import discord
from discord.ext import commands

from ..bank import (
    _ref,
    charge_wallet,
    get_effective_luck,
    house_receive,
    log_history,
    update_bank,
)
from ..helpers import parse_positive_int
from ..settings import SLOT_SYMBOLS
from .economy import _BJView, _bj_draw, _bj_str, _bj_total, _lucky_card

_HOUSE_CUT = 0.05

GAME_LABELS = {"flip": "🪙 หัว/ก้อย", "slot": "🎰 สล็อต", "bj": "🃏 แบล็คแจ็ค"}


async def _refund_tie(player_a: discord.Member, player_b: discord.Member, net_pot: int) -> int:
    refund = net_pot // 2
    remainder = net_pot - refund * 2
    await asyncio.gather(update_bank(player_a, refund), update_bank(player_b, refund))
    if remainder > 0:
        await house_receive(remainder)
    return refund


# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _slot_spin(user_id: int) -> tuple[list[str], int]:
    luck = await get_effective_luck(user_id)
    if random.random() < min(8 / 512 * luck, 0.99):
        s = random.choice(SLOT_SYMBOLS)
        syms = [s, s, s]
    else:
        syms = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        if luck == 0.0 and syms[0] == syms[1] == syms[2]:
            syms[2] = random.choice([x for x in SLOT_SYMBOLS if x != syms[0]])
    jp = syms[0] == syms[1] == syms[2]
    two = not jp and (syms[0] == syms[1] or syms[0] == syms[2] or syms[1] == syms[2])
    if jp and syms[0] == "💀":            score = 200
    elif jp and syms[0] in ("💎", "7️⃣"): score = 150
    elif jp:                               score = 80
    elif two:                              score = 10
    else:                                  score = 0
    return syms, score


def _score_label(s: int) -> str:
    return {200: "☠️ Death!", 150: "💎 Mega!", 80: "🏆 Jackpot!", 10: "✌️ 2 เหมือน"}.get(s, "💨 Miss")


# ── Views ──────────────────────────────────────────────────────────────────────

class DuelChallengeView(discord.ui.View):
    def __init__(self, target_id: int):
        super().__init__(timeout=60)
        self.accepted: bool | None = None
        self._tid = target_id

    async def _check(self, i: discord.Interaction) -> bool:
        if i.user.id != self._tid:
            await i.response.send_message("ไม่ใช่ challenge ของแก!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ รับคำท้า", style=discord.ButtonStyle.green)
    async def accept(self, i, _):
        if not await self._check(i): return
        self.accepted = True; self.stop(); await i.response.defer()

    @discord.ui.button(label="❌ ปฏิเสธ", style=discord.ButtonStyle.red)
    async def decline(self, i, _):
        if not await self._check(i): return
        self.accepted = False; self.stop(); await i.response.defer()

    async def on_timeout(self): self.accepted = None


class _SideView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.side: str | None = None

    async def _pick(self, i: discord.Interaction, side: str):
        if i.user.id != self.user_id:
            await i.response.send_message("ไม่ใช่เทิร์นของแก!", ephemeral=True)
            return
        self.side = side; self.stop(); await i.response.defer()

    @discord.ui.button(label="👑 หัว", style=discord.ButtonStyle.blurple)
    async def heads(self, i, _): await self._pick(i, "1")

    @discord.ui.button(label="🦅 ก้อย", style=discord.ButtonStyle.secondary)
    async def tails(self, i, _): await self._pick(i, "2")


# ── DuelCog ────────────────────────────────────────────────────────────────────

class DuelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def run_duel(self, channel: discord.TextChannel, challenger: discord.Member, target: discord.Member, game: str, bet: int):
        """Entry point used by both prefix command and panel button."""
        async def _err(text: str):
            await channel.send(text)

        if bet <= 0:
            await _err("จำนวนเดิมพันไม่ถูกต้อง")
            return

        c_doc, t_doc = await asyncio.gather(_ref(challenger.id).get(), _ref(target.id).get())
        if not c_doc.exists:
            await _err("ผู้ท้ายังไม่มีบัญชี `$register` ก่อน"); return
        if not t_doc.exists:
            await _err(f"{target.display_name} ยังไม่มีบัญชี"); return
        if int((c_doc.to_dict() or {}).get("wallet", 0)) < bet:
            await _err("เงินในกระเป๋าไม่พอ 💸"); return

        em = discord.Embed(
            title="⚔️  D U E L",
            description=(
                f"{challenger.mention} ท้า {target.mention}\n"
                f"เกม: **{GAME_LABELS[game]}**  •  เดิมพัน **{bet:,}** 🪙 ต่อคน\n\n"
                f"{target.mention} มีเวลา **60 วินาที** ในการตัดสินใจ"
            ),
            color=discord.Color.orange(),
        )
        em.set_footer(text=f"Pot รวม {bet*2:,} 🪙  •  House cut 5%  •  ผู้ชนะได้ {int(bet*2*0.95):,} 🪙")
        view = DuelChallengeView(target.id)
        duel_msg = await channel.send(embed=em, view=view)
        await view.wait()

        if view.accepted is None:
            em.description = "⏰ หมดเวลา — ไม่มีการตอบรับ"; em.color = discord.Color.dark_gray()
            await duel_msg.edit(embed=em, view=None); return
        if not view.accepted:
            em.description = f"❌ {target.display_name} ปฏิเสธคำท้า"; em.color = discord.Color.red()
            await duel_msg.edit(embed=em, view=None); return

        t_doc2 = await _ref(target.id).get()
        if int((t_doc2.to_dict() or {}).get("wallet", 0)) < bet:
            em.description = f"❌ {target.display_name} เงินไม่พอแล้ว 💸"; em.color = discord.Color.red()
            await duel_msg.edit(embed=em, view=None); return

        if await charge_wallet(challenger, bet) is None:
            await _err("เงินผู้ท้าไม่พอ 💸"); return
        if await charge_wallet(target, bet) is None:
            await update_bank(challenger, bet)
            await _err("เงินผู้ถูกท้าไม่พอ 💸"); return

        pot = bet * 2
        house_cut = int(pot * _HOUSE_CUT)
        await house_receive(house_cut)
        net_pot = pot - house_cut

        if game == "flip":
            await self._flip(duel_msg, challenger, target, bet, net_pot)
        elif game == "slot":
            await self._slot(duel_msg, challenger, target, bet, net_pot)
        elif game == "bj":
            await self._bj(duel_msg, challenger, target, bet, net_pot)

    @commands.command(aliases=["ท้าดวล", "pvp"])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def duel(self, ctx, member: discord.Member = None, game: str = None, amount: str = None):
        if not member or member.bot or member == ctx.author:
            await ctx.send("ระบุ @user ที่จะท้า (ห้ามท้าตัวเองหรือบอท)\n`$duel @user <flip|slot|bj> <amount>`")
            return
        game = (game or "").lower()
        if game not in GAME_LABELS:
            await ctx.send("เลือกเกม: `flip` | `slot` | `bj`\nเช่น `$duel @user bj 5000`")
            return
        bet = parse_positive_int(amount)
        if not bet:
            await ctx.send("ใส่จำนวนเดิมพันด้วย เช่น `$duel @user slot 1000`")
            return
        await self.run_duel(ctx.channel, ctx.author, member, game, bet)

    # ── Flip ──────────────────────────────────────────────────────────────────

    async def _flip(self, msg: discord.Message, ca: discord.Member, ta: discord.Member, bet: int, net_pot: int):
        side_icon = lambda s: "👑 หัว" if s == "1" else "🦅 ก้อย"

        em = discord.Embed(title="🪙 หัว/ก้อย DUEL", description=f"{ca.mention} เลือกหัวหรือก้อยก่อน", color=discord.Color.blurple())
        sv = _SideView(ca.id)
        await msg.edit(embed=em, view=sv)
        await sv.wait()
        c_side = sv.side or "1"
        t_side = "2" if c_side == "1" else "1"

        em.description = (
            f"**{ca.display_name}** → {side_icon(c_side)}\n"
            f"**{ta.display_name}** → {side_icon(t_side)}\n\n"
            "🌀 โยนเหรียญ..."
        )
        await msg.edit(embed=em, view=None)
        await asyncio.sleep(1.8)

        lk_c, lk_t = await asyncio.gather(get_effective_luck(ca.id), get_effective_luck(ta.id))
        win_c = max(0.08, min(0.92, lk_c / (lk_c + lk_t)))
        c_wins = random.random() < win_c
        winner, loser = (ca, ta) if c_wins else (ta, ca)

        await update_bank(winner, net_pot)
        net_c = net_pot - bet if c_wins else -bet
        net_t = net_pot - bet if not c_wins else -bet
        asyncio.create_task(log_history(ca.id, {"cmd": "duel_flip", "bet": bet, "vs": ta.id, "net": net_c}))
        asyncio.create_task(log_history(ta.id, {"cmd": "duel_flip", "bet": bet, "vs": ca.id, "net": net_t}))

        coin_out = side_icon(c_side if c_wins else t_side)
        em = discord.Embed(title=f"🪙 {coin_out} ออก!", color=discord.Color.green() if c_wins else discord.Color.red())
        em.add_field(name=f"{'👑 ' if c_wins else ''}{ca.display_name}", value=f"{side_icon(c_side)}\n`{'✅ +' if c_wins else '❌ -'}{abs(net_c):,} 🪙`", inline=True)
        em.add_field(name="VS", value="​", inline=True)
        em.add_field(name=f"{'👑 ' if not c_wins else ''}{ta.display_name}", value=f"{side_icon(t_side)}\n`{'✅ +' if not c_wins else '❌ -'}{abs(net_t):,} 🪙`", inline=True)
        em.add_field(name="📊 โอกาสชนะ (Luck)", value=f"{ca.display_name} **{win_c*100:.1f}%** vs {ta.display_name} **{(1-win_c)*100:.1f}%**", inline=False)
        em.set_footer(text=f"🍀 Luck: {ca.display_name}={lk_c:.2f}x  {ta.display_name}={lk_t:.2f}x")
        await msg.edit(embed=em)

    # ── Slot ──────────────────────────────────────────────────────────────────

    async def _slot(self, msg: discord.Message, ca: discord.Member, ta: discord.Member, bet: int, net_pot: int):
        em = discord.Embed(title="🎰 สล็อต DUEL", description="🌀 ทั้งคู่กำลังหมุน...", color=discord.Color.blurple())
        await msg.edit(embed=em, view=None)

        (syms_c, sc_c), (syms_t, sc_t) = await asyncio.gather(_slot_spin(ca.id), _slot_spin(ta.id))

        if sc_c > sc_t:
            winner, net_c, net_t = ca, net_pot - bet, -bet
        elif sc_t > sc_c:
            winner, net_c, net_t = ta, -bet, net_pot - bet
        else:
            winner = None
            refund = await _refund_tie(ca, ta, net_pot)
            net_c = net_t = refund - bet

        if winner:
            await update_bank(winner, net_pot)

        asyncio.create_task(log_history(ca.id, {"cmd": "duel_slot", "bet": bet, "vs": ta.id, "net": net_c}))
        asyncio.create_task(log_history(ta.id, {"cmd": "duel_slot", "bet": bet, "vs": ca.id, "net": net_t}))

        color = discord.Color.gold() if sc_c != sc_t else discord.Color.dark_gray()
        em = discord.Embed(title="🎰 สล็อต DUEL — ผลลัพธ์", color=color)
        em.add_field(
            name=f"{'👑 ' if winner == ca else ''}{ca.display_name}",
            value=f"{'  '.join(syms_c)}\n{_score_label(sc_c)}\n`{'✅ +' if net_c >= 0 else '❌ '}{net_c:,} 🪙`",
            inline=True,
        )
        em.add_field(name="VS", value="​", inline=True)
        em.add_field(
            name=f"{'👑 ' if winner == ta else ''}{ta.display_name}",
            value=f"{'  '.join(syms_t)}\n{_score_label(sc_t)}\n`{'✅ +' if net_t >= 0 else '❌ '}{net_t:,} 🪙`",
            inline=True,
        )
        if not winner:
            em.add_field(name="🤝 เสมอ!", value=f"คืน **{refund:,}** 🪙 ให้ทั้งคู่", inline=False)
        await msg.edit(embed=em)

    # ── BJ ────────────────────────────────────────────────────────────────────

    async def _bj(self, msg: discord.Message, ca: discord.Member, ta: discord.Member, bet: int, net_pot: int):
        lk_c, lk_t = await asyncio.gather(get_effective_luck(ca.id), get_effective_luck(ta.id))

        dealer = [_bj_draw(), _bj_draw()]
        hand_c = [_lucky_card(lk_c), _lucky_card(lk_c)]
        hand_t = [_lucky_card(lk_t), _lucky_card(lk_t)]

        def _em(phase: str = ""):
            e = discord.Embed(title="🃏 แบล็คแจ็ค DUEL", color=discord.Color.blurple())
            d_tot = _bj_total(dealer)
            e.add_field(name=f"🏠 Dealer [{_bj_total([dealer[0]])}?]", value=_bj_str(dealer, hide_second=True), inline=False)
            e.add_field(name=f"👤 {ca.display_name} = {_bj_total(hand_c)}", value=_bj_str(hand_c), inline=True)
            e.add_field(name=f"👤 {ta.display_name} = {_bj_total(hand_t)}", value=_bj_str(hand_t), inline=True)
            if phase: e.add_field(name="​", value=phase, inline=False)
            return e

        def _em_final():
            e = discord.Embed(title="🃏 แบล็คแจ็ค DUEL — ผลลัพธ์", color=discord.Color.gold())
            e.add_field(name=f"🏠 Dealer = {_bj_total(dealer)}", value=_bj_str(dealer, hide_second=False), inline=False)
            return e

        await msg.edit(embed=_em(), view=None)

        # Play turns sequentially
        for player, hand, lk in [(ca, hand_c, lk_c), (ta, hand_t, lk_t)]:
            while _bj_total(hand) < 21:
                bv = _BJView(player.id)
                await msg.edit(embed=_em(f"⏳ **{player.display_name}** เทิร์นของแก — Hit หรือ Stand?"), view=bv)
                timed_out = await bv.wait()
                if timed_out or bv.action == "stand":
                    break
                hand.append(_lucky_card(lk))

        await msg.edit(embed=_em("🏠 Dealer กำลังหยิบไพ่..."), view=None)
        while _bj_total(dealer) < 17:
            dealer.append(_bj_draw())
            await asyncio.sleep(0.7)

        p_c, p_t, p_d = _bj_total(hand_c), _bj_total(hand_t), _bj_total(dealer)

        def _vs_dealer(p: int) -> int:
            if p > 21: return -1
            if p_d > 21 or p > p_d: return 1
            if p == p_d: return 0
            return -1

        sc_c, sc_t = _vs_dealer(p_c), _vs_dealer(p_t)

        if sc_c != sc_t:
            winner = ca if sc_c > sc_t else ta
        elif p_c > 21 and p_t > 21:
            winner = None
        elif p_c > p_t:
            winner = ca
        elif p_t > p_c:
            winner = ta
        else:
            winner = None

        if winner:
            await update_bank(winner, net_pot)
            net_c = net_pot - bet if winner == ca else -bet
            net_t = net_pot - bet if winner == ta else -bet
        else:
            refund = await _refund_tie(ca, ta, net_pot)
            net_c = net_t = refund - bet

        asyncio.create_task(log_history(ca.id, {"cmd": "duel_bj", "bet": bet, "vs": ta.id, "net": net_c}))
        asyncio.create_task(log_history(ta.id, {"cmd": "duel_bj", "bet": bet, "vs": ca.id, "net": net_t}))

        def _rlabel(p: int, sc: int) -> str:
            if p > 21: return "💥 Bust"
            if sc == 1: return f"✅ ชนะ Dealer ({p})"
            if sc == 0: return f"🤝 Push ({p})"
            return f"❌ แพ้ Dealer ({p})"

        em = _em_final()
        em.color = discord.Color.gold() if winner else discord.Color.dark_gray()
        em.add_field(
            name=f"{'👑 ' if winner == ca else ''}{ca.display_name}",
            value=f"{_bj_str(hand_c)}\n{_rlabel(p_c, sc_c)}\n`{'✅ +' if net_c >= 0 else '❌ '}{net_c:,} 🪙`",
            inline=True,
        )
        em.add_field(name="VS", value="​", inline=True)
        em.add_field(
            name=f"{'👑 ' if winner == ta else ''}{ta.display_name}",
            value=f"{_bj_str(hand_t)}\n{_rlabel(p_t, sc_t)}\n`{'✅ +' if net_t >= 0 else '❌ '}{net_t:,} 🪙`",
            inline=True,
        )
        if winner:
            em.add_field(name="🏆 ผู้ชนะ", value=f"{winner.mention} +{net_pot - bet:,} 🪙", inline=False)
        else:
            em.add_field(name="🤝 เสมอ!", value=f"คืน {refund:,} 🪙 ให้ทั้งคู่", inline=False)
        em.set_footer(text=f"🍀 Luck: {ca.display_name}={lk_c:.2f}x  {ta.display_name}={lk_t:.2f}x")
        await msg.edit(embed=em, view=None)


async def setup(bot):
    await bot.add_cog(DuelCog(bot))
