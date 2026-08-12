"""Tests for bobcoin.components — the Components v2 command menu.

Covers CategoryButton construction + callback (success and error paths) and
CommandMenuView construction (valid category, invalid → fallback to main).
No real Discord connection needed — fake interactions only.
"""

import asyncio

import discord

import bobcoin.components as components


def run(coro):
    return asyncio.run(coro)


class _Response:
    def __init__(self):
        self.done = False
        self.edits = []
        self.messages = []

    def is_done(self):
        return self.done

    async def edit_message(self, **kw):
        self.edits.append(kw)

    async def send_message(self, content=None, **kw):
        self.messages.append((content, kw))


class _Followup:
    def __init__(self):
        self.messages = []

    async def send(self, content=None, **kw):
        self.messages.append((content, kw))


class _Interaction:
    def __init__(self):
        self.response = _Response()
        self.followup = _Followup()


# ── COMMAND_CATEGORIES content ──────────────────────────────────────────

def test_categories_expose_expected_keys():
    assert set(components.COMMAND_CATEGORIES) == {"main", "eco", "gamble", "fun", "info"}


def test_category_bodies_format_with_prefix():
    for _, (label, body) in components.COMMAND_CATEGORIES.items():
        assert label
        formatted = body.format(prefix="$")
        assert formatted  # formats without raising


# ── CategoryButton ──────────────────────────────────────────────────────

def test_category_button_current_is_primary_and_disabled():
    btn = components.CategoryButton("$", "eco", "eco")
    assert btn.disabled is True
    assert btn.style == discord.ButtonStyle.primary
    assert btn.prefix == "$"
    assert btn.category_key == "eco"


def test_category_button_other_is_secondary_enabled():
    btn = components.CategoryButton("$", "gamble", "main")
    assert btn.disabled is False
    assert btn.style == discord.ButtonStyle.secondary


def _current_title(view) -> str:
    """Extract the 'Current category: …' line from the trailing TextDisplay."""
    return list(view.children)[-1].content


def test_category_button_callback_edits_to_own_category():
    async def scenario():
        it = _Interaction()
        btn = components.CategoryButton("$", "fun", "main")
        await btn.callback(it)
        assert it.response.edits, "must edit the menu message"
        view = it.response.edits[0]["view"]
        assert isinstance(view, components.CommandMenuView)
        assert "Feature" in _current_title(view)     # the clicked category
    run(scenario())


def test_category_button_callback_error_sends_followup_when_done(monkeypatch):
    monkeypatch.setattr(components.logger, "exception", lambda *a, **kw: None)

    async def scenario():
        it = _Interaction()
        it.response.done = True

        async def _boom(**kw):
            raise RuntimeError("edit failed")

        it.response.edit_message = _boom
        btn = components.CategoryButton("$", "fun", "main")
        await btn.callback(it)
        assert it.response.messages == []          # done → followup path
        assert it.followup.messages, "must send error via followup"
        assert "เกิดข้อผิดพลาด" in it.followup.messages[0][0]
    run(scenario())


def test_category_button_callback_error_sends_direct_when_not_done(monkeypatch):
    monkeypatch.setattr(components.logger, "exception", lambda *a, **kw: None)

    async def scenario():
        it = _Interaction()
        it.response.done = False

        async def _boom(**kw):
            raise RuntimeError("edit failed")

        it.response.edit_message = _boom
        btn = components.CategoryButton("$", "fun", "main")
        await btn.callback(it)
        assert it.response.messages, "must send error via response"
        assert "เกิดข้อผิดพลาด" in it.response.messages[0][0]
    run(scenario())


# ── CommandMenuView ─────────────────────────────────────────────────────

def test_menu_view_builds_all_rows():
    view = components.CommandMenuView("$")
    assert "Home" in _current_title(view)          # default category
    children = list(view.children)
    assert len(children) == 5                      # text + separator + action row + sep + text
    buttons = children[2]
    labels = [item.label for item in buttons.children if getattr(item, "label", None)]
    assert labels == ["Home", "Economy", "Gamble", "Feature", "Info"]


def test_menu_view_falls_back_to_main_on_unknown_category():
    view = components.CommandMenuView("$", "nope")
    assert "Home" in _current_title(view)


def test_menu_view_marks_selected_button_disabled():
    view = components.CommandMenuView("$", "eco")
    assert "Economy" in _current_title(view)
    action_row = list(view.children)[2]
    for item in action_row.children:
        if item.category_key == "eco":
            assert item.disabled is True
            assert item.style == discord.ButtonStyle.primary
        else:
            assert item.disabled is False
