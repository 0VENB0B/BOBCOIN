from io import BytesIO
from pathlib import Path

import discord
from PIL import Image, ImageFont

from .settings import BASE_DIR


def asset_path(name):
    return BASE_DIR / name


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

