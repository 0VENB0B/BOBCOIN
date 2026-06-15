import discord
from discord import ui


COMMAND_CATEGORIES = {
    "main": (
        "Home",
        "## BOBCOIN Commands\n"
        "Prefix: `{prefix}`\n\n"
        "เลือกหมวดจากปุ่มด้านล่างเพื่อดูคำสั่งแบบ Components v2."
    ),
    "eco": (
        "Economy",
        "**Economy**\n"
        "`{prefix}Backpack` ดูยอดเงิน\n"
        "`{prefix}deposit <amount>` ฝากเงิน\n"
        "`{prefix}withdraw <amount>` ถอนเงิน\n"
        "`{prefix}lottery <5 digits> [amount]` หวย\n"
        "`{prefix}slot <amount>` สล็อต\n"
        "`{prefix}leaderboard [1-10]` อันดับเงิน\n"
        "`{prefix}filpcoin|flipcoin <1|2> <amount>` ทายหัวก้อย"
    ),
    "fun": (
        "Feature",
        "**Feature**\n"
        "`{prefix}emoji <text>` แปลงข้อความเป็น emoji\n"
        "`{prefix}mrp` แนะนำหนัง\n"
        "`{prefix}calR <width> <height>` พื้นที่สี่เหลี่ยม\n"
        "`{prefix}calT <base> <height>` พื้นที่สามเหลี่ยม\n"
        "`{prefix}calC <radius>` พื้นที่วงกลม\n"
        "`{prefix}QM` เกมคณิตคิดเร็ว"
    ),
    "media": (
        "Media",
        "**Media/Profile**\n"
        "`{prefix}DTC <text>` สร้างรูปข้อความ\n"
        "`{prefix}stonk [@user]` สร้างรูป stonk\n"
        "`{prefix}ind <name> <age>` สร้างบัตรโปรไฟล์\n"
        "`{prefix}profile [@user]` ดูโปรไฟล์"
    ),
    "info": (
        "Info",
        "**Info/Admin**\n"
        "`{prefix}ping` เช็ค latency\n"
        "`{prefix}botinfo` ข้อมูลบอท\n"
        "`{prefix}serverinfo` ข้อมูล server\n"
        "`{prefix}invite` ลิงก์เชิญบอท\n"
        "`{prefix}clear [1-100]` ลบข้อความ"
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
        await interaction.response.edit_message(
            view=CommandMenuView(self.prefix, self.category_key),
        )


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

