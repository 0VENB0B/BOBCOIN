import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

COMMAND_PREFIX = os.getenv("GUCOIN_PREFIX", "$")
BOT_OWNER_ID = int(os.getenv("GUCOIN_OWNER_ID", "836652703412387850"))


def _parse_id_set(value: str) -> frozenset[int]:
    ids: set[int] = set()
    for raw in value.split(","):
        raw = raw.strip()
        if raw.isdecimal():
            ids.add(int(raw))
    return frozenset(ids)


BOT_ADMIN_ROLE_IDS = _parse_id_set(os.getenv("GUCOIN_ADMIN_ROLE_IDS", ""))
MAX_BET = 1_000_000_000
MAX_PURGE_MESSAGES = 100

BOT_ICON_URL = "https://cdn.discordapp.com/attachments/865170212822319114/894807330313621535/Discord.png"
LIKE_ICON_URL = "https://image.similarpng.com/very-thumbnail/2020/06/Icon-like-button-transparent-PNG.png"
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=880963590289498142&permissions=268823616&scope=bot"

SLOT_SYMBOLS = ("🍎", "🍊", "🍐", "🍇", "🍋", "💎", "7️⃣", "💀")
SLOT_SPIN_FRAMES = ("🍎🍊🍐", "🍊🍇💀", "🍐🍎🍊", "💀🍇🍎", "🍇🍐💀", "💎7️⃣🍋", "7️⃣💎💀", "🍋🍎💎")

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


def get_token():
    return os.getenv("DISCORD_TOKEN") or os.getenv("GUCOIN_TOKEN") or os.getenv("BOBCOIN_TOKEN")
