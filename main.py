import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
BANK_FILE = BASE_DIR / "mainbank.json"
BANK_LOCK = asyncio.Lock()
COMMAND_PREFIX = os.getenv("BOBCOIN_PREFIX", "$")
MAX_BET = 1_000_000
MAX_PURGE_MESSAGES = 100
BOT_ICON_URL = "https://cdn.discordapp.com/attachments/865170212822319114/894807330313621535/Discord.png"
LIKE_ICON_URL = "https://image.similarpng.com/very-thumbnail/2020/06/Icon-like-button-transparent-PNG.png"
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=880963590289498142&permissions=268823616&scope=bot"
SLOT_SYMBOLS = ("🍎", "🍊", "🍐")
SLOT_SPIN_FRAMES = ("🍎🍊🍐", "🍊🍐🍎", "🍐🍎🍊")
MOVIE_RECOMMENDATIONS = (
    "The Shawshank Redemption",
    "The Godfather",
    "The Dark Knight",
    "12 Angry Men",
    "Schindler's List",
    "The Lord of the Rings: The Return of the King",
    "Pulp Fiction",
    "Forrest Gump",
    "Inception",
    "Fight Club",
)

logger = logging.getLogger("bobcoin")

Intents = discord.Intents.default()
Intents.members = True
if hasattr(Intents, "message_content"):
    Intents.message_content = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=Intents)


def asset_path(name):
    return BASE_DIR / name


def parse_positive_int(value, max_value=MAX_BET):
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None

    if amount <= 0 or amount > max_value:
        return None
    return amount


def load_font(size):
    candidates = (
        BASE_DIR / "arial.ttf",
        Path("arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default()


def image_file(image, filename):
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return discord.File(output, filename=filename)


async def avatar_image(member, size=128):
    avatar = getattr(member, "display_avatar", None)
    if avatar is not None:
        avatar = avatar.replace(size=size)
    else:
        avatar = member.avatar_url_as(size=size)

    data = BytesIO(await avatar.read())
    return Image.open(data).convert("RGBA")


def avatar_url(member):
    avatar = getattr(member, "display_avatar", None)
    if avatar is None:
        avatar = getattr(member, "avatar_url", "")
    return str(avatar)


def _read_bank_unlocked():
    if not BANK_FILE.exists():
        return {}

    try:
        with BANK_FILE.open("r", encoding="utf-8") as f:
            users = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read bank data")
        return {}

    if not isinstance(users, dict):
        return {}
    return users


def _write_bank_unlocked(users):
    tmp_file = BANK_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp_file, BANK_FILE)


def _account(users, user_id):
    account = users.setdefault(str(user_id), {})
    account["wallet"] = int(account.get("wallet", 0))
    account["bank"] = int(account.get("bank", 0))
    return account


async def transfer_funds(user, amount, source, target):
    async with BANK_LOCK:
        users = _read_bank_unlocked()
        account = _account(users, user.id)
        if account[source] < amount:
            return None
        account[source] -= amount
        account[target] += amount
        _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]


async def charge_wallet(user, amount):
    return await update_bank(user, -amount, "wallet")


async def get_balance(user):
    async with BANK_LOCK:
        users = _read_bank_unlocked()
        account = users.get(str(user.id))
        if account is None:
            account = {"wallet": 0, "bank": 0}
            users[str(user.id)] = account
            _write_bank_unlocked(users)
        else:
            before = dict(account)
            _account(users, user.id)
            if account != before:
                _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]


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


@bot.event
async def on_command_error(ctx,error):
    error = getattr(error, "original", error)

    if isinstance(error, commands.CommandNotFound):
        await ctx.send("ไม่มีคำสั่งนี้")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send("ใจเย็น ลองอีกครั้งใน {:.2f} วิ".format(error.retry_after))
        return
    if isinstance(error, (commands.MissingPermissions, commands.MissingAnyRole)):
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

@bot.command()
async def stonk(ctx, user: discord.Member = None):
    if user is None:
        user = ctx.author

    rich = Image.open(asset_path("pic.jpg")).convert("RGBA")
    pfp = await avatar_image(user, size=128)
    pfp = pfp.resize((177, 177))
    rich.paste(pfp, (342, 0), pfp)
    await ctx.send(file=image_file(rich, "picture.png"))


@bot.command()
async def DTC(ctx,*,text = "No text entered"):
    text = text[:500]
    img = Image.open(asset_path("white.png")).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = load_font(20)
    draw.text((0,0),text,(0,0,0),font = font)
    await ctx.send(file=image_file(img, "text.png"))

@bot.command()
async def calR(ctx,a:int,b:int):
    width = a
    height = b
    cal = width * height
    em = discord.Embed(title="$calR = width * height",color = discord.Color.green())
    em.set_footer(text = f'พื้นที่ของรูปสี่เหลื่ยม{width} * {height} = {cal}',icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)

@bot.command()
async def calT(ctx,a:int,b:int):
    side = a
    height = b
    cal =  side * height / 2
    em = discord.Embed(title="$cal",color = discord.Color.green())
    em.set_footer(text = f'พื้นที่รูปสี่เหลี่ยมคางหมู{side} * {height} = {cal}',icon_url=BOT_ICON_URL)
    await ctx.send(embed = em)



@bot.command()
async def mrp(ctx):
    movie = random.choice(MOVIE_RECOMMENDATIONS)
    rec = (f'หนังน่าดู {movie} บอกเลยว่าสนุก')
    em = discord.Embed(title = f"หนังที่ดีสำหรับ {ctx.author.name}",color = discord.Color.green())
    em.add_field(name = "ห นั ง คุ ณ ภ า พ",value= rec)
    em.set_thumbnail(url = f"https://image.freepik.com/free-vector/isometric-cinema-icon-set_1284-18691.jpg")
    em.set_footer(text = "ขอให้สนุกน้าาาาา",icon_url = LIKE_ICON_URL)
    await ctx.send(embed=em)
@bot.command()
@commands.has_any_role('DEV')
async def cool(ctx):
    await ctx.send('You are cool indeed')

@bot.command()
async def wait(ctx):
    await ctx.send('wait what')
    await asyncio.sleep(5)
    await ctx.send('wait what')

@bot.command()
async def emoji(ctx,*,text):
    if len(text) > 80:
        await ctx.send("ข้อความยาวเกินไป")
        return

    emoji = []
    num2emo = {
        "0": ":zero:",
        "1": ":one:",
        "2": ":two:",
        "3": ":three:",
        "4": ":four:",
        "5": ":five:",
        "6": ":six:",
        "7": ":seven:",
        "8": ":eight:",
        "9": ":nine:",
    }
    for i in text.lower():
        if i.isdecimal():
            emoji.append(num2emo.get(i, ""))
        elif "a" <= i <= "z":
            emoji.append(f':regional_indicator_' + i + ':')
        elif i.isspace():
            emoji.append(f':heavy_minus_sign:')
        else:
            await ctx.send('กูไม่มีอีโมจิ ไอเด็กเหี้ยนี้')
            return emoji

    await ctx.send(''.join(emoji))



@bot.command()
async def calC(ctx,*,text):
    r = float(text)
    cal = 3.14 * r**2
    em = discord.Embed(title="$calC = pi * radius^2",color = discord.Color.green())
    em.set_footer(text = f'พื้นที่วงกลม {r} * {3.14} = {cal}',icon_url=BOT_ICON_URL)
    await ctx.send(embed = em)
@bot.command()
async def Backpack(ctx):
    wallet_amt, bank_amt = await get_balance(ctx.author)
    em = discord.Embed(title = f"{ctx.author.name}'s balance",color = discord.Color.green())
    em.add_field(name = "BOBCOIN balance",value= str(wallet_amt))
    em.add_field(name = "BOB Bank balance",value= str(bank_amt))
    await ctx.send(embed=em)

@bot.command()
async def TestJson(ctx):
    await open_account(ctx.author)
    earning = random.randrange(101)
    await ctx.send(f"On my way son : God gave you {earning} coins!!")
    await update_bank(ctx.author, earning)

@bot.command()
@commands.cooldown(1, 120, commands.BucketType.user)
async def lottery(ctx,text= None,amount=None):
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

    bot_number = random.randrange(10000,99999)

    await ctx.send(f"เลขที่ออก {bot_number}")
    ans = int(text)
    if ans == bot_number:
        prize = ticket_cost * 100
        await ctx.send("สลุต่านชั่วข้ามคืน")
        await update_bank(ctx.author, prize)
    else:
        await ctx.send("ลางไม่ดีอีกแล้ววว")


@bot.event
async def on_message(msg):
    if msg.author.bot:
        return

    if "$reaction" in msg.content:
        await msg.add_reaction("<:Discord:895553740793339986>")

    await bot.process_commands(msg)

@bot.command()
async def QM(ctx):
    await open_account(ctx.author)

    member = ctx.author
    ran3 = random.randint(200,800)
    ran4 = random.randint(10000,99999)

    question = "%d + %d" % (ran3,ran4)
    answer = ran3 + ran4

    await ctx.send(f"คำถามคือ {question}")
    try:
        message = await bot.wait_for(
            'message',
            check=lambda message: message.author == member and message.channel == ctx.channel,
            timeout=30,
        )
    except asyncio.TimeoutError:
        await ctx.send("หมดเวลา")
        return

    if message.content.strip() == str(answer):
        await ctx.send("แกก็เก่งเหมือนกันนี้")
        await update_bank(ctx.author,500)
        return

    await ctx.send("อ่อนหัด พยายามแค่ไหนก็ยังอ่อนหัด")
    await ctx.send("แต่ไม่เป็นไรรางวัลปลอบใจ 10 บาท")
    await update_bank(ctx.author,10)

@bot.command(pass_context=True)
@commands.has_permissions(manage_messages=True)
async def shop(ctx,member:discord.Member = None,*,role:discord.Role = None):
    await buy_role(ctx, member, role, price=1000)

@bot.command()
@commands.has_permissions(manage_messages=True)
@commands.has_any_role('Profile')
async def BD(ctx,member:discord.Member = None,*,role:discord.Role = None):
    await buy_role(ctx, member, role, price=100000)


@bot.command()
@commands.has_any_role('WATCH')
async def watch(ctx):
    await ctx.send(datetime.today().strftime("Day %d Month %m Year %Y |Time %H:%M"))
@bot.command()
@commands.has_any_role('Profile')
async def profile(ctx,member:discord.Member = None):
    member = ctx.author if member is None else member
    em = discord.Embed(colour=member.color, timestamp=ctx.message.created_at)
    em.set_author(name=f"{member}'s profile")
    em.set_thumbnail(url=avatar_url(member))
    em.add_field(name="ID", value=member.id)
    em.add_field(name="Account created at", value=member.created_at.strftime("%a, %#d %B %Y, %I:%M %p UTC"))
    joined_at = member.joined_at.strftime("%a, %#d %B %Y, %I:%M %p UTC") if member.joined_at else "Unknown"
    em.add_field(name="Joined at", value=joined_at)
    await ctx.send(embed=em)


@bot.command()
@commands.cooldown(1, 10, commands.BucketType.user)
async def slot(ctx,amount = None):
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
    x = 0
    while x < 5:
        await message.edit(content = random.choice(SLOT_SPIN_FRAMES))
        await asyncio.sleep(0.2)
        x += 1
    await message.edit(content = ''.join(final))
    if final[0] == final[1] or final[0] == final[2] or final[2] == final[1]:
        await update_bank(ctx.author,5*amount)
        await ctx.send("ไอหมอนี้มันมาวะ")
    else:
        await update_bank(ctx.author,-20*amount)
        await ctx.send("ร ะ วั ง จ น น ะ")
@bot.command()
async def withdraw(ctx,amount = None):
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
@bot.command()
async def leaderboard(ctx,x = 3):
    x = parse_positive_int(x, max_value=10) or 3
    users = await get_bank_data()
    totals = []
    for user_id, account in users.items():
        total_amount = int(account.get("wallet", 0)) + int(account.get("bank", 0))
        totals.append((total_amount, int(user_id)))
    totals = sorted(totals, reverse=True)

    em = discord.Embed(title = f"Top {x} จตุรเทพแห่งความมั่นคั่ง",description = "จตุรเทพแห่งความมั่นคั่ง",color = discord.Color.purple())
    for index, (amt, id_) in enumerate(totals[:x], start=1):
        user = bot.get_user(id_) or await bot.fetch_user(id_)
        em.add_field(name = f"{index}. {user}",value = f"{amt}",inline = False)
    await ctx.send(embed = em)
@bot.command()
async def deposit(ctx,amount = None):
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

async def open_account(user):
    async with BANK_LOCK:
        users = _read_bank_unlocked()
        if str(user.id) in users:
            before = dict(users[str(user.id)])
            _account(users, user.id)
            if users[str(user.id)] != before:
                _write_bank_unlocked(users)
            return False

        users[str(user.id)] = {"wallet": 0, "bank": 0}
        _write_bank_unlocked(users)
        return True

async def get_bank_data():
    async with BANK_LOCK:
        return _read_bank_unlocked()

async def update_bank(user,change = 0,mode = "wallet"):
    if mode not in {"wallet", "bank"}:
        raise ValueError("mode must be wallet or bank")

    async with BANK_LOCK:
        users = _read_bank_unlocked()
        account = _account(users, user.id)
        new_balance = account[mode] + int(change)
        if new_balance < 0:
            return None
        if int(change) == 0:
            return [account["wallet"], account["bank"]]
        account[mode] = new_balance
        _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]

@bot.command()
async def invite(ctx):
    em = discord.Embed(title = f"BOB's BOBCOIN",color = discord.Color.purple())
    em.add_field(name = "BOBCOIN",value=INVITE_URL)
    em.set_thumbnail(url = BOT_ICON_URL)
    em.set_footer(text = "บ อ ท แ ห่ ง ช น ชั้ น",icon_url = BOT_ICON_URL)
    await ctx.send(embed=em)

@bot.command()
async def ER(ctx):
  message = await ctx.send("hello")
  await asyncio.sleep(1)
  await message.edit(content="newcontent")
@bot.command()
async def item(ctx):
    await ctx.send("@watch | @Profile")
@bot.command()
async def TC(ctx):
    em = discord.Embed(title = f"BOB's BOBCOIN",description = "Test Command For Develop New Feature",colour = discord.Color.green())
    em.set_author(name = "Discord.py Command",icon_url=BOT_ICON_URL)
    em.add_field(name = "DTC ตามด้วยข้อความ [TC]",value = "Test image(TYPE TEXT) manipulation",inline = False)
    em.add_field(name = "stonk ตามด้วย@user [TC]",value = "Test image(TYPE USER) manipulation",inline = False)
    em.add_field(name = "ER [TC]",value = "Test Message Edit",inline = False)
    em.add_field(name = "TestJson [TC]",value = "Test Money Given System",inline = False)
    em.add_field(name = "wait [TC]",value = "Test Asyncio",inline = False)
    em.add_field(name = "reaction [TC]",value = "Test Reaction",inline = False)
    em.set_thumbnail(url = "https://i.pinimg.com/originals/e1/59/25/e15925c931a81678a3c2e0c0a40db781.gif")
    em.set_footer(text = "| บ อ ท แ ห่ ง ช น ชั้ น น |",icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)
@bot.command()
async def ECO(ctx):
    em = discord.Embed(title = f"BOB's BOBCOIN",description = "BOB Economy command",colour = discord.Color.orange())
    em.set_author(name = "Economy Command",icon_url=BOT_ICON_URL)
    em.add_field(name = "deposit [ECO]",value = "Deposit BOBCOIN Feature",inline = False)
    em.add_field(name = "withdraw [ECO]",value = "Withdraw From BOB Bank Feature",inline = False)
    em.add_field(name = "Backpack [ECO]",value = "Check Your Backpack Feature",inline = False)
    em.add_field(name = "lottery ตามด้วยตัวเลข 5 หลัก และเงินเดิมพัน [ECO]",value = "Lottery Feature",inline = False)
    em.add_field(name = "slot ตามด้วยเงินพนัน [ECO]",value = "Slot Feature",inline = False)
    em.add_field(name = "leaderboard [FT]",value = "Leader board Feature",inline = False)
    em.add_field(name = "shop @user @item[FT]",value = "Shop Feature",inline = False)
    em.add_field(name = "filpcoin/flipcoin หัว/ก้อย และเงินเดิมพัน [FT]",value = "Flip Coin Feature",inline = False)
    em.add_field(name = "item [FT]",value="Item Feature",inline = False)
    em.add_field(name = "QM [FT]",value = "Quick Math Feature",inline = False)
    em.set_thumbnail(url = "https://i.pinimg.com/originals/de/4a/90/de4a9060d587b1e7d18d2048c1eec080.gif")
    em.set_footer(text = "| บ อ ท แ ห่ ง ช น ชั้ น น |",icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)

@bot.command()
async def FT(ctx):
    em = discord.Embed(title = f"BOB's BOBCOIN",description = "BOB Feature Command",colour = discord.Color.light_grey())
    em.add_field(name = "emoji ตามด้วยข้อความ(ENG ONLY) [FT]",value = "Convert Text To Emoji Feature",inline = False)
    em.add_field(name = "mrp [FT]",value = "Recommend Moive Feature",inline = False)
    em.add_field(name = "calR ตามด้วย กว้าง(ตัวเลข) และ ยาว(ตัวเลข) [FT]",value = "Calculator Rectangle Feature",inline = False)
    em.add_field(name = "calT ตามด้วย ผลบวกด้านคู่ขนาน(ตัวเลข) และ สูง(ตัวเลข) [FT]",value = "Calculator Trapezoid Feature",inline = False)
    em.add_field(name = "calC ตามด้วย รัศมี(ตัวเลข) [FT]",value = "Calculator Circle Feature",inline = False)
    em.add_field(name = "ind [FT]",value= "Introduce To Make Profile Card",inline = False)
    em.add_field(name = "botinfo [FT]",value="Information About BOB's BOBCOIN",inline = False)
    em.set_thumbnail(url = "http://shardacomputerngp.com/images/header/horoscope.gif")
    em.set_footer(text = "| บ อ ท แ ห่ ง ช น ชั้ น น |",icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)
@bot.command()
async def INFO(ctx):
    em = discord.Embed(title = f"BOB's BOBCOIN",description = "BOB Information Command",colour = discord.Color.dark_green())
    em.add_field(name = "botinfo [INFO]",value = "Information About BOB's BOBCOIN",inline = False)
    em.add_field(name = "invite [INFO]",value = "Invite BOB's BOBCOIN",inline = False)
    em.add_field(name = "ping [INFO]",value = "Check Bot's Ping",inline=False)
    em.add_field(name = "github [INFO]",value="Github BOB's BOBCOIN Feature",inline = False)
    em.set_thumbnail(url = "https://i.pinimg.com/originals/f8/5f/55/f85f55221962f0c1100496ffc0898d40.gif")
    em.set_footer(text = "| บ อ ท แ ห่ ง ช น ชั้ น น |)",icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)
@bot.command()
async def command(ctx):
    em = discord.Embed(title = "Info",
        description = f"prefix {COMMAND_PREFIX}\nคำสั่งตามด้วย[TC] = Test Command\nคำสั่งตามด้วย[FT] = Feature Command\nคำสั่งตามด้วย[ECO] = Economy Feature",
        color = discord.Color.dark_blue())
    em.set_author(name = "BOBCOIN COMMANDS",icon_url = BOT_ICON_URL)
    em.add_field(name = "FT",value = "Feature Command",inline = False)
    em.add_field(name = "TC",value = "Test Command",inline = False)
    em.add_field(name = "ECO",value = "Economy Command",inline = False)
    em.add_field(name = "INFO",value = "Information Command",inline = False)
    em.set_thumbnail(url = "https://cdnb.artstation.com/p/assets/images/images/010/982/795/original/valerian-pranata-file2.gif?1527245408")
    em.set_footer(text = "| บ อ ท แ ห่ ง ช น ชั้ น น |",icon_url = BOT_ICON_URL)
    await ctx.send(embed = em)
@bot.command(aliases=["flipcoin"])
async def filpcoin(ctx,text=None,amount=None):
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

    bot_pick = random.randint(1,2)
    await ctx.send(bot_pick)
    if bot_pick == 1:
        await ctx.send("Head")
    else:
        await ctx.send("Tail")

    if int(text) == bot_pick:
        await update_bank(ctx.author, amount)
    else:
        await update_bank(ctx.author, -amount)
@bot.command()
async def ind(ctx,textA=None,textB=None):
    if textA is None or textB is None:
        await ctx.send("กรอกข้อมูลให้ครบไอเด็กเหี้ย")
        await ctx.send("1.ชื่อเล่น(แนะนำเป็นชื่อภาษาอังกฤษ) 2.อายุ ")
        return

    if not re.fullmatch(r"[A-Za-z]{1,7}", textA):
        await ctx.send("ชื่อเล่นมึงต้องมีแค่อังกฤษเท่านั้นไอเด็กเหี้ย")
        return

    if not textB.isdecimal():
        await ctx.send("ใส่ตัวเลขไอเด็กเหี้ย")
        return

    age = int(textB)
    if age < 1 or age > 120:
        await ctx.send("อายุต้องอยู่ระหว่าง 1-120")
        return

    time = (datetime.today().strftime("%d/%m/%Y"))
    rich = Image.open(asset_path("ID.jpg")).convert("RGBA")

    pfp = await avatar_image(ctx.author, size=128)
    pfp = pfp.resize((188,202))
    rich.paste(pfp,(54,102), pfp)
    draw = ImageDraw.Draw(rich)
    font = load_font(36)
    draw.text((54,331.4),ctx.author.name,(16,29,143),font = font)
    draw1 = ImageDraw.Draw(rich)
    font1 = load_font(30)
    draw1.text((155.86,387.42 ),textA,(16,29,143),font = font1)
    draw2 = ImageDraw.Draw(rich)
    font2 = load_font(30)
    draw2.text((133.72,425.42),textB,(16,29,143),font = font2)
    draw3 = ImageDraw.Draw(rich)
    font3 = load_font(15)
    draw3.text((12,480.96),time,(16,29,143),font = font3)
    await ctx.send(file=image_file(rich, "TID.png"))


@bot.command()
async def ping(ctx):
    await ctx.send(f"ping {round(bot.latency*1000)} ms")
@bot.command()
async def botinfo(ctx):
    em = discord.Embed(title = "ข้อมูลบอท",color = discord.Color.dark_blue())
    em.add_field(name = "ชื่อ",value = bot.user.name,inline = False)
    em.add_field(name = "รหัส",value = bot.user.id,inline = False)
    em.add_field(name = "รุ่น",value = getattr(bot.user, "discriminator", "0"),inline = False)
    em.add_field(name = "เวอร์ชั่น",value = "1.0.0",inline = False)
    em.add_field(name = "สถานะการทำงาน",value = "ทำงานได้อย่างเต็มที่",inline = False)
    await ctx.send(embed = em)

@bot.command()
async def serverinfo(ctx):
    if ctx.guild is None:
        await ctx.send("ใช้คำสั่งนี้ได้เฉพาะใน server")
        return

    em = discord.Embed(title = "ข้อมูลServer",color = discord.Color.dark_blue())
    em.add_field(name = "ชื่อ",value = ctx.guild.name,inline = False)
    em.add_field(name = "รหัส",value = ctx.guild.id,inline = False)
    em.add_field(name = "จำนวนผู้ใช้",value = ctx.guild.member_count,inline = False)
    icon_url = getattr(ctx.guild, "icon_url", None)
    if not icon_url:
        icon = getattr(ctx.guild, "icon", None)
        icon_url = str(icon) if icon else ""
    em.set_thumbnail(url = str(icon_url))
    await ctx.send(embed = em)
@bot.command()
async def TOKEN(ctx):
    await ctx.send("มึงอย่าแม้แต่จะคิด")
@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Game(name=f"{COMMAND_PREFIX}command"))
@bot.command()
async def github(ctx):
    await ctx.send("https://github.com/DEVPOB/BOBCOIN")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx,amount: int = 100):
    amount = max(1, min(amount, MAX_PURGE_MESSAGES))
    await ctx.send("https://tenor.com/view/thanos-just-the-snap-avengers-infinity-war-gif-12393235")
    await asyncio.sleep(1.3)
    await ctx.channel.purge(limit=amount)


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    token = os.getenv("DISCORD_TOKEN") or os.getenv("BOBCOIN_TOKEN")
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN or BOBCOIN_TOKEN before starting the bot.")
    bot.run(token)


if __name__ == "__main__":
    main()
