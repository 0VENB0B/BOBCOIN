import asyncio
import logging
import random
import time

import discord
from discord.ext import commands, tasks

logger = logging.getLogger("bobcoin.economy")

from ..ai import BOB_SYSTEM, call_ai


class NotRegistered(Exception):
    pass


# ── Blackjack helpers ────────────────────────────────────────────────────────

def _bj_draw() -> int:
    v = random.randint(1, 13)
    return 11 if v == 1 else min(v, 10)


def _lucky_card(lk: float) -> int:
    v = random.randint(1, 13)
    if lk > 1 and v <= 5 and random.random() < min((lk - 1) * 0.12, 0.45):
        v = random.randint(6, 13)
    elif lk < 1 and v >= 9 and random.random() < min((1 - lk) * 0.12, 0.45):
        v = random.randint(1, 8)
    return 11 if v == 1 else min(v, 10)


def _bj_total(hand: list[int]) -> int:
    total, aces = sum(hand), hand.count(11)
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total


def _bj_str(hand: list[int], hide_second: bool = False) -> str:
    _F = {11: "A", 10: "10", 9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}
    cards = [f"[{_F[c]}]" for c in hand]
    if hide_second and len(cards) >= 2:
        cards[1] = "[?]"
    return "  ".join(cards)


class _BJView(discord.ui.View):
    def __init__(self, player_id: int):
        super().__init__(timeout=30)
        self.player_id = player_id
        self.action: str | None = None

    @discord.ui.button(label="Hit 🃏", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
            return
        self.action = "hit"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Stand ✋", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
            return
        self.action = "stand"
        self.stop()
        await interaction.response.defer()


# ── Rob cooldown tracker (resets on restart — intentional) ──────────────────
_rob_cooldowns: dict[tuple[int, int], float] = {}


class _PanelCtx:
    """Duck-type substitute for commands.Context — used by panel buttons."""
    __slots__ = ("author", "_ch")

    def __init__(self, channel, user):
        self.author = user
        self._ch = channel

    async def send(self, *a, **kw):
        return await self._ch.send(*a, **kw)


from ..bank import (
    ACHIEVEMENTS,
    accrue_loan_interest,
    add_xp,
    ai_loan_limit,
    calc_interest,
    calc_loan_limit,
    charge_wallet,
    contribute_jackpot,
    get_achievements,
    get_balance,
    get_bank_data,
    get_history,
    get_house_balance,
    get_house_data,
    get_jackpot_pool,
    get_total_outstanding_loans,
    get_loan_info,
    get_effective_luck,
    get_user_luck,
    house_can_pay_games,
    grant_achievement,
    repay_loan,
    set_user_luck,
    house_payout,
    house_receive,
    is_registered,
    log_history,
    open_account,
    pay_interest_all,
    take_loan,
    transfer_to_user,
    trigger_jackpot,
    try_daily,
    update_bank,
    user_deposit,
    user_withdraw,
    xp_to_level,
)
from ..helpers import is_bot_admin, parse_amount_or_reply, parse_positive_int
from ..settings import SLOT_SYMBOLS

_SLOT_SYSTEM  = BOB_SYSTEM + " ตอนนี้กำลังแสดงผลสล็อตให้ user ดู พูดแบบ BOB ตอบสนองต่อผลที่ออกมา"
_FLIP_SYSTEM  = BOB_SYSTEM + " ตอนนี้กำลังทอยเหรียญให้ user ดู พูดแบบ BOB ตอบสนองต่อ user ตามสถานการณ์"
_LOTTERY_SYSTEM = BOB_SYSTEM + " ตอนนี้กำลังออกผลหวยให้ user ดู พูดแบบ BOB ทำนายหรือ react ตามสถานการณ์"


async def _get_game_streak(user_id: int) -> tuple[int, bool]:
    """Return (streak_length, is_win_streak) from recent decisive game history."""
    entries = await get_history(user_id, limit=15)
    decisive = [e for e in entries if e.get("cmd") in ("slot", "flip", "lottery") and e.get("net", 0) != 0]
    if not decisive:
        return 0, False
    is_win = decisive[0]["net"] > 0
    n = next((i for i, e in enumerate(decisive) if (e["net"] > 0) != is_win), len(decisive))
    return n, is_win


def _streak_effects(streak: int, is_win: bool, bet: int) -> tuple[float, int]:
    """Return (win_bonus_pct, mercy_amount). Applied to payout after game."""
    if is_win and streak >= 3:
        return min((streak - 2) * 0.05, 0.25), 0  # +5% per win beyond 2, cap 25%
    if not is_win and streak >= 5:
        return 0.0, int(bet * 0.03)  # 3% mercy refund
    return 0.0, 0


async def _house_closed_embed(ctx) -> bool:
    """Send closed embed and return True if house can't pay games."""
    if await house_can_pay_games():
        return False
    em = discord.Embed(
        title="🔴 คลังหลวงแห้งชั่วคราว",
        description="เงินรางวัลไม่พอจ่าย ขอหยุดรับเดิมพันก่อนนะ\nรอให้คลังฟื้นแล้วค่อยมาใหม่ 🏛️",
        color=discord.Color.red(),
    )
    em.set_footer(text="$house เพื่อดูสถานะคลัง")
    await ctx.send(embed=em)
    return True


async def _post_game(ctx, bet: int, won: bool, streak: int, streak_is_win: bool, ach_keys: list[str]) -> None:
    """Fire-and-forget: XP, achievements, level-up notification."""
    xp_gain = max(bet // 1_000, 1)
    _, new_level, leveled_up = await add_xp(ctx.author.id, xp_gain)
    if leveled_up:
        asyncio.create_task(ctx.send(
            f"⬆️ **Level Up!** {ctx.author.mention} → **Level {new_level}** 🎉",
            delete_after=10,
        ))
        if new_level >= 10:
            ach_keys.append("level_10")
    for key in ach_keys:
        newly = await grant_achievement(ctx.author.id, key)
        if newly and key in ACHIEVEMENTS:
            icon, name, desc = ACHIEVEMENTS[key]
            asyncio.create_task(ctx.send(
                f"🏆 **Achievement Unlocked!** {icon} **{name}** — {desc}",
                delete_after=12,
            ))


# ── Standalone game runners (called by both prefix commands and panel buttons) ─

async def _run_slot(ctx, amount: int) -> None:
    if await _house_closed_embed(ctx):
        return
    if await charge_wallet(ctx.author, amount) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return

    await house_receive(amount)
    jackpot_contrib = max(amount // 100, 1)
    jackpot_pool_task = asyncio.create_task(contribute_jackpot(jackpot_contrib))

    _BASE_JP = 8 / 512
    user_luck = await get_effective_luck(ctx.author.id)
    if random.random() < min(_BASE_JP * user_luck, 0.99):
        s = random.choice(SLOT_SYMBOLS)
        final = [s, s, s]
    else:
        final = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        if user_luck == 0.0 and final[0] == final[1] == final[2]:
            final[2] = random.choice([sym for sym in SLOT_SYMBOLS if sym != final[0]])

    is_jackpot = final[0] == final[1] == final[2]
    is_two_match = (not is_jackpot) and (
        final[0] == final[1] or final[0] == final[2] or final[1] == final[2]
    )

    # ponytail: multiplier tier by symbol rarity
    if is_jackpot and final[0] == "💀":
        multiplier, label = 20, "☠️ DEATH JACKPOT!!"
    elif is_jackpot and final[0] in ("💎", "7️⃣"):
        multiplier, label = 15, "💎 MEGA JACKPOT!!"
    elif is_jackpot:
        multiplier, label = 8, "🏆 JACKPOT!!"
    elif is_two_match:
        multiplier, label = 0, "😤 เกือบแล้ว..."
    else:
        multiplier, label = -1, "💀 แพ้"

    if is_jackpot:
        outcome = f"{label} {''.join(final)} ได้ {multiplier}x"
    elif is_two_match:
        outcome = f"เกือบได้!! {''.join(final)} แต่ได้คืนทุน"
    else:
        outcome = f"แพ้ยับ {''.join(final)} เสียเงินหมดเลย"

    commentary_task = asyncio.create_task(call_ai(_SLOT_SYSTEM, [{"role": "user", "content": outcome}], fallback="", max_tokens=80))
    streak_task = asyncio.create_task(_get_game_streak(ctx.author.id))

    _SPIN = "🌀"
    jackpot_display = await jackpot_pool_task

    def _spin_embed(reels, footer="กำลังหมุน..."):
        display = "  ╎  ".join(reels)
        em = discord.Embed(description=f"# {display}", color=discord.Color.blurple())
        em.set_author(name="🎰  S L O T  M A C H I N E", icon_url=ctx.author.display_avatar.url)
        em.set_footer(text=f"💰 เดิมพัน {amount:,}  •  💎 Jackpot Pool: {jackpot_display:,} 🪙  •  {footer}")
        return em

    msg = await ctx.send(embed=_spin_embed([_SPIN, _SPIN, _SPIN]))
    for _ in range(4):
        spins = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        await msg.edit(embed=_spin_embed(spins))
        await asyncio.sleep(0.22)

    await asyncio.sleep(0.1)
    for _ in range(3):
        spins = [random.choice(SLOT_SYMBOLS) for _ in range(2)]
        await msg.edit(embed=_spin_embed([final[0], spins[0], spins[1]], f"🔒 {final[0]}  ╎  🌀  ╎  🌀"))
        await asyncio.sleep(0.22)

    await asyncio.sleep(0.1)
    for _ in range(3):
        spin = random.choice(SLOT_SYMBOLS)
        await msg.edit(embed=_spin_embed([final[0], final[1], spin], f"🔒 {final[0]}  ╎  🔒 {final[1]}  ╎  🌀"))
        await asyncio.sleep(0.22)

    await asyncio.sleep(0.15)
    commentary = await commentary_task
    streak, streak_is_win = await streak_task

    if is_jackpot and multiplier == 20:
        color = discord.Color.red()
    elif is_jackpot and multiplier == 15:
        color = discord.Color.gold()
    elif is_jackpot:
        color = discord.Color.green()
    elif is_two_match:
        color = discord.Color.orange()
    else:
        color = discord.Color.dark_red()

    result_display = "  ╎  ".join(final)
    em = discord.Embed(description=f"# {result_display}", color=color)
    em.set_author(name="🎰  S L O T  M A C H I N E", icon_url=ctx.author.display_avatar.url)

    symbols_str = " ".join(final)
    bonus_pct, mercy = _streak_effects(streak, streak_is_win, amount)
    ach_keys = []
    if amount >= 1_000_000:
        ach_keys.append("high_roller")

    if is_jackpot:
        full_payout = amount * (multiplier + 1)
        streak_bonus = int(full_payout * bonus_pct)
        prog_jackpot = 0
        if multiplier == 20:
            prog_jackpot = await trigger_jackpot()
            ach_keys.append("death")
        else:
            ach_keys.append("jackpot")
        actual = await house_payout(full_payout + streak_bonus) + prog_jackpot
        await update_bank(ctx.author, actual)
        net = actual - amount
        em.add_field(name=label, value=f"**+{net:,}** 🪙", inline=True)
        em.add_field(name="Multiplier", value=f"**{multiplier}x**", inline=True)
        if prog_jackpot > 0:
            em.add_field(name="💎 Progressive Jackpot!!", value=f"+**{prog_jackpot:,}** 🪙", inline=False)
        if streak_bonus > 0:
            em.add_field(name=f"🔥 {streak}x Win Streak!", value=f"+{int(bonus_pct*100)}% bonus (+{streak_bonus:,} 🪙)", inline=False)
        if commentary:
            em.add_field(name="​", value=f"*{commentary}*", inline=False)
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": label, "multiplier": multiplier, "net": net}))
        ach_keys.append("first_win")
        if streak_is_win and streak >= 4:
            ach_keys.append("streak_5")
    elif is_two_match:
        actual = await house_payout(amount)
        await update_bank(ctx.author, actual)
        em.add_field(name=label, value=f"คืนทุน **{actual:,}** 🪙", inline=True)
        if commentary:
            em.add_field(name="​", value=f"*{commentary}*", inline=False)
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": "near", "multiplier": 0, "net": actual - amount}))
    else:
        net = -amount
        em.add_field(name=label, value=f"**-{amount:,}** 🪙", inline=True)
        if mercy > 0:
            mercy_actual = await house_payout(mercy)
            await update_bank(ctx.author, mercy_actual)
            net += mercy_actual
            em.add_field(name=f"💀 {streak}x Cold Streak — Mercy", value=f"+{mercy_actual:,} 🪙 (3% คืน)", inline=False)
        if commentary:
            em.add_field(name="​", value=f"*{commentary}*", inline=False)
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": "lose", "multiplier": 0, "net": net}))

    em.set_footer(text=f"{ctx.author.display_name}  •  เดิมพัน {amount:,} เหรียญ")
    await msg.edit(embed=em)
    asyncio.create_task(_post_game(ctx, amount, is_jackpot, streak, streak_is_win, ach_keys))


async def _run_flip(ctx, amount: int, side: str) -> None:
    """side: '1' = หัว, '2' = ก้อย"""
    if await _house_closed_embed(ctx):
        return
    if await charge_wallet(ctx.author, amount) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return

    await house_receive(amount)

    choice_name = "หัว" if side == "1" else "ก้อย"
    choice_icon = "👑" if side == "1" else "🦅"
    user_luck = await get_effective_luck(ctx.author.id)
    win_chance = min(0.5 * (user_luck ** 0.2), 0.72)
    win = random.random() < win_chance
    bot_pick = int(side) if win else (3 - int(side))
    drawn_name = "หัว" if bot_pick == 1 else "ก้อย"
    drawn_icon = "👑" if bot_pick == 1 else "🦅"
    payout = int(amount * 1.8) if win else 0

    taunt_task = asyncio.create_task(call_ai(
        _FLIP_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือก{choice_name} เดิมพัน {amount:,} เหรียญ ยั่วก่อนโยนหน่อย"}],
        fallback="โยนเหรียญแล้ว... ไม่รู้จะออกอะไร",
        max_tokens=60,
    ))
    streak_task = asyncio.create_task(_get_game_streak(ctx.author.id))
    react_task = asyncio.create_task(call_ai(
        _FLIP_SYSTEM,
        [{"role": "user", "content": f"ออก{drawn_name} {ctx.author.name}{'ชนะ' if win else 'แพ้'} {amount:,} เหรียญ react สั้นๆ แซวให้หัวร้อน"}],
        fallback="ดีใจด้วย! 🎉" if win else "โชคร้ายจริงๆ 💸",
        max_tokens=60,
    ))

    _SPIN_FRAMES = ["🌀", "💫", "✨", "🪙", "💫", "🌀", "✨", "🪙"]

    def _coin_embed(frame, taunt_text=None):
        em = discord.Embed(description=f"# {frame}", color=discord.Color.blurple())
        em.set_author(name="🪙  C O I N  F L I P", icon_url=ctx.author.display_avatar.url)
        if taunt_text:
            em.add_field(name="​", value=f"*{taunt_text}*", inline=False)
        em.set_footer(text=f"เลือก: {choice_name} {choice_icon}  •  เดิมพัน {amount:,} 🪙")
        return em

    msg = await ctx.send(embed=_coin_embed(_SPIN_FRAMES[0]))
    for frame in _SPIN_FRAMES[1:5]:
        await msg.edit(embed=_coin_embed(frame))
        await asyncio.sleep(0.18)

    taunt = await taunt_task
    for frame in _SPIN_FRAMES[5:]:
        await msg.edit(embed=_coin_embed(frame, taunt))
        await asyncio.sleep(0.38)

    reaction = await react_task
    streak, streak_is_win = await streak_task
    bonus_pct, mercy = _streak_effects(streak, streak_is_win, amount)
    color = discord.Color.green() if win else discord.Color.red()
    em = discord.Embed(description=f"# {drawn_icon}", color=color)
    em.set_author(name="🪙  C O I N  F L I P", icon_url=ctx.author.display_avatar.url)

    if win:
        streak_bonus = int(payout * bonus_pct)
        actual = await house_payout(payout + streak_bonus)
        await update_bank(ctx.author, actual)
        net = actual - amount
        em.add_field(name=f"✅ {drawn_name} — ชนะ!", value=f"**+{net:,}** 🪙", inline=True)
        em.add_field(name="Payout", value="**1.8x**", inline=True)
        if streak_bonus > 0:
            em.add_field(name=f"🔥 {streak}x Win Streak!", value=f"+{int(bonus_pct*100)}% bonus (+{streak_bonus:,} 🪙)", inline=False)
        if actual < payout + streak_bonus:
            em.add_field(name="⚠️ คลังหลวงแห้ง!", value=f"จ่ายได้แค่ **{actual:,}** 🪙", inline=False)
    else:
        net = -amount
        em.add_field(name=f"❌ {drawn_name} — แพ้!", value=f"**-{amount:,}** 🪙", inline=True)
        if mercy > 0:
            mercy_actual = await house_payout(mercy)
            await update_bank(ctx.author, mercy_actual)
            net += mercy_actual
            em.add_field(name=f"💀 {streak}x Cold Streak — Mercy", value=f"+{mercy_actual:,} 🪙 (3% คืน)", inline=False)

    if reaction:
        em.add_field(name="​", value=f"*{reaction}*", inline=False)
    em.set_footer(text=f"{ctx.author.display_name}  •  เลือก {choice_name} {choice_icon}  •  เดิมพัน {amount:,} 🪙")
    await msg.edit(embed=em)

    asyncio.create_task(log_history(ctx.author.id, {"cmd": "flip", "bet": amount, "choice": choice_name, "drawn": drawn_name, "win": win, "net": net}))
    flip_ach = []
    if amount >= 1_000_000:
        flip_ach.append("high_roller")
    if win:
        flip_ach.append("first_win")
        if streak_is_win and streak >= 4:
            flip_ach.append("streak_5")
    asyncio.create_task(_post_game(ctx, amount, win, streak, streak_is_win, flip_ach))


async def _run_lottery(ctx, ticket_cost: int, text: str) -> None:
    """text: 5-digit ticket number string"""
    if await _house_closed_embed(ctx):
        return
    if await charge_wallet(ctx.author, ticket_cost) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return

    await house_receive(ticket_cost)

    user_luck = await get_effective_luck(ctx.author.id)
    player_num = int(text)
    bot_number = random.randrange(10000, 100000)
    if user_luck > 1:
        r = random.random()
        if r < min((user_luck - 1) * 0.004, 0.08):
            bot_number = player_num
        elif r < min((user_luck - 1) * 0.04, 0.25):
            bot_number = random.randint(1, 9) * 10000 + (player_num % 10000)
        elif r < min((user_luck - 1) * 0.10, 0.40):
            bot_number = random.randint(10, 99) * 1000 + (player_num % 1000)
    drawn_str = str(bot_number)
    player = player_num

    if player == bot_number:
        match, win, multiplier = "5ตัว", ticket_cost * 50, 50
    elif player % 10000 == bot_number % 10000:
        match, win, multiplier = "4ตัวท้าย", ticket_cost * 8, 8
    elif player % 1000 == bot_number % 1000:
        match, win, multiplier = "3ตัวท้าย", ticket_cost * 3, 3
    else:
        match, win, multiplier = "ไม่ถูก", 0, 0

    prophecy_task = asyncio.create_task(call_ai(
        _LOTTERY_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือกเลข {text} เดิมพัน {ticket_cost:,} เหรียญ ทำนายโชคมั่วๆ หน่อย"}],
        fallback="ดวงดาวกำลังจะบอกอะไรบางอย่าง...",
        max_tokens=80,
    ))
    streak_task = asyncio.create_task(_get_game_streak(ctx.author.id))
    reaction_task = asyncio.create_task(call_ai(
        _LOTTERY_SYSTEM,
        [{"role": "user", "content": f"เลขออก {drawn_str} {ctx.author.name} {'ถูก' + match if win else 'ไม่ถูกเลย'} เดิมพัน {ticket_cost:,} เหรียญ react สั้นๆ กวนๆ"}],
        fallback="ชีวิตคือความไม่แน่นอน" if not win else "สุลต่านชั่วข้ามคืน!!",
        max_tokens=80,
    ))

    def _lottery_embed(digits, prophecy=None, color=discord.Color.blurple(), title="🎟️  L O T T E R Y"):
        if match != "ไม่ถูก" and digits == drawn_str:
            tail = {"5ตัว": 5, "4ตัวท้าย": 4, "3ตัวท้าย": 3}[match]
            styled = f"`{digits[:-tail]}`**`{digits[-tail:]}`**" if tail < 5 else f"**`{digits}`**"
        else:
            styled = f"`{digits}`"
        em = discord.Embed(title=title, description=f"# {styled}", color=color)
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        if prophecy:
            em.add_field(name="🔮 คำทำนาย", value=f"*{prophecy}*", inline=False)
        em.set_footer(text=f"เลขที่เลือก: {text}  •  เดิมพัน {ticket_cost:,} 🪙")
        return em

    _SPIN_CHARS = "0123456789"
    msg = await ctx.send(embed=_lottery_embed("?" * 5))
    await asyncio.sleep(0.3)

    revealed = []
    for i, digit in enumerate(drawn_str):
        for _ in range(5):
            preview = "".join(revealed) + random.choice(_SPIN_CHARS) + "?" * (4 - i)
            em = discord.Embed(title="🎟️  L O T T E R Y", description=f"# `{preview}`", color=discord.Color.blurple())
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            em.set_footer(text=f"เลขที่เลือก: {text}  •  เดิมพัน {ticket_cost:,} 🪙")
            await msg.edit(embed=em)
            await asyncio.sleep(0.12)
        revealed.append(digit)

    prophecy = await prophecy_task
    await msg.edit(embed=_lottery_embed(drawn_str, prophecy))
    await asyncio.sleep(1.2)

    reaction = await reaction_task
    streak, streak_is_win = await streak_task
    bonus_pct, mercy = _streak_effects(streak, streak_is_win, ticket_cost)

    if win:
        streak_bonus = int(win * bonus_pct)
        actual = await house_payout(win + streak_bonus)
        await update_bank(ctx.author, actual)
        net = actual - ticket_cost
        tier_labels = {"5ตัว": ("🏆", "ถูก 5 ตัว!!", discord.Color.gold()), "4ตัวท้าย": ("🥈", "ถูก 4 ตัวท้าย!", discord.Color.green()), "3ตัวท้าย": ("🥉", "ถูก 3 ตัวท้าย", discord.Color.teal())}
        tier_icon, tier_label, color = tier_labels[match]
        em = discord.Embed(title=f"{tier_icon} {tier_label}", color=color)
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="เลขที่ออก", value=f"`{drawn_str}`", inline=True)
        em.add_field(name="เลขที่เลือก", value=f"`{text}`", inline=True)
        em.add_field(name="รางวัล", value=f"**+{net:,}** 🪙  ({multiplier}x)", inline=False)
        if streak_bonus > 0:
            em.add_field(name=f"🔥 {streak}x Win Streak!", value=f"+{int(bonus_pct*100)}% bonus (+{streak_bonus:,} 🪙)", inline=False)
        if actual < win + streak_bonus:
            em.add_field(name="⚠️ คลังหลวงแห้ง!", value=f"จ่ายได้แค่ **{actual:,}** 🪙", inline=False)
        if reaction:
            em.add_field(name="​", value=f"*{reaction}*", inline=False)
    else:
        net = -ticket_cost
        em = discord.Embed(title="💸 ไม่ถูกสักตัว", color=discord.Color.dark_red())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="เลขที่ออก", value=f"`{drawn_str}`", inline=True)
        em.add_field(name="เลขที่เลือก", value=f"`{text}`", inline=True)
        em.add_field(name="เสียไป", value=f"**-{ticket_cost:,}** 🪙", inline=False)
        if mercy > 0:
            mercy_actual = await house_payout(mercy)
            await update_bank(ctx.author, mercy_actual)
            net += mercy_actual
            em.add_field(name=f"💀 {streak}x Cold Streak — Mercy", value=f"+{mercy_actual:,} 🪙 (3% คืน)", inline=False)
        if reaction:
            em.add_field(name="​", value=f"*{reaction}*", inline=False)

    em.set_footer(text=f"เดิมพัน {ticket_cost:,} 🪙")
    await msg.edit(embed=em)
    asyncio.create_task(log_history(ctx.author.id, {"cmd": "lottery", "bet": ticket_cost, "pick": text, "drawn": drawn_str, "match": match, "net": net}))
    lot_ach = []
    if ticket_cost >= 1_000_000:
        lot_ach.append("high_roller")
    if win:
        lot_ach.append("first_win")
        if match == "5ตัว":
            lot_ach.append("lottery_5")
        if streak_is_win and streak >= 4:
            lot_ach.append("streak_5")
    asyncio.create_task(_post_game(ctx, ticket_cost, bool(win), streak, streak_is_win, lot_ach))


async def _run_bj(ctx, amount: int) -> None:
    if await _house_closed_embed(ctx):
        return
    if await charge_wallet(ctx.author, amount) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return
    await house_receive(amount)

    user_luck = await get_effective_luck(ctx.author.id)
    player = [_lucky_card(user_luck), _lucky_card(user_luck)]
    dealer = [_bj_draw(), _bj_draw()]

    def _bj_embed(p_hand, d_hand, hide_dealer=True, status=None, color=discord.Color.blurple()):
        p_tot = _bj_total(p_hand)
        d_visible = _bj_total([d_hand[0]]) if hide_dealer else _bj_total(d_hand)
        em = discord.Embed(title="🃏  B L A C K J A C K", color=color)
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        d_label = f"🏠 Dealer  {f'= {_bj_total(d_hand)}' if not hide_dealer else f'[{d_visible}?]'}"
        em.add_field(name=d_label, value=_bj_str(d_hand, hide_dealer), inline=False)
        em.add_field(name=f"👤 You  = {p_tot}", value=_bj_str(p_hand), inline=False)
        if status:
            em.add_field(name="​", value=status, inline=False)
        em.set_footer(text=f"เดิมพัน {amount:,} 🪙  •  Hit หรือ Stand")
        return em

    p_nat = _bj_total(player) == 21
    d_nat = _bj_total(dealer) == 21

    if p_nat or d_nat:
        if p_nat and not d_nat:
            payout = int(amount * 2.5)
            actual = await house_payout(payout)
            await update_bank(ctx.author, actual)
            net = actual - amount
            em = _bj_embed(player, dealer, False, f"🃏 **BLACKJACK!!** **+{net:,}** 🪙 (2.5x)", discord.Color.gold())
            ach = ["first_win"] + (["high_roller"] if amount >= 1_000_000 else [])
        elif d_nat and not p_nat:
            net = -amount
            em = _bj_embed(player, dealer, False, f"💀 Dealer Blackjack… **-{amount:,}** 🪙", discord.Color.red())
            ach = ["high_roller"] if amount >= 1_000_000 else []
        else:
            actual = await house_payout(amount)
            await update_bank(ctx.author, actual)
            net = 0
            em = _bj_embed(player, dealer, False, "🤝 **Push** — คืนทุน", discord.Color.orange())
            ach = []
        await ctx.send(embed=em)
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "bj", "bet": amount, "result": "blackjack", "net": net}))
        asyncio.create_task(_post_game(ctx, amount, net > 0, 0, False, ach))
        return

    msg = await ctx.send(embed=_bj_embed(player, dealer))

    while _bj_total(player) < 21:
        view = _BJView(ctx.author.id)
        await msg.edit(embed=_bj_embed(player, dealer), view=view)
        timed_out = await view.wait()
        if timed_out or view.action == "stand":
            break
        player.append(_lucky_card(user_luck))

    p_total = _bj_total(player)

    if p_total > 21:
        net = -amount
        em = _bj_embed(player, dealer, False, f"💥 **BUST!**  **-{amount:,}** 🪙", discord.Color.red())
        await msg.edit(embed=em, view=None)
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "bj", "bet": amount, "result": "bust", "net": net}))
        asyncio.create_task(_post_game(ctx, amount, False, 0, False, ["high_roller"] if amount >= 1_000_000 else []))
        return

    while _bj_total(dealer) < 17:
        dealer.append(_bj_draw())
        await msg.edit(embed=_bj_embed(player, dealer, False, "🏠 Dealer กำลังหยิบไพ่..."))
        await asyncio.sleep(0.6)

    d_total = _bj_total(dealer)

    if d_total > 21 or p_total > d_total:
        payout = int(amount * 1.8)
        actual = await house_payout(payout)
        await update_bank(ctx.author, actual)
        net = actual - amount
        bust_note = "💥 Dealer Bust!  " if d_total > 21 else ""
        em = _bj_embed(player, dealer, False, f"{bust_note}✅ **ชนะ!**  **+{net:,}** 🪙", discord.Color.green())
        ach = ["first_win"] + (["high_roller"] if amount >= 1_000_000 else [])
        result = "win"
    elif p_total == d_total:
        actual = await house_payout(amount)
        await update_bank(ctx.author, actual)
        net = 0
        em = _bj_embed(player, dealer, False, "🤝 **Push** — คืนทุน", discord.Color.orange())
        ach = ["high_roller"] if amount >= 1_000_000 else []
        result = "push"
    else:
        net = -amount
        em = _bj_embed(player, dealer, False, f"❌ **แพ้!**  **-{amount:,}** 🪙", discord.Color.red())
        ach = ["high_roller"] if amount >= 1_000_000 else []
        result = "lose"

    await msg.edit(embed=em, view=None)
    asyncio.create_task(log_history(ctx.author.id, {"cmd": "bj", "bet": amount, "p": p_total, "d": d_total, "result": result, "net": net}))
    asyncio.create_task(_post_game(ctx, amount, net > 0, 0, False, ach))


# ─────────────────────────────────────────────────────────────────────────────
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
        from ..bank import _ref
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
        from ..bank import _ref
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
        except asyncio.TimeoutError:
            await ctx.send("⏰ หมดเวลา ลองสมัครใหม่อีกทีนะ")
            return

        # step 2: คำอธิบายตัวเอง
        await ctx.send("**[2/2]** แนะนำตัวเองสั้นๆ หน่อย (หรือพิมพ์ `-` เพื่อข้าม)")
        try:
            msg2 = await self.bot.wait_for("message", check=check, timeout=60)
            bio = msg2.content.strip()[:100]
            if bio == "-":
                bio = ""
        except asyncio.TimeoutError:
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
        from ..bank import _ref
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
    @commands.check(is_bot_admin)
    async def TestJson(self, ctx):
        earning = random.randrange(101)
        await ctx.send(f"On my way son : God gave you {earning} coins!!")
        await update_bank(ctx.author, earning)

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
        amount = await parse_amount_or_reply(ctx, amount, "ใส่เงินที่พนันด้วยสิเฮ้ย!", "เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000,000")
        if amount is None:
            return
        await _run_slot(ctx, amount)

    @commands.command()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def withdraw(self, ctx, amount=None):
        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่จำนวนเงินที่จะถอนด้วยสิเฮ้ย!",
            "จำนวนเงินต้องเป็นตัวเลข 1 ถึง 1,000,000,000",
        )
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
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "withdraw", "amount": amount}))

    @commands.command(aliases=["lb"])
    async def leaderboard(self, ctx, x=3):
        x = parse_positive_int(x, max_value=10) or 3
        users = await get_bank_data()
        totals = sorted(
            (
                (int(a.get("wallet", 0)) + int(a.get("deposited", a.get("bank", 0))), int(uid))
                for uid, a in users.items()
            ),
            reverse=True,
        )
        em = discord.Embed(
            title=f"Top {x} จตุรเทพแห่งความมั่งคั่ง",
            color=discord.Color.purple(),
        )
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for index, (amt, id_) in enumerate(totals[:x], start=1):
            user = self.bot.get_user(id_) or await self.bot.fetch_user(id_)
            medal = medals.get(index, f"**#{index}**")
            uid_str = str(id_)
            lv = xp_to_level(int(users.get(uid_str, {}).get("xp", 0)))
            em.add_field(name=f"{medal} {user.display_name}", value=f"{amt:,} 🪙  •  ⭐ Lv.{lv}", inline=False)
        await ctx.send(embed=em)

    @commands.command()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def deposit(self, ctx, amount=None):
        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่จำนวนเงินที่จะฝากด้วยสิเฮ้ย!",
            "จำนวนเงินต้องเป็นตัวเลข 1 ถึง 1,000,000,000",
        )
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
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "deposit", "amount": amount}))

    @commands.command(aliases=["flipcoin", "filpcoin"])
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def flip(self, ctx, text=None, amount=None):
        if text is None or text not in {"1", "2"}:
            await ctx.send("กรุณาใส่เลขที่จะทาย\nหัว = 1\nก้อย = 2")
            return
        amount = await parse_amount_or_reply(ctx, amount, "ใส่เงินที่พนันด้วยสิเฮ้ย!", "เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000,000")
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

        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่จำนวนเงินที่จะโอนด้วย!",
            "จำนวนเงินต้องเป็นตัวเลข 1 ถึง 1,000,000,000",
        )
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
        asyncio.create_task(log_history(ctx.author.id, {"cmd": "give", "amount": amount, "to_id": str(member.id), "to_name": member.display_name, "net": -amount}))
        asyncio.create_task(log_history(member.id, {"cmd": "receive", "amount": amount, "from_id": str(ctx.author.id), "from_name": ctx.author.display_name, "net": amount}))

    @commands.command(aliases=["รายวัน", "เช็คอิน"])
    async def daily(self, ctx):
        result = await try_daily(ctx.author.id)
        if result is False:
            await ctx.send("⚠️ คลังหลวงไม่มีเงินพอจ่ายรายวันตอนนี้ ลองใหม่ภายหลัง")
            return
        if result is None:
            from ..bank import _ref
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
            asyncio.create_task(_post_game(ctx, 0, True, 0, False, ["daily_7"]))

    @commands.command(aliases=["เลเวล", "lv", "lvl"])
    async def level(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        _, deposited = await get_balance(target)
        from ..bank import _ref
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
        jp_rate = min(8 / 512 * luck, 0.99) * 100
        await ctx.send(
            f"✅ **{member.display_name}** luck = **{luck}x**\n"
            f"Jackpot rate: **{jp_rate:.2f}%**/spin (ปกติ 1.56%)"
        )

    @commands.command(aliases=["โชค"])
    async def luck(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        lk = await get_user_luck(target.id)
        jp_rate = min(8 / 512 * lk, 0.99) * 100
        color = discord.Color.green() if lk >= 1.0 else discord.Color.red()
        em = discord.Embed(color=color)
        em.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        em.add_field(name="🍀 Luck Modifier", value=f"**{lk}x**", inline=True)
        em.add_field(name="🎰 Jackpot Rate", value=f"**{jp_rate:.2f}%** / spin", inline=True)
        base_rate = 8 / 512 * 100
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

        pair_key = (ctx.author.id, member.id)
        now = time.time()
        cooldown_left = 7200 - (now - _rob_cooldowns.get(pair_key, 0))
        if cooldown_left > 0:
            h, m = int(cooldown_left // 3600), int((cooldown_left % 3600) // 60)
            await ctx.send(f"🕐 ยังปล้น **{member.display_name}** ไม่ได้ รออีก **{h}ชม. {m}นาที**")
            return

        target_wallet, _ = await get_balance(member)
        if target_wallet < 500:
            await ctx.send(f"**{member.display_name}** จนเกินปล้น 💸 (ต้องมีอย่างน้อย 500 🪙)")
            return

        _rob_cooldowns[pair_key] = now
        robber_wallet, _ = await get_balance(ctx.author)
        success = random.random() < 0.35

        if success:
            pct = random.uniform(0.05, 0.15)
            stolen = max(int(target_wallet * pct), 1)
            if await charge_wallet(member, stolen) is None:
                await ctx.send("มีบางอย่างผิดพลาด ลองใหม่")
                return
            await update_bank(ctx.author, stolen)
            em = discord.Embed(title="🦹 ปล้นสำเร็จ!!", color=discord.Color.green())
            em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            em.set_thumbnail(url=member.display_avatar.url)
            em.add_field(name="เหยื่อ", value=member.display_name, inline=True)
            em.add_field(name="ได้ไป", value=f"**+{stolen:,}** 🪙 ({pct*100:.0f}%)", inline=True)
            em.set_footer(text="ปล้นคนเดิมได้อีกใน 2 ชม.")
        else:
            penalty = min(int(robber_wallet * 0.10), 500_000)
            got_paid = ""
            if penalty > 0 and await charge_wallet(ctx.author, penalty) is not None:
                await update_bank(member, penalty)
                got_paid = f"\n**{member.display_name}** ได้รับค่าเสียหาย **{penalty:,}** 🪙"
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
