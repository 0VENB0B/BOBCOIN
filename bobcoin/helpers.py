import discord

from .bank import charge_wallet, open_account, update_bank
from .settings import MAX_BET


def parse_positive_int(value, max_value=MAX_BET):
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None

    if amount <= 0 or amount > max_value:
        return None
    return amount


async def parse_amount_or_reply(ctx, value, missing_message, invalid_message=None):
    amount = parse_positive_int(value)
    if amount is None:
        message = missing_message if value is None else (invalid_message or missing_message)
        await ctx.send(message)
        return None
    return amount


def role_can_be_assigned(ctx, role):
    if ctx.guild is None:
        return False, "ใช้คำสั่งนี้ได้เฉพาะใน server"

    me = ctx.guild.me
    if me is not None and role >= me.top_role:
        return False, "บอทไม่มีสิทธิ์ให้ role นี้"

    owner = getattr(ctx.guild, "owner", None)
    if owner != ctx.author and role >= ctx.author.top_role:
        return False, "คุณให้ role ที่สูงกว่าหรือเท่ากับตัวเองไม่ได้"

    return True, None


async def buy_role(ctx, member, role, price):
    await open_account(ctx.author)
    if member is None:
        await ctx.send("ใส่ชื่อที่จะซื้อของให้")
        return
    if role is None:
        await ctx.send("ใส่สิ่งของที่ต้องการ")
        return

    allowed, reason = role_can_be_assigned(ctx, role)
    if not allowed:
        await ctx.send(reason)
        return

    if await charge_wallet(ctx.author, price) is None:
        await ctx.send("เงินไม่พอ # จ น")
        return

    try:
        await member.add_roles(role)
    except (discord.Forbidden, discord.HTTPException):
        await update_bank(ctx.author, price)
        await ctx.send("ให้ role ไม่สำเร็จ คืนเงินแล้ว")
        return

    await ctx.send(f"{member} was given {role}")

