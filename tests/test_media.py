"""Tests for bobcoin.cogs.media — the image-generation commands.

The asset lookup is redirected to a temp PNG (so tests never depend on the
repo's jpg sizes) and avatar downloads are faked, while the real PIL
paste/draw/save path still runs. The `ind` validation branches are tested
without touching images at all.
"""

import asyncio

import pytest
from conftest import invoke_command
from PIL import Image

import bobcoin.cogs.media as media


class _Avatar:
    url = "http://avatar.example/x.png"


class _Author:
    def __init__(self, uid, name="TestUser"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.display_avatar = _Avatar()


class _Ctx:
    def __init__(self, author):
        self.author = author
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


@pytest.fixture
def imgs(monkeypatch, tmp_path):
    """Redirect asset_path to a temp PNG + fake avatar downloads."""
    png = tmp_path / "asset.png"
    Image.new("RGBA", (800, 600), (255, 255, 255, 255)).save(png)
    monkeypatch.setattr(media, "asset_path", lambda name: str(png))

    async def _fake_avatar(member, size=128):
        return Image.new("RGBA", (256, 256), (200, 30, 30, 255))

    monkeypatch.setattr(media, "avatar_image", _fake_avatar)
    return png


def run(coro):
    return asyncio.run(coro)


def _sent_file(ctx):
    """Return the discord.File passed as `file=` kwarg of the first send."""
    assert ctx.sent, "expected at least one send"
    return ctx.sent[0][1].get("file")


def _sent_text(ctx, index=0):
    return ctx.sent[index][0][0]


# ── ind validation (no images touched) ────────────────────────────────

def test_ind_missing_args_shows_usage():
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "ind", ctx)
        assert "กรอกข้อมูลให้ครบ" in _sent_text(ctx, 0)
        assert ctx.sent[0][1].get("file") is None
    run(scenario())


def test_ind_rejects_non_english_name():
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "ind", ctx, "ชื่อไทย", "20")
        assert "ต้องมีแค่อังกฤษ" in _sent_text(ctx)
    run(scenario())


def test_ind_rejects_name_too_long():
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "ind", ctx, "ABCDEFGH", "20")
        assert "ต้องมีแค่อังกฤษ" in _sent_text(ctx)
    run(scenario())


def test_ind_rejects_non_numeric_age():
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "ind", ctx, "bob", "ยี่สิบ")
        assert "ใส่ตัวเลข" in _sent_text(ctx)
    run(scenario())


@pytest.mark.parametrize("age", ["0", "121"])
def test_ind_rejects_age_out_of_range(age):
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "ind", ctx, "bob", age)
        assert "อายุต้องอยู่ระหว่าง 1-120" in _sent_text(ctx)
    run(scenario())


# ── image pipelines ───────────────────────────────────────────────────

def test_ind_valid_builds_id_image(imgs):
    async def scenario():
        ctx = _Ctx(_Author(1, "bob"))
        await invoke_command(media.MediaCog(), "ind", ctx, "bob", "20")
        assert _sent_file(ctx).filename == "TID.png"
    run(scenario())


def test_stonk_builds_meme_image(imgs):
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "stonk", ctx)
        assert _sent_file(ctx).filename == "picture.png"
    run(scenario())


def test_dtc_truncates_long_text(imgs):
    async def scenario():
        ctx = _Ctx(_Author(1))
        # DTC has a keyword-only `text` param (name is uppercase in the cog)
        await invoke_command(media.MediaCog(), "DTC", ctx, text="x" * 600)
        assert _sent_file(ctx).filename == "text.png"
    run(scenario())


def test_dtc_default_text_when_empty(imgs):
    async def scenario():
        ctx = _Ctx(_Author(1))
        await invoke_command(media.MediaCog(), "DTC", ctx)
        assert _sent_file(ctx).filename == "text.png"
    run(scenario())
