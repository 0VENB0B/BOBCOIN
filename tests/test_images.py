"""Tests for bobcoin.images — the image helper module.

Covers asset_path, font loading (found + fallback), discord.File creation,
avatar download + RGBA conversion, and avatar URL resolution. Avatar reads are
faked so no network is ever touched.
"""

import asyncio
from io import BytesIO

from PIL import Image

import bobcoin.images as images


def run(coro):
    return asyncio.run(coro)


# ── asset_path ──────────────────────────────────────────────────────────

def test_asset_path_resolves_under_base_dir():
    p = images.asset_path("pic.jpg")
    assert p.name == "pic.jpg"
    assert str(images.BASE_DIR) in str(p)


# ── load_font ───────────────────────────────────────────────────────────

def test_load_font_returns_truetype_or_default():
    font = images.load_font(24)
    assert font is not None


def test_load_font_falls_back_to_default_when_all_missing(monkeypatch):
    def _boom(*_a, **_kw):
        raise OSError("no font")

    sentinel = object()
    monkeypatch.setattr(images.ImageFont, "truetype", _boom)
    monkeypatch.setattr(images.ImageFont, "load_default", lambda: sentinel)
    assert images.load_font(16) is sentinel


# ── image_file ──────────────────────────────────────────────────────────

def test_image_file_builds_png_file():
    img = Image.new("RGB", (4, 4), (10, 20, 30))
    f = images.image_file(img, "out.png")
    assert f.filename == "out.png"
    data = f.fp.read() if hasattr(f.fp, "read") else f.fp.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"        # PNG magic


# ── avatar_image / avatar_url ───────────────────────────────────────────

class _Avatar:
    def __init__(self, url="http://avatar.example/x.png"):
        self.url = url

    def __str__(self):
        return self.url

    def replace(self, size=None):
        return self

    async def read(self):
        buf = BytesIO()
        Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, format="PNG")
        return buf.getvalue()


class _Member:
    def __init__(self, with_avatar=True):
        self.display_avatar = _Avatar() if with_avatar else None
        self.avatar_url = "http://avatar.example/legacy.png"

    def avatar_url_as(self, size=None):
        return _Avatar(self.avatar_url)


def test_avatar_image_reads_and_converts_rgba():
    async def scenario():
        img = await images.avatar_image(_Member(), size=64)
        assert img.mode == "RGBA"
        assert img.size == (8, 8)
    run(scenario())


def test_avatar_image_uses_legacy_avatar_url_when_no_display_avatar():
    async def scenario():
        img = await images.avatar_image(_Member(with_avatar=False), size=64)
        assert img.mode == "RGBA"
    run(scenario())


def test_avatar_url_prefers_display_avatar():
    assert images.avatar_url(_Member()) == "http://avatar.example/x.png"


def test_avatar_url_falls_back_to_legacy_field():
    m = _Member(with_avatar=False)
    assert images.avatar_url(m) == "http://avatar.example/legacy.png"


def test_avatar_url_empty_when_nothing_available():
    class _Bare:
        pass

    assert images.avatar_url(_Bare()) == ""
