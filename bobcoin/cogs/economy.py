import asyncio
import random

import discord
from discord.ext import commands

from ..bank import (
    charge_wallet,
    get_balance,
    get_bank_data,
    open_account,
    transfer_funds,
    update_bank,
)
from ..helpers import buy_role, parse_amount_or_reply, parse_positive_int
from ..settings import SLOT_SPIN_FRAMES, SLOT_SYMBOLS


class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def Backpack(self, ctx):
        wallet_amt, bank_amt = await get_balance(ctx.author)
        em = discord.Embed(title=f"{ctx.author.name}'s balance", color=discord.Color.green())
        em.add_field(name="BOBCOIN balance", value=str(wallet_amt))
        em.add_field(name="BOB Bank balance", value=str(bank_amt))
        await ctx.send(embed=em)

    @commands.command()
    async def TestJson(self, ctx):
        await open_account(ctx.author)
        earning = random.randrange(101)
        await ctx.send(f"On my way son : God gave you {earning} coins!!")
        await update_bank(ctx.author, earning)

    @commands.command()
    @commands.cooldown(1, 120, commands.BucketType.user)
    async def lottery(self, ctx, text=None, amount=None):
        await open_account(ctx.author)
        if text is None:
            await ctx.send("ใส่เลขด้วยสิเฮ้ย(ตัวเลข 5 หลักพอ)")
            return
        if not text.isdecimal() or len(text) != 5:
            await ctx.send("ใส่ตัวเลขโว้ยยย(ตัวเลข 5 หลักพอ)")
            return

        ticket_cost = parse_positive_int(amount or 100)
        if ticket_cost is None:
            await ctx.send("เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000")
            return

        if await charge_wallet(ctx.author, ticket_cost) is None:
            await ctx.send("เงินไม่พอ # จ น")
            return

        bot_number = random.randrange(10000, 99999)
        await ctx.send(f"เลขที่ออก {bot_number}")
        if int(text) == bot_number:
            await ctx.send("สลุต่านชั่วข้ามคืน")
            await update_bank(ctx.author, ticket_cost * 100)
        else:
            await ctx.send("ลางไม่ดีอีกแล้ววว")

    @commands.command(pass_context=True)
    @commands.has_permissions(manage_messages=True)
    async def shop(self, ctx, member: discord.Member = None, *, role: discord.Role = None):
        await buy_role(ctx, member, role, price=1000)

    @commands.command()
    @commands.has_permissions(manage_messages=True)
    @commands.has_any_role("Profile")
    async def BD(self, ctx, member: discord.Member = None, *, role: discord.Role = None):
        await buy_role(ctx, member, role, price=100000)

    @commands.command()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def slot(self, ctx, amount=None):
        await open_account(ctx.author)
        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่เงินที่พนันด้วยสิเฮ้ย!",
            "เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000",
        )
        if amount is None:
            return

        bal = await get_balance(ctx.author)
        max_loss = 20 * amount
        if max_loss > bal[0]:
            await ctx.send("เงินไม่พอ # จ น")
            return

        final = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        message = await ctx.send("Slot Begin!")
        await asyncio.sleep(1)
        for _ in range(5):
            await message.edit(content=random.choice(SLOT_SPIN_FRAMES))
            await asyncio.sleep(0.2)

        await message.edit(content="".join(final))
        if final[0] == final[1] or final[0] == final[2] or final[2] == final[1]:
            await update_bank(ctx.author, 5 * amount)
            await ctx.send("ไอหมอนี้มันมาวะ")
        else:
            await update_bank(ctx.author, -20 * amount)
            await ctx.send("ร ะ วั ง จ น น ะ")

    @commands.command()
    async def withdraw(self, ctx, amount=None):
        await open_account(ctx.author)
        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่เงินที่ฝากด้วยสิเฮ้ย!",
            "จำนวนเงินต้องเป็นตัวเลข 1 ถึง 1,000,000",
        )
        if amount is None:
            return
        if await transfer_funds(ctx.author, amount, "bank", "wallet") is None:
            await ctx.send("เงินไม่พอ # จ น ")
            return

        await ctx.send(f"คุณถอนเงินจำนวน {amount} เหรียญ!")

    @commands.command()
    async def leaderboard(self, ctx, x=3):
        x = parse_positive_int(x, max_value=10) or 3
        users = await get_bank_data()
        totals = []
        for user_id, account in users.items():
            total_amount = int(account.get("wallet", 0)) + int(account.get("bank", 0))
            totals.append((total_amount, int(user_id)))
        totals = sorted(totals, reverse=True)

        em = discord.Embed(
            title=f"Top {x} จตุรเทพแห่งความมั่นคั่ง",
            description="จตุรเทพแห่งความมั่นคั่ง",
            color=discord.Color.purple(),
        )
        for index, (amt, id_) in enumerate(totals[:x], start=1):
            user = self.bot.get_user(id_) or await self.bot.fetch_user(id_)
            em.add_field(name=f"{index}. {user}", value=f"{amt}", inline=False)
        await ctx.send(embed=em)

    @commands.command()
    async def deposit(self, ctx, amount=None):
        await open_account(ctx.author)
        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่เงินที่ถอนด้วยสิเฮ้ย!",
            "จำนวนเงินต้องเป็นตัวเลข 1 ถึง 1,000,000",
        )
        if amount is None:
            return
        if await transfer_funds(ctx.author, amount, "wallet", "bank") is None:
            await ctx.send("เงินไม่พอ # จ น ")
            return

        await ctx.send(f"คุณฝากเงินจำนวน{amount} เหรียญ!")

    @commands.command(aliases=["flipcoin"])
    async def filpcoin(self, ctx, text=None, amount=None):
        await open_account(ctx.author)
        if text is None:
            await ctx.send("กรุณาใส่เลขที่จะทาย\nหัว = 1\nก้อย = 2\nใส่เงินพนัน")
            return
        if text not in {"1", "2"}:
            await ctx.send("กรุณาใส่เลขที่จะทาย\nหัว = 1\nก้อย = 2\nใส่เงินพนัน")
            return

        amount = await parse_amount_or_reply(
            ctx,
            amount,
            "ใส่เงินที่พนันด้วยสิเฮ้ย!",
            "เงินเดิมพันต้องเป็นตัวเลข 1 ถึง 1,000,000",
        )
        if amount is None:
            return

        bal = await get_balance(ctx.author)
        if amount > bal[0]:
            await ctx.send("เงินไม่พอ # จ น")
            return

        bot_pick = random.randint(1, 2)
        await ctx.send(bot_pick)
        await ctx.send("Head" if bot_pick == 1 else "Tail")

        if int(text) == bot_pick:
            await update_bank(ctx.author, amount)
        else:
            await update_bank(ctx.author, -amount)

    @commands.command()
    async def item(self, ctx):
        await ctx.send("@watch | @Profile")


async def setup(bot):
    await bot.add_cog(EconomyCog(bot))

