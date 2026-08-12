"""Game runners shared by the prefix commands and the casino panel
(kept separate from EconomyCog so each file stays small)."""

import asyncio
import logging
import random

import discord

from . import metrics
from .ai import BOB_SYSTEM, call_ai
from .bank import (
    ACHIEVEMENTS,
    add_xp,
    charge_wallet,
    contribute_jackpot,
    get_cooldown,
    get_effective_luck,
    get_history,
    get_house_balance,
    grant_achievement,
    house_can_pay_games,
    house_payout,
    house_receive,
    log_history,
    max_bet_allowed,
    record_game_outcome,
    set_cooldown,
    trigger_jackpot,
    update_bank,
)
from .games import (
    RPS_CHOICES,
    _bj_draw,
    _bj_str,
    _bj_total,
    _lucky_card,
    _rps_move_that_beats,
    _rps_move_that_loses_to,
    _rps_winner,
    _streak_effects,
)
from .settings import SLOT_JACKPOT_BASE, SLOT_SYMBOLS

logger = logging.getLogger("bobcoin.gameplay")

# Slot replay buttons reuse the panel's persisted per-game cooldown (seconds).
_PANEL_SLOT_CD = 30

# Fire-and-forget background tasks must keep a reference, or CPython's GC can
# destroy the Task mid-flight and cancel it silently (RUF006). Hold every
# spawned task in a set until it finishes.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """Schedule a background coroutine, keeping it alive until completion."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    metrics.incr("background_tasks_spawned")
    return task


def _on_task_done(task: asyncio.Task) -> None:
    """Drop the reference and retrieve the exception (if any).

    Retrieving the exception silences the "Task exception was never retrieved"
    warning. These are fire-and-forget tasks (log_history, XP, AI commentary),
    so a raised exception must not crash anything — but it would be nice to see
    it in the logs, so re-log it instead of swallowing silently.
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task failed", exc_info=exc)
        metrics.incr("background_task_failures")


async def drain_background_tasks(timeout: float = 5.0) -> int:
    """Wait up to ``timeout`` seconds for in-flight background tasks to finish.

    Used at shutdown (P3 Ops — graceful shutdown) so fire-and-forget writes
    (history, XP, achievements) aren't cut off mid-flight. Returns the number
    of tasks still pending after the timeout (they are cancelled).
    """
    pending = list(_background_tasks)
    if not pending:
        return 0
    _done, still_pending = await asyncio.wait(pending, timeout=timeout)
    for task in still_pending:
        task.cancel()
    return len(still_pending)




class _BJView(discord.ui.View):
    def __init__(self, player_id: int, allow_double: bool = False):
        super().__init__(timeout=30)
        self.player_id = player_id
        self.action: str | None = None
        # Double Down is only offered on the first two cards (solo games).
        # Duels keep the classic Hit/Stand (allow_double=False) so both players
        # stay on the same bet.
        if not allow_double:
            self.remove_item(self.double_down)

    @discord.ui.button(label="Hit 🃏", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
            return
        self.action = "hit"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Double ⬇️", style=discord.ButtonStyle.blurple)
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
            return
        self.action = "double"
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


class _PanelCtx:
    """Duck-type substitute for commands.Context — used by panel buttons."""
    __slots__ = ("_ch", "author")

    def __init__(self, channel, user):
        self.author = user
        self._ch = channel

    async def send(self, *a, **kw):
        return await self._ch.send(*a, **kw)


def _ctx_channel(ctx):
    """Resolve the message target from a real Context or a _PanelCtx."""
    return getattr(ctx, "channel", None) or getattr(ctx, "_ch", None)


class _SlotReplayView(discord.ui.View):
    """Attached to the final slot embed — one-click replay of the same bet."""

    def __init__(self, channel, author, amount: int):
        super().__init__(timeout=60)
        self.channel = channel
        self.author_id = author.id
        self.amount = amount

    async def _owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
        return False

    @discord.ui.button(label="🔄 เล่นอีก", style=discord.ButtonStyle.blurple)
    async def replay(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._owner(interaction):
            return
        # Replay respects the same persisted cooldown as the panel slot button
        # (Firestore-backed, survives restarts) — no free spin-spamming.
        remaining = await get_cooldown(interaction.user.id, "panel_slot", _PANEL_SLOT_CD)
        if remaining > 0:
            await interaction.response.send_message(f"⏳ รออีก **{remaining:.0f}** วิ ก่อนหมุนต่อ", ephemeral=True)
            return
        await set_cooldown(interaction.user.id, "panel_slot")
        await interaction.response.defer()
        await _run_slot(_PanelCtx(self.channel, interaction.user), self.amount)

    @discord.ui.button(label="❌ ปิด", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not await self._owner(interaction):
            return
        await interaction.response.edit_message(view=None)


_HOUSE_BAND_COLORS = (
    discord.Color.green(),
    discord.Color.yellow(),
    discord.Color.orange(),
    discord.Color.red(),
)

_SLOT_SYSTEM  = BOB_SYSTEM + " ตอนนี้กำลังแสดงผลสล็อตให้ user ดู พูดแบบ BOB ตอบสนองต่อผลที่ออกมา"
_FLIP_SYSTEM  = BOB_SYSTEM + " ตอนนี้กำลังทอยเหรียญให้ user ดู พูดแบบ BOB ตอบสนองต่อ user ตามสถานการณ์"
_LOTTERY_SYSTEM = BOB_SYSTEM + " ตอนนี้กำลังออกผลหวยให้ user ดู พูดแบบ BOB ทำนายหรือ react ตามสถานการณ์"
_RPS_SYSTEM = BOB_SYSTEM + " ตอนนี้กำลังเล่นเป่ายิ้งฉุบ (ค้อน/กรรไกร/กระดาษ) กับ user พูดแบบ BOB กวนๆ ตอบสนองตามผล"


async def _get_game_streak(user_id: int) -> tuple[int, bool]:
    """Return (streak_length, is_win_streak) from recent decisive game history."""
    entries = await get_history(user_id, limit=15)
    decisive = [e for e in entries if e.get("cmd") in ("slot", "flip", "lottery", "rps") and e.get("net", 0) != 0]
    if not decisive:
        return 0, False
    is_win = decisive[0]["net"] > 0
    n = next((i for i, e in enumerate(decisive) if (e["net"] > 0) != is_win), len(decisive))
    return n, is_win


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


async def _bet_too_big(ctx, amount: int) -> bool:
    """Send a notice and return True if the bet exceeds what the house can cover (whale-proof)."""
    bal = await get_house_balance()
    max_bet = await max_bet_allowed(bal)
    if amount <= max_bet:
        return False
    await ctx.send(
        f"⚠️ เดิมพันสูงสุดตอนนี้คือ **{max_bet:,}** 🪙 (กันปลาวาฬล้างคลัง)\n"
        f"คลังหลวงมีแค่ **{bal:,}** 🪙 เดิมพันน้อยๆ ก่อนนะ 😉"
    )
    return True


async def _begin_game(ctx, amount: int) -> bool:
    """Shared opening for every game: block if the house can't pay or the bet is
    too big, then charge the player and collect the bet into the house.
    Returns False when the game should not run."""
    if await _house_closed_embed(ctx):
        return False
    if await _bet_too_big(ctx, amount):
        return False
    if await charge_wallet(ctx.author, amount) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return False
    await house_receive(amount)
    return True


async def _post_game(ctx, bet: int, won: bool, streak: int, streak_is_win: bool, ach_keys: list[str]) -> None:
    """Fire-and-forget: XP, achievements, level-up notification."""
    xp_gain = max(bet // 1_000, 1)
    _, new_level, leveled_up = await add_xp(ctx.author.id, xp_gain)
    if leveled_up:
        _spawn(ctx.send(
            f"⬆️ **Level Up!** {ctx.author.mention} → **Level {new_level}** 🎉",
            delete_after=10,
        ))
        if new_level >= 10:
            ach_keys.append("level_10")
    for key in ach_keys:
        newly = await grant_achievement(ctx.author.id, key)
        if newly and key in ACHIEVEMENTS:
            icon, name, desc = ACHIEVEMENTS[key]
            _spawn(ctx.send(
                f"🏆 **Achievement Unlocked!** {icon} **{name}** — {desc}",
                delete_after=12,
            ))


# ── Standalone game runners (called by both prefix commands and panel buttons) ─

async def _run_slot(ctx, amount: int) -> None:
    if not await _begin_game(ctx, amount):
        return
    jackpot_contrib = max(amount // 100, 1)
    jackpot_pool_task = _spawn(contribute_jackpot(jackpot_contrib))

    _BASE_JP = SLOT_JACKPOT_BASE
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

    commentary_task = _spawn(call_ai(_SLOT_SYSTEM, [{"role": "user", "content": outcome}], fallback="", max_tokens=80))
    streak_task = _spawn(_get_game_streak(ctx.author.id))

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
        _spawn(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": label, "multiplier": multiplier, "net": net}))
        ach_keys.append("first_win")
        if streak_is_win and streak >= 4:
            ach_keys.append("streak_5")
    elif is_two_match:
        actual = await house_payout(amount)
        await update_bank(ctx.author, actual)
        net = actual - amount
        em.add_field(name=label, value=f"คืนทุน **{actual:,}** 🪙", inline=True)
        if commentary:
            em.add_field(name="​", value=f"*{commentary}*", inline=False)
        _spawn(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": "near", "multiplier": 0, "net": net}))
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
        _spawn(log_history(ctx.author.id, {"cmd": "slot", "bet": amount, "symbols": symbols_str, "outcome": "lose", "multiplier": 0, "net": net}))

    em.set_footer(text=f"{ctx.author.display_name}  •  เดิมพัน {amount:,} เหรียญ")
    await msg.edit(embed=em, view=_SlotReplayView(_ctx_channel(ctx), ctx.author, amount))
    _spawn(record_game_outcome("slot", amount, net))
    _spawn(_post_game(ctx, amount, is_jackpot, streak, streak_is_win, ach_keys))


async def _run_flip(ctx, amount: int, side: str) -> None:
    """side: '1' = หัว, '2' = ก้อย"""
    if not await _begin_game(ctx, amount):
        return

    choice_name = "หัว" if side == "1" else "ก้อย"
    choice_icon = "👑" if side == "1" else "🦅"
    user_luck = await get_effective_luck(ctx.author.id)
    win_chance = min(0.5 * (user_luck ** 0.2), 0.72)
    win = random.random() < win_chance
    bot_pick = int(side) if win else (3 - int(side))
    drawn_name = "หัว" if bot_pick == 1 else "ก้อย"
    drawn_icon = "👑" if bot_pick == 1 else "🦅"
    payout = int(amount * 1.8) if win else 0

    taunt_task = _spawn(call_ai(
        _FLIP_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือก{choice_name} เดิมพัน {amount:,} เหรียญ ยั่วก่อนโยนหน่อย"}],
        fallback="โยนเหรียญแล้ว... ไม่รู้จะออกอะไร",
        max_tokens=60,
    ))
    streak_task = _spawn(_get_game_streak(ctx.author.id))
    react_task = _spawn(call_ai(
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

    _spawn(record_game_outcome("flip", amount, net))
    _spawn(log_history(ctx.author.id, {"cmd": "flip", "bet": amount, "choice": choice_name, "drawn": drawn_name, "win": win, "net": net}))
    flip_ach = []
    if amount >= 1_000_000:
        flip_ach.append("high_roller")
    if win:
        flip_ach.append("first_win")
        if streak_is_win and streak >= 4:
            flip_ach.append("streak_5")
    _spawn(_post_game(ctx, amount, win, streak, streak_is_win, flip_ach))


async def _run_lottery(ctx, ticket_cost: int, text: str) -> None:
    """text: 5-digit ticket number string"""
    if not await _begin_game(ctx, ticket_cost):
        return

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

    prophecy_task = _spawn(call_ai(
        _LOTTERY_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือกเลข {text} เดิมพัน {ticket_cost:,} เหรียญ ทำนายโชคมั่วๆ หน่อย"}],
        fallback="ดวงดาวกำลังจะบอกอะไรบางอย่าง...",
        max_tokens=80,
    ))
    streak_task = _spawn(_get_game_streak(ctx.author.id))
    reaction_task = _spawn(call_ai(
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
    _spawn(record_game_outcome("lottery", ticket_cost, net))
    _spawn(log_history(ctx.author.id, {"cmd": "lottery", "bet": ticket_cost, "pick": text, "drawn": drawn_str, "match": match, "net": net}))
    lot_ach = []
    if ticket_cost >= 1_000_000:
        lot_ach.append("high_roller")
    if win:
        lot_ach.append("first_win")
        if match == "5ตัว":
            lot_ach.append("lottery_5")
        if streak_is_win and streak >= 4:
            lot_ach.append("streak_5")
    _spawn(_post_game(ctx, ticket_cost, bool(win), streak, streak_is_win, lot_ach))


async def _run_bj(ctx, amount: int) -> None:
    if not await _begin_game(ctx, amount):
        return

    user_luck = await get_effective_luck(ctx.author.id)
    player = [_lucky_card(user_luck), _lucky_card(user_luck)]
    dealer = [_bj_draw(), _bj_draw()]

    def _bj_embed(p_hand, d_hand, hide_dealer=True, status=None, color=discord.Color.blurple(), bet=None):
        p_tot = _bj_total(p_hand)
        d_visible = _bj_total([d_hand[0]]) if hide_dealer else _bj_total(d_hand)
        em = discord.Embed(title="🃏  B L A C K J A C K", color=color)
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        d_label = f"🏠 Dealer  {f'= {_bj_total(d_hand)}' if not hide_dealer else f'[{d_visible}?]'}"
        em.add_field(name=d_label, value=_bj_str(d_hand, hide_dealer), inline=False)
        em.add_field(name=f"👤 You  = {p_tot}", value=_bj_str(p_hand), inline=False)
        if status:
            em.add_field(name="​", value=status, inline=False)
        em.set_footer(text=f"เดิมพัน {bet or amount:,} 🪙  •  Hit, Double หรือ Stand")
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
        _spawn(record_game_outcome("bj", amount, net))
        _spawn(log_history(ctx.author.id, {"cmd": "bj", "bet": amount, "result": "blackjack", "net": net}))
        _spawn(_post_game(ctx, amount, net > 0, 0, False, ach))
        return

    msg = await ctx.send(embed=_bj_embed(player, dealer))

    total_bet = amount
    doubled = False
    while _bj_total(player) < 21:
        # Double Down: only on the first two cards, once per hand
        allow_double = len(player) == 2 and not doubled
        view = _BJView(ctx.author.id, allow_double=allow_double)
        await msg.edit(embed=_bj_embed(player, dealer, bet=total_bet), view=view)
        timed_out = await view.wait()
        if timed_out or view.action == "stand":
            break
        if view.action == "double":
            if await charge_wallet(ctx.author, amount) is None:
                await ctx.send("เงินไม่พอจะ Double 😅 ถือว่า Stand นะ")
                break
            await house_receive(amount)
            total_bet += amount
            doubled = True
            await msg.edit(embed=_bj_embed(player, dealer, False, "⬇️ **Double Down!** รับไพ่อีก 1 ใบแล้วจบ", bet=total_bet))
            await asyncio.sleep(0.5)
            player.append(_lucky_card(user_luck))
            break
        player.append(_lucky_card(user_luck))

    p_total = _bj_total(player)

    if p_total > 21:
        net = -total_bet
        em = _bj_embed(player, dealer, False, f"💥 **BUST!**  **-{total_bet:,}** 🪙", discord.Color.red(), bet=total_bet)
        await msg.edit(embed=em, view=None)
        _spawn(record_game_outcome("bj", total_bet, net))
        _spawn(log_history(ctx.author.id, {"cmd": "bj", "bet": total_bet, "doubled": doubled, "p": p_total, "d": _bj_total(dealer), "result": "bust", "net": net}))
        _spawn(_post_game(ctx, total_bet, False, 0, False, ["high_roller"] if total_bet >= 1_000_000 else []))
        return

    while _bj_total(dealer) < 17:
        dealer.append(_bj_draw())
        await msg.edit(embed=_bj_embed(player, dealer, False, "🏠 Dealer กำลังหยิบไพ่...", bet=total_bet))
        await asyncio.sleep(0.6)

    d_total = _bj_total(dealer)

    if d_total > 21 or p_total > d_total:
        payout = int(total_bet * 1.8)
        actual = await house_payout(payout)
        await update_bank(ctx.author, actual)
        net = actual - total_bet
        bust_note = "💥 Dealer Bust!  " if d_total > 21 else ""
        em = _bj_embed(player, dealer, False, f"{bust_note}✅ **ชนะ!**  **+{net:,}** 🪙", discord.Color.green(), bet=total_bet)
        ach = ["first_win"] + (["high_roller"] if total_bet >= 1_000_000 else [])
        result = "win"
    elif p_total == d_total:
        actual = await house_payout(total_bet)
        await update_bank(ctx.author, actual)
        net = 0
        em = _bj_embed(player, dealer, False, "🤝 **Push** — คืนทุน", discord.Color.orange(), bet=total_bet)
        ach = ["high_roller"] if total_bet >= 1_000_000 else []
        result = "push"
    else:
        net = -total_bet
        em = _bj_embed(player, dealer, False, f"❌ **แพ้!**  **-{total_bet:,}** 🪙", discord.Color.red(), bet=total_bet)
        ach = ["high_roller"] if total_bet >= 1_000_000 else []
        result = "lose"

    await msg.edit(embed=em, view=None)
    _spawn(record_game_outcome("bj", total_bet, net))
    _spawn(log_history(ctx.author.id, {"cmd": "bj", "bet": total_bet, "doubled": doubled, "p": p_total, "d": d_total, "result": result, "net": net}))
    _spawn(_post_game(ctx, total_bet, net > 0, 0, False, ach))


# ── Rock Paper Scissors ───────────────────────────────────────────────────────

class _RPSView(discord.ui.View):
    def __init__(self, player_id: int):
        super().__init__(timeout=30)
        self.player_id = player_id
        self.choice: str | None = None

    async def _pick(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("ไม่ใช่เกมของแก!", ephemeral=True)
            return
        self.choice = choice
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="🪨 ค้อน", style=discord.ButtonStyle.gray)
    async def rock(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, "rock")

    @discord.ui.button(label="✂️ กรรไกร", style=discord.ButtonStyle.gray)
    async def scissors(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, "scissors")

    @discord.ui.button(label="📄 กระดาษ", style=discord.ButtonStyle.gray)
    async def paper(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._pick(interaction, "paper")


async def _run_rps(ctx, amount: int) -> None:
    """เป่ายิ้งฉุบ vs BOT — ชนะ 1.8x, เสมอได้คืน, แพ้เสียเดิมพัน (Luck ส่งผล)."""
    if not await _begin_game(ctx, amount):
        return

    user_luck = await get_effective_luck(ctx.author.id)
    view = _RPSView(ctx.author.id)
    em = discord.Embed(
        title="✊  R P S   D U E L",
        description="เลือกเลย: 🪨 ค้อน  ✂️ กรรไกร  📄 กระดาษ",
        color=discord.Color.blurple(),
    )
    em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    em.set_footer(text=f"เดิมพัน {amount:,} 🪙  •  ชนะ 1.8x  •  ตอบภายใน 30 วิ")
    msg = await ctx.send(embed=em, view=view)
    timed_out = await view.wait()

    if timed_out or view.choice is None:
        refund = await house_payout(amount)
        await update_bank(ctx.author, refund)
        em = discord.Embed(title="⏰ หมดเวลา!", description=f"คืนเดิมพัน **{refund:,}** 🪙 ให้แล้ว คราวหน้าเลือกให้ไวขึ้นนะ 😏", color=discord.Color.dark_gray())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await msg.edit(embed=em, view=None)
        return

    player_choice = view.choice
    # Luck shifts the outcome odds (same shape as flip): better luck → the bot
    # picks the move that loses to yours more often, capped so it's never a joke.
    win_thresh = min(0.33 * (user_luck ** 0.2), 0.45)
    roll = random.random()
    if roll < win_thresh:
        bot_choice = _rps_move_that_loses_to(player_choice)
    elif roll < win_thresh + 0.33:
        bot_choice = player_choice
    else:
        bot_choice = _rps_move_that_beats(player_choice)

    result = _rps_winner(player_choice, bot_choice)
    taunt_task = _spawn(call_ai(
        _RPS_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือก{RPS_CHOICES[player_choice]} เดิมพัน {amount:,} เหรียญ ยั่วก่อนออกมือหน่อย"}],
        fallback="เป่ายิ้งฉุบ! ปล่อยหมัดมาเลย",
        max_tokens=60,
    ))
    streak_task = _spawn(_get_game_streak(ctx.author.id))
    react_task = _spawn(call_ai(
        _RPS_SYSTEM,
        [{"role": "user", "content": f"{ctx.author.name} เลือก{RPS_CHOICES[player_choice]} บอทเลือก{RPS_CHOICES[bot_choice]} {'ชนะ' if result > 0 else 'เสมอ' if result == 0 else 'แพ้'} เดิมพัน {amount:,} เหรียญ react สั้นๆ แซวๆ"}],
        fallback="ดีเลย!" if result > 0 else "เสมอกันพอดี" if result == 0 else "โชคไม่ดีเลยนะ",
        max_tokens=60,
    ))

    _FRAMES = ["🪨", "✂️", "📄", "💥"]
    for frame in _FRAMES:
        await msg.edit(embed=_rps_frame_embed(ctx, frame, amount))
        await asyncio.sleep(0.3)

    taunt = await taunt_task
    em = _rps_frame_embed(ctx, _FRAMES[-1], amount)
    if taunt:
        em.add_field(name="​", value=f"*{taunt}*", inline=False)
    await msg.edit(embed=em)
    await asyncio.sleep(0.6)

    reaction = await react_task
    streak, streak_is_win = await streak_task
    bonus_pct, mercy = _streak_effects(streak, streak_is_win, amount)

    my_icon = RPS_CHOICES[player_choice]
    bot_icon = RPS_CHOICES[bot_choice]
    if result > 0:
        payout = int(amount * 1.8)
        streak_bonus = int(payout * bonus_pct)
        actual = await house_payout(payout + streak_bonus)
        await update_bank(ctx.author, actual)
        net = actual - amount
        em = discord.Embed(title="✅ ชนะ!", color=discord.Color.green())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="👤 คุณ", value=my_icon, inline=True)
        em.add_field(name="🤖 BOB", value=bot_icon, inline=True)
        em.add_field(name="💰 ได้รับ", value=f"**+{net:,}** 🪙 (1.8x)", inline=False)
        if streak_bonus > 0:
            em.add_field(name=f"🔥 {streak}x Win Streak!", value=f"+{int(bonus_pct*100)}% bonus (+{streak_bonus:,} 🪙)", inline=False)
        if actual < payout + streak_bonus:
            em.add_field(name="⚠️ คลังหลวงแห้ง!", value=f"จ่ายได้แค่ **{actual:,}** 🪙", inline=False)
        result_tag = "win"
    elif result == 0:
        actual = await house_payout(amount)
        await update_bank(ctx.author, actual)
        net = 0
        em = discord.Embed(title="🤝 เสมอ!", color=discord.Color.orange())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="👤 คุณ", value=my_icon, inline=True)
        em.add_field(name="🤖 BOB", value=bot_icon, inline=True)
        em.add_field(name="💰 ได้รับ", value=f"คืนทุน **{amount:,}** 🪙", inline=False)
        result_tag = "tie"
    else:
        net = -amount
        em = discord.Embed(title="❌ แพ้!", color=discord.Color.red())
        em.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        em.add_field(name="👤 คุณ", value=my_icon, inline=True)
        em.add_field(name="🤖 BOB", value=bot_icon, inline=True)
        em.add_field(name="💰 เสีย", value=f"**-{amount:,}** 🪙", inline=False)
        if mercy > 0:
            mercy_actual = await house_payout(mercy)
            await update_bank(ctx.author, mercy_actual)
            net += mercy_actual
            em.add_field(name=f"💀 {streak}x Cold Streak — Mercy", value=f"+{mercy_actual:,} 🪙 (3% คืน)", inline=False)
        result_tag = "lose"

    if reaction:
        em.add_field(name="​", value=f"*{reaction}*", inline=False)
    em.set_footer(text=f"เดิมพัน {amount:,} 🪙  •  {my_icon} vs {bot_icon}")
    await msg.edit(embed=em, view=None)

    _spawn(record_game_outcome("rps", amount, net))
    _spawn(log_history(ctx.author.id, {"cmd": "rps", "bet": amount, "pick": player_choice, "bot": bot_choice, "result": result_tag, "net": net}))
    ach = []
    if amount >= 1_000_000:
        ach.append("high_roller")
    if result > 0:
        ach.append("first_win")
        if streak_is_win and streak >= 4:
            ach.append("streak_5")
    _spawn(_post_game(ctx, amount, result > 0, streak, streak_is_win, ach))


def _rps_frame_embed(ctx, frame: str, amount: int) -> discord.Embed:
    em = discord.Embed(description=f"# {frame}", color=discord.Color.blurple())
    em.set_author(name="✊  R P S   D U E L", icon_url=ctx.author.display_avatar.url)
    em.set_footer(text=f"เดิมพัน {amount:,} 🪙  •  เป่ายิ้งฉุบ!")
    return em


# ─────────────────────────────────────────────────────────────────────────────
