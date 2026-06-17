import asyncio
import logging
import random
import re

import discord
from discord.ext import commands

from ..ai import BOB_SYSTEM, call_ai
from ..helpers import parse_positive_int
from ..settings import COMMAND_PREFIX, MAX_BET

logger = logging.getLogger("bobcoin.events")

SWEAR_WORDS = [
    "มึง", "กู", "ไอสัตว์", "ไอเหี้ย", "เหี้ย", "สัตว์", "ควาย",
    "หน้าหี", "แม่ง", "เย็ด", "บ้า", "โง่", "ไอโง่", "ไอบ้า",
    "ไอควาย", "ไอเหี้ย",
]

ROAST_FALLBACKS = [
    "ปากเหม็นจัง",
    "ด่าได้แค่นี้เองเหรอ 💀",
    "แม่แกสอนมาแค่นี้เองเหรอ",
    "ไปนอนก่อนเถอะ",
]

_GAME_KEYWORDS = {
    "slot":    ["slot", "สล็อต"],
    "flip":    ["flip", "ทอยเหรียญ", "หัวก้อย", "โยนเหรียญ", "ฟลิป"],
    "lottery": ["lottery", "หวย", "ลอตเตอรี่", "ลอต"],
}
_ALLIN_KW  = ["all in", "allin", "ทุ่มหมด", "ทั้งหมด", "หมดตัว", "หมดเลย", "ทุ่มทั้งหมด"]
_HALF_KW   = ["ครึ่ง", "half", "ครึ่งนึง"]


def _parse_intent(text: str) -> dict | None:
    t = text.lower()
    game = next((g for g, kws in _GAME_KEYWORDS.items() if any(k in t for k in kws)), None)
    if not game:
        return None

    allin = any(w in t for w in _ALLIN_KW)
    half  = any(w in t for w in _HALF_KW)
    amount_spec = "allin" if allin else ("half" if half else None)

    # extract numbers — 5-digit = lottery ticket, rest = amount
    all_nums = []
    for raw in re.findall(r"\b(\d[\d,]*)\b", text):
        cleaned = raw.replace(",", "")
        if cleaned.isdecimal() and len(cleaned) <= len(str(MAX_BET)):
            all_nums.append(cleaned)

    if game == "lottery":
        ticket = next((n for n in all_nums if len(n) == 5), None)
        others = [n for n in (parse_positive_int(n) for n in all_nums if len(n) != 5) if n is not None]
        if amount_spec is None and others:
            amount_spec = others[0]
        return {"game": "lottery", "amount": amount_spec, "ticket": ticket}

    if amount_spec is None and all_nums:
        amount_spec = parse_positive_int(all_nums[0])

    if game == "flip":
        if any(w in t for w in ["หัว", "head"]):
            side = "1"
        elif any(w in t for w in ["ก้อย", "tail"]):
            side = "2"
        else:
            side = random.choice(["1", "2"])
        return {"game": "flip", "amount": amount_spec, "side": side}

    return {"game": "slot", "amount": amount_spec}



class EventsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _handle_game_intent(self, msg: discord.Message, intent: dict, user_text: str) -> None:
        from ..bank import get_balance

        game        = intent["game"]
        amount_spec = intent["amount"]

        # resolve amount
        if amount_spec in ("allin", "half"):
            wallet, _ = await get_balance(msg.author)
            amount = wallet if amount_spec == "allin" else wallet // 2
        else:
            amount = int(amount_spec) if amount_spec else 100

        amount = min(max(amount, 1), MAX_BET)

        game_labels = {"slot": "สล็อต 🎰", "flip": "ทอยเหรียญ 🪙", "lottery": "หวย 🎟️"}

        if game == "slot":
            msg.content = f"{COMMAND_PREFIX}slot {amount}"
            situation = f'{msg.author.display_name} บอกว่า "{user_text}" อยากเล่นสล็อต {amount:,} เหรียญ ตอบสั้นๆ แบบ BOB กวนๆ'

        elif game == "flip":
            side      = intent["side"]
            side_name = "หัว" if side == "1" else "ก้อย"
            msg.content = f"{COMMAND_PREFIX}flip {side} {amount}"
            situation = f'{msg.author.display_name} บอกว่า "{user_text}" อยากทอยเหรียญ เลือก{side_name} {amount:,} เหรียญ ตอบสั้นๆ แบบ BOB'

        elif game == "lottery":
            ticket = intent.get("ticket")
            if not ticket:
                no_ticket = await call_ai(
                    BOB_SYSTEM,
                    [{"role": "user", "content": f'{msg.author.display_name} อยากเล่นหวยแต่ไม่บอกเลข 5 หลัก บอกให้ใส่เลขด้วย แบบ BOB'}],
                    fallback="หวยต้องบอกเลข 5 หลักด้วยนะ เช่น `@BOB หวย 36412 all in`",
                    max_tokens=60,
                )
                await msg.reply(no_ticket)
                return
            msg.content = f"{COMMAND_PREFIX}lottery {ticket} {amount}"
            situation = f'{msg.author.display_name} บอกว่า "{user_text}" อยากเล่นหวยเลข {ticket} {amount:,} เหรียญ ตอบสั้นๆ แบบ BOB'

        # generate confirm ด้วย AI (parallel กับ process_commands จะรันหลัง)
        confirm = await call_ai(
            BOB_SYSTEM,
            [{"role": "user", "content": situation}],
            fallback=f"โอเค จัดให้ {game_labels[game]} {amount:,} เหรียญ",
            max_tokens=60,
        )
        await msg.reply(confirm)
        await self.bot.process_commands(msg)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        from bobcoin.cogs.economy import NotRegistered
        error = getattr(error, "original", error)

        if isinstance(error, NotRegistered):
            return  # message already sent in cog_before_invoke
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("ไม่มีคำสั่งนี้")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send("ใจเย็น ลองอีกครั้งใน {:.2f} วิ".format(error.retry_after))
            return
        if isinstance(error, (commands.MissingPermissions, commands.MissingAnyRole)):
            await ctx.send("ไม่มีสิทธิ์ใช้คำสั่งนี้")
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("ไม่มีสิทธิ์ใช้คำสั่งนี้")
            return
        if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await ctx.send("รูปแบบคำสั่งไม่ถูกต้อง")
            return

        logger.error(
            "Unhandled command error in %s",
            ctx.command,
            exc_info=(type(error), error, error.__traceback__),
        )
        await ctx.send("คำสั่งนี้มีปัญหา ลองใหม่อีกครั้ง")

    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.author.bot:
            return

        if "$reaction" in msg.content:
            await msg.add_reaction("<:Discord:895553740793339986>")

        # mention หรือ reply หา bot → คุยได้เลย
        bot_mentioned = self.bot.user in msg.mentions
        replied_to_bot = (
            msg.reference is not None
            and msg.reference.resolved is not None
            and isinstance(msg.reference.resolved, discord.Message)
            and msg.reference.resolved.author == self.bot.user
        )

        if bot_mentioned or replied_to_bot:
            user_text = msg.content.replace(f"<@{self.bot.user.id}>", "").strip() or "สวัสดี"

            intent = _parse_intent(user_text)
            if intent:
                await self._handle_game_intent(msg, intent, user_text)
                return

            # generic chat — ดึง context จาก reply ถ้ามี
            messages = []
            if replied_to_bot and msg.reference.resolved:
                prev = msg.reference.resolved.content
                if prev:
                    messages.append({"role": "assistant", "content": prev})
            messages.append({"role": "user", "content": user_text})

            async with msg.channel.typing():
                reply = await call_ai(BOB_SYSTEM, messages, "...")
            await msg.reply(reply)
            return

        # ด่า → ด่ากลับ
        content_lower = msg.content.lower()
        if any(swear in content_lower for swear in SWEAR_WORDS):
            fallback = random.choice(ROAST_FALLBACKS)
            reply = await call_ai(
                BOB_SYSTEM,
                [{"role": "user", "content": f'คนนี้ด่ามาว่า: "{msg.content}" ด่ากลับให้หน่อย แบบ BOB'}],
                fallback,
            )
            await msg.reply(reply)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.change_presence(activity=discord.Game(name=f"{COMMAND_PREFIX}command"))


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
