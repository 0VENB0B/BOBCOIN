import logging

import discord
from discord import ui

logger = logging.getLogger("bobcoin.components")


COMMAND_CATEGORIES = {
    "main": (
        "Home",
        "## 🪙 GUCOIN Commands\n"
        "Prefix: `{prefix}`\n\n"
        "เลือกหมวดจากปุ่มด้านล่างเพื่อดูคำสั่ง"
    ),
    "eco": (
        "Economy",
        "**💰 Economy**\n"
        "`{prefix}register` เปิดบัญชี GUCOIN *(ทำก่อนอื่น!)*\n"
        "`{prefix}balance` ดูยอดเงินในกระเป๋าและธนาคาร\n"
        "`{prefix}deposit <amount>` ฝากเงินเข้าธนาคาร\n"
        "`{prefix}withdraw <amount>` ถอนเงินจากธนาคาร\n"
        "`{prefix}give <@user> <amount>` โอนเงินให้คนอื่น *(ต้องมีบัญชีทั้งคู่)*\n"
        "`{prefix}leaderboard [1-10]` อันดับคนรวย\n"
        "`{prefix}history [@user]` ดูประวัติการเล่น/ธุรกรรม"
    ),
    "gamble": (
        "Gamble",
        "**🎰 Gambling**\n"
        "`{prefix}slot <amount>` สล็อต — 3 ตัวเหมือน = แจ็คพอต (8x/15x/20x)\n"
        "  └ 💀💀💀 = **Death Jackpot 20x** | 💎/7️⃣ = **Mega 15x**\n"
        "`{prefix}flip <1|2> <amount>` ทายหัวก้อย (1=หัว, 2=ก้อย) ชนะได้ 1.8x\n"
        "`{prefix}lottery <5 หลัก> [amount]` หวย 5 ตัว\n"
        "  └ ถูก 5 ตัว = **50x** | 4 ตัวท้าย = **8x** | 3 ตัวท้าย = **3x**\n"
        "`{prefix}bj <amount>` **Blackjack** — Hit/Stand กับ Dealer (ชนะ 1.8x, BJ 2.5x)\n"
        "`{prefix}rob <@user>` ปล้นคนอื่น (35% สำเร็จ, cooldown 2ชม./คู่)\n"
        "\n⚠️ เดิมพันได้สูงสุด **1,000,000,000** เหรียญ"
    ),
    "fun": (
        "Feature",
        "**🎮 Feature**\n"
        "`{prefix}emoji <text>` แปลงข้อความเป็น emoji\n"
        "`{prefix}mrp [ชื่อหนัง|genre]` แนะนำหนังจาก API\n"
        "`{prefix}calr <width> <height>` พื้นที่สี่เหลี่ยม\n"
        "`{prefix}calt <base> <height>` พื้นที่สามเหลี่ยม\n"
        "`{prefix}calc <radius>` พื้นที่วงกลม\n"
        "`{prefix}quiz` เกมคณิตคิดเร็ว"
    ),
    "info": (
        "Info",
        "**ℹ️ Info/Admin**\n"
        "`{prefix}ping` เช็ค latency\n"
        "`{prefix}botinfo` ข้อมูลบอท\n"
        "`{prefix}serverinfo` ข้อมูล server\n"
        "`{prefix}invite` ลิงก์เชิญบอท\n"
        "`{prefix}profile [@user]` ดูโปรไฟล์ Discord\n"
        "`{prefix}clear [1-100]` ลบข้อความ *(ต้องมีสิทธิ์)*"
    ),
}


class CategoryButton(ui.Button):
    def __init__(self, prefix, category_key, current_key):
        label, _ = COMMAND_CATEGORIES[category_key]
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary if category_key == current_key else discord.ButtonStyle.secondary,
            disabled=category_key == current_key,
        )
        self.prefix = prefix
        self.category_key = category_key

    async def callback(self, interaction):
        try:
            await interaction.response.edit_message(
                view=CommandMenuView(self.prefix, self.category_key),
            )
        except Exception:
            logger.exception("Button callback failed")
            if interaction.response.is_done():
                await interaction.followup.send("เกิดข้อผิดพลาด ลองใหม่อีกครั้ง", ephemeral=True)
            else:
                await interaction.response.send_message("เกิดข้อผิดพลาด ลองใหม่อีกครั้ง", ephemeral=True)


class CommandMenuView(ui.LayoutView):
    def __init__(self, prefix, category_key="main"):
        super().__init__(timeout=180)
        category_key = category_key if category_key in COMMAND_CATEGORIES else "main"
        title, body = COMMAND_CATEGORIES[category_key]

        self.add_item(ui.TextDisplay(body.format(prefix=prefix)))
        self.add_item(ui.Separator())

        row = ui.ActionRow()
        for key in COMMAND_CATEGORIES:
            row.add_item(CategoryButton(prefix, key, category_key))
        self.add_item(row)

        self.add_item(ui.Separator(visible=False))
        self.add_item(ui.TextDisplay(f"Current category: **{title}**"))
