from .settings import BOT_ADMIN_ROLE_IDS, BOT_OWNER_ID, MAX_BET


def parse_positive_int(value, max_value=MAX_BET):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value.isdecimal() or len(value) > len(str(max_value)):
            return None
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


async def is_bot_admin(ctx) -> bool:
    if ctx.author.id == BOT_OWNER_ID:
        return True
    try:
        if await ctx.bot.is_owner(ctx.author):
            return True
    except Exception:
        pass
    if not BOT_ADMIN_ROLE_IDS:
        return False
    return any(getattr(role, "id", None) in BOT_ADMIN_ROLE_IDS for role in getattr(ctx.author, "roles", ()))
