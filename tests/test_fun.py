"""Tests for bobcoin.cogs.fun — the fun/feature commands.

Pure helpers (_answers_match/_safe_reward/_positive_number/_format_number/
_emojify_text/_get_ai_question/_build_quiz_data/_make_distractors) get full
edge-case coverage, the MCQ quiz command is exercised end-to-end with fake
views, and the $roblox recommendation command + invite embed are covered with
a faked recommendation source.
"""

import asyncio
import types

import discord
from conftest import invoke_command

import bobcoin.cogs.fun as fun
from bobcoin.bank import get_balance, open_account
from bobcoin.roblox import RobloxGame


class _Avatar:
    url = "http://avatar.example/x.png"


class _Author:
    def __init__(self, uid, name="QuizTaker"):
        self.id = uid
        self.name = name
        self.display_name = name
        self.mention = f"<@{uid}>"
        self.display_avatar = _Avatar()


class _Message:
    def __init__(self, content=""):
        self.content = content

    async def edit(self, **kw):
        return None


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        msg = _Message()
        self.sent.append((a, kw))
        return msg

    def typing(self):
        return _Typing()


class _Ctx:
    def __init__(self, uid):
        self.author = _Author(uid)
        self.channel = _Channel()
        self.prefix = "$"
        self.bot = None

    async def send(self, *a, **kw):
        msg = _Message()
        self.channel.sent.append((a, kw))
        return msg

    def typing(self):
        return _Typing()


class _Bot:
    """Fake bot used by the (unused-by-quiz-now) wait_for helper."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def wait_for(self, event, check=None, timeout=None):
        self.calls.append((event, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _Message(self.outcome)


def run(coro):
    return asyncio.run(coro)


def _cog_with(bot):
    return fun.FunCog(bot)


def _sent_text(ctx):
    return [a[0] for a, _kw in ctx.channel.sent if a]


# ── _answers_match ──────────────────────────────────────────────────────

def test_answers_match_exact_and_normalized():
    assert fun._answers_match("Banana", "banana")          # case-insensitive
    assert fun._answers_match("  hello   world ", "hello world")  # whitespace
    assert fun._answers_match("A", " a ")                  # trimmed


def test_answers_match_numeric_with_commas():
    assert fun._answers_match("1,234", "1234")
    assert fun._answers_match("1234", "1,234")


def test_answers_match_mismatches():
    assert not fun._answers_match("banana", "apple")
    assert not fun._answers_match("", "apple")             # empty expected
    assert not fun._answers_match("apple", "")             # empty given
    assert not fun._answers_match("", "")
    assert not fun._answers_match("12", "12.5")            # not both decimal
    assert not fun._answers_match("abc", "12")             # number vs text


# ── _safe_reward ───────────────────────────────────────────────────────

def test_safe_reward_clamps_and_falls_back():
    assert fun._safe_reward(1000) == 1000
    assert fun._safe_reward("1500") == 1500                # str accepted
    assert fun._safe_reward(100) == 500                    # clamped low
    assert fun._safe_reward(9000) == 2000                  # clamped high
    assert fun._safe_reward(None) == 1000                  # fallback
    assert fun._safe_reward("abc") == 1000                 # unparsable fallback


# ── _positive_number ───────────────────────────────────────────────────

def test_positive_number():
    assert fun._positive_number("12") == 12.0
    assert fun._positive_number("1,234.5") == 1234.5       # comma stripped
    assert fun._positive_number(7) == 7.0                  # int accepted
    assert fun._positive_number("0") is None
    assert fun._positive_number("-3") is None
    assert fun._positive_number("abc") is None
    assert fun._positive_number("") is None
    assert fun._positive_number(None) is None


# ── _format_number ─────────────────────────────────────────────────────

def test_format_number_strips_trailing_zeros():
    assert fun._format_number(10) == "10"
    assert fun._format_number(10.5) == "10.5"
    assert fun._format_number(10.0) == "10"
    assert fun._format_number(1234567.891) == "1,234,567.89"
    assert fun._format_number(0.5) == "0.5"


# ── _emojify_text ──────────────────────────────────────────────────────

def test_emojify_text_letters_digits_and_space():
    assert fun._emojify_text("ab") == (
        ":regional_indicator_a::regional_indicator_b:"
    )
    assert fun._emojify_text("12") == ":one::two:"
    assert fun._emojify_text("a 1") == (
        ":regional_indicator_a::heavy_minus_sign::one:"
    )


def test_emojify_text_punctuation_mapping():
    # NOTE: "x" is not here — it's a letter, so it maps to a regional
    # indicator before the punctuation table is consulted.
    for raw, expected in [
        ("!", "❗"), ("?", "❓"), ("+", "➕"), ("-", "➖"),
        ("*", "✖️"), ("/", "➗"), (".", "⏺️"),
        (",", "⏸️"), ("<", "◀️"), (">", "▶️"),
    ]:
        assert fun._emojify_text(raw) == expected, raw


def test_emojify_text_rejects_unsupported_and_overlong():
    assert fun._emojify_text("สวัสดี") is None             # non-latin char
    assert fun._emojify_text("a" * 2000) is None           # output > 1900 chars
    assert fun._emojify_text("") == ""                     # empty → empty string


# ── _get_ai_question ───────────────────────────────────────────────────

def test_get_ai_question_parses_json(monkeypatch):
    async def _fake_ai(*_a, **_kw):
        return '{"question":"1+1","answer":"2","reward":1000}'
    monkeypatch.setattr(fun, "call_ai", _fake_ai)

    async def scenario():
        q = await fun._get_ai_question()
        assert q == {"question": "1+1", "answer": "2", "reward": 1000}
    run(scenario())


def test_get_ai_question_bad_json_returns_none(monkeypatch):
    async def _fake_ai(*_a, **_kw):
        return "ไม่ใช่ JSON เลยแม้แต่นิดเดียว"
    monkeypatch.setattr(fun, "call_ai", _fake_ai)

    async def scenario():
        assert await fun._get_ai_question() is None
    run(scenario())


def test_get_ai_question_ai_error_returns_none(monkeypatch):
    async def _fake_ai(*_a, **_kw):
        return ""
    monkeypatch.setattr(fun, "call_ai", _fake_ai)

    async def scenario():
        assert await fun._get_ai_question() is None
    run(scenario())


# ── quiz helpers (MCQ) ─────────────────────────────────────────────────

def test_make_distractors_numeric():
    wrong = fun._make_distractors("10300", correct_numeric=10300)
    assert len(wrong) == 3
    assert "10300" not in wrong
    assert all(w.isdecimal() and int(w) > 0 for w in wrong)


def test_make_distractors_non_numeric_fallback():
    wrong = fun._make_distractors("คำตอบ")
    assert len(wrong) == 3
    assert "คำตอบ" not in wrong


def test_build_quiz_data_with_ai_options():
    q = {"question": "1+1", "answer": "2", "options": ["1", "2", "3", "4"], "reward": 1500}
    question, answer, options, reward = fun._build_quiz_data(q)
    assert (question, answer, reward) == ("1+1", "2", 1500)
    assert sorted(options) == ["1", "2", "3", "4"]


def test_build_quiz_data_missing_options_generates_distractors():
    q = {"question": "1+1", "answer": "2", "reward": 1000}
    question, answer, options, reward = fun._build_quiz_data(q)
    assert question == "1+1" and answer == "2" and reward == 1000
    assert len(options) == 4 and answer in options


def test_build_quiz_data_options_without_answer_are_rebuilt():
    q = {"question": "Q", "answer": "5", "options": ["1", "2", "3", "4"], "reward": 500}
    _, answer, options, _ = fun._build_quiz_data(q)
    assert answer in options and len(options) == 4


def test_build_quiz_data_clamps_options_to_four():
    q = {"question": "Q", "answer": "3", "options": ["1", "2", "3", "4", "5", "6"], "reward": 500}
    _, _ans, options, _ = fun._build_quiz_data(q)
    assert len(options) == 4 and "3" in options


def test_build_quiz_data_fallback_math(monkeypatch):
    values = iter([300, 10000, 300, 400, 500])      # a, b, then 3 distinct offsets

    def _fake_randint(a, b):
        return next(values)
    monkeypatch.setattr(fun.random, "randint", _fake_randint)

    question, answer, options, reward = fun._build_quiz_data(None)
    assert question == "300 + 10000"
    assert answer == "10300"
    assert reward == 500
    assert len(options) == 4 and "10300" in options


# ── quiz command (MCQ buttons) ─────────────────────────────────────────

class _FakeQuizView:
    def __init__(self, answer, options, player_id, correct=None, timed_out=False):
        self.correct = correct
        self.children = []
        self._t = timed_out

    async def wait(self):
        return self._t


def _quiz_view(correct=True, timed_out=False):
    def _factory(answer, options, player_id):
        return _FakeQuizView(answer, options, player_id, correct=correct, timed_out=timed_out)
    return _factory


def test_quiz_correct_answer_pays_reward(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)
    monkeypatch.setattr(fun, "_QuizView", _quiz_view(correct=True))

    async def scenario():
        store = __import__("bobcoin.bank.core", fromlist=["_db"])._db._store
        await open_account(_Author(1))
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [1000, 0]
        assert store["users/1"]["xp"] == 2           # reward//500 = 2 xp
        assert any("✅ ถูกต้อง" in t for t in _sent_text(ctx))
    run(scenario())


def test_quiz_wrong_answer_pays_nothing(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)
    monkeypatch.setattr(fun, "_QuizView", _quiz_view(correct=False))

    async def scenario():
        await open_account(_Author(1))
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("❌ ผิด" in t for t in _sent_text(ctx))
        assert any("**2**" in t for t in _sent_text(ctx))  # shows the answer
    run(scenario())


def test_quiz_timeout_shows_answer(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "2+2", "answer": "4", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)
    monkeypatch.setattr(fun, "_QuizView", _quiz_view(timed_out=True))

    async def scenario():
        await open_account(_Author(1))
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("หมดเวลา" in t and "**4**" in t for t in _sent_text(ctx))
    run(scenario())


def test_quiz_fallback_math_when_ai_fails(monkeypatch):
    async def _no_question(*_a, **_kw):
        return None
    monkeypatch.setattr(fun, "_get_ai_question", _no_question)
    monkeypatch.setattr(fun, "_QuizView", _quiz_view(correct=True))

    def _fake_randint(a, b):
        return 300 if b <= 800 else 10000           # a=300, b=10000
    monkeypatch.setattr(fun.random, "randint", _fake_randint)

    async def scenario():
        await open_account(_Author(1))
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [500, 0]   # fallback reward
        embeds = [kw.get("embed") for _a, kw in ctx.channel.sent if kw.get("embed")]
        assert any(e.description == "300 + 10000" for e in embeds)
    run(scenario())


def test_quiz_correct_but_unregistered_gets_nothing(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)
    monkeypatch.setattr(fun, "_QuizView", _quiz_view(correct=True))

    async def scenario():
        ctx = _Ctx(1)                               # no account opened
        await invoke_command(_cog_with(_Bot("")), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("ยังไม่มีบัญชี" in t for t in _sent_text(ctx))
    run(scenario())


# ── $roblox command ─────────────────────────────────────────────────────

def _game():
    return RobloxGame(
        name="DOORS",
        place_id=6516141723,
        universe_id=2440500124,
        genre_label="👻 สยองขวัญ",
        blurb="เดินเข้าอาคารผีสิง",
        creator="LSPLASH",
        playing=50000,
        visits=10000000000,
        favorites=500000,
        price=None,
        description="DOORS description",
        thumb_url=None,
        url="https://www.roblox.com/games/6516141723",
        source_label="curated",
    )


class _FakeRobloxView:
    def __init__(self, game, author_id, query):
        self.game = game
        self.author_id = author_id
        self.query = query


def test_roblox_command_sends_recommendation(monkeypatch):
    async def _recommend(query):
        assert query == "doors"
        return _game()
    monkeypatch.setattr(fun, "recommend_roblox_game", _recommend)
    monkeypatch.setattr(fun, "RobloxView", _FakeRobloxView)

    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "roblox", ctx, query="doors")
        em = ctx.channel.sent[0][1]["embed"]
        assert em.title == "🎮 DOORS"
        values = " ".join(f.value for f in em.fields)
        assert "50,000" in values                    # playing
        assert "10,000,000,000" in values            # visits
        view = ctx.channel.sent[0][1]["view"]
        assert view.author_id == 1 and view.query == "doors"
    run(scenario())


def test_roblox_command_none_game_replies_error(monkeypatch):
    async def _recommend(_q):
        return None
    monkeypatch.setattr(fun, "recommend_roblox_game", _recommend)

    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "roblox", ctx)
        assert any("หาแมพไม่เจอ" in t for t in _sent_text(ctx))
    run(scenario())


def test_build_invite_embed_mentions_friends():
    class _Friend:
        def __init__(self, uid):
            self.mention = f"<@{uid}>"

    class _Inviter:
        def __init__(self):
            self.display_name = "Boss"

    em = fun._build_invite_embed(_game(), _Inviter(), [_Friend(2), _Friend(3)])
    assert "ชวนมาเล่น **DOORS**" in em.title
    assert "<@2> <@3>" in em.description
    assert "50,000" in em.description                # live playing count shown


# ── $roblox interactive view ────────────────────────────────────────────

class _Resp:
    def __init__(self):
        self.deferred = None
        self.edits = []
        self.messages = []

    async def defer(self, **kw):
        self.deferred = kw

    async def edit_message(self, **kw):
        self.edits.append(kw)

    async def send_message(self, content=None, **kw):
        self.messages.append((content, kw))


class _RobloxInteraction:
    def __init__(self, user):
        self.user = user
        self.channel = _Channel()
        self.response = _Resp()
        self.edited = []

    async def edit_original_response(self, **kw):
        self.edited.append(kw)


def _rbx_view():
    return fun.RobloxView(_game(), 1, None)


def test_roblox_view_builds_controls_and_invite_picker():
    view = _rbx_view()
    labels = [getattr(c, "label", None) for c in view.children]
    assert "🔄 สุ่มแมพใหม่" in labels
    assert "👥 ชวนเพื่อน" in labels
    assert "🎮 เปิดเกม" in labels                     # url button
    assert any(getattr(c, "placeholder", "") == "📂 แนวเกม" for c in view.children)
    assert any(isinstance(c, discord.ui.Button) and c.style == discord.ButtonStyle.link for c in view.children)
    assert not any(isinstance(c, discord.ui.UserSelect) for c in view.children)

    view._invite_mode = True
    view._build()
    assert any(isinstance(c, discord.ui.UserSelect) for c in view.children)


def test_roblox_view_reroll_refreshes_embed(monkeypatch):
    async def _recommend(query, exclude=None):
        return _game()
    monkeypatch.setattr(fun, "recommend_roblox_game", _recommend)

    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(1))
        await view._on_reroll(it)
        assert it.response.deferred is not None
        assert it.edited and it.edited[0]["embed"].title == "🎮 DOORS"
        assert it.edited[0]["view"] is view
    run(scenario())


def test_roblox_view_genre_select_filters(monkeypatch):
    async def _recommend(query, exclude=None):
        assert query == "ต่อสู้"
        return _game()
    monkeypatch.setattr(fun, "recommend_roblox_game", _recommend)

    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(1))
        select = types.SimpleNamespace(values=["ต่อสู้"])
        await view._on_genre(it, select)
        assert view.query == "ต่อสู้"
        assert it.edited
    run(scenario())


def test_roblox_view_invite_toggles_friend_picker():
    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(1))
        await view._on_invite(it, None)
        assert view._invite_mode is True
        assert it.response.edits
        assert any(isinstance(c, discord.ui.UserSelect) for c in view.children)
    run(scenario())


def test_roblox_view_invite_posts_mentions_then_restores():
    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(1))
        select = types.SimpleNamespace(values=[_Author(2), _Author(3)])
        await view._on_friends(it, select)

        sent = it.channel.sent
        assert sent
        em = sent[0][1]["embed"]
        assert "<@2> <@3>" in em.description
        assert sent[0][1]["allowed_mentions"] is not None    # pings enabled
        join_view = sent[0][1]["view"]
        assert any(c.style == discord.ButtonStyle.link for c in join_view.children)
        assert view._invite_mode is False                     # picker closed
        assert it.edited                                     # original re-rendered
    run(scenario())


def test_roblox_view_owner_only_rejects_others():
    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(99))
        await view._on_reroll(it)
        assert it.response.messages                      # ephemeral rejection
        assert not it.edited
    run(scenario())


# ── $roblox redeem codes ───────────────────────────────────────────────

def _game_with_codes():
    from dataclasses import replace
    return replace(_game(), codes=("BIGNEWS", "FUDD10", "JCWK"))


def test_build_roblox_embed_shows_codes_field():
    em = fun._build_roblox_embed(_game_with_codes())
    values = " ".join(f.value for f in em.fields)
    assert "BIGNEWS" in values
    assert "FUDD10" in values


def test_build_roblox_embed_no_codes_field_without_codes():
    em = fun._build_roblox_embed(_game())
    values = " ".join(f.value for f in em.fields)
    assert "โค้ด" not in values


def test_build_roblox_embed_truncates_many_codes():
    from dataclasses import replace
    em = fun._build_roblox_embed(replace(_game(), codes=tuple(f"CODE{i}" for i in range(10))))
    values = " ".join(f.value for f in em.fields)
    assert "CODE0" in values and "CODE7" in values   # first 8 shown
    assert "CODE8" not in values
    assert "อีก 2 โค้ด" in values


def test_roblox_view_codes_button_shows_only_with_codes():
    view = fun.RobloxView(_game_with_codes(), 1, None)
    labels = [getattr(c, "label", None) for c in view.children]
    assert "🎁 โค้ดเกม" in labels
    assert "🎁 โค้ดเกม" not in [getattr(c, "label", None) for c in _rbx_view().children]


def test_roblox_view_codes_button_sends_ephemeral_codes():
    async def scenario():
        view = fun.RobloxView(_game_with_codes(), 1, None)
        it = _RobloxInteraction(_Author(1))
        await view._on_codes(it, None)
        assert it.response.messages
        _content, kw = it.response.messages[0]
        assert kw.get("ephemeral") is True
        assert "BIGNEWS" in kw["embed"].description
        assert "FUDD10" in kw["embed"].description
    run(scenario())


def test_roblox_view_codes_button_owner_only():
    async def scenario():
        view = fun.RobloxView(_game_with_codes(), 1, None)
        it = _RobloxInteraction(_Author(99))
        await view._on_codes(it, None)
        assert it.response.messages                      # ephemeral rejection
    run(scenario())


def test_roblox_view_codes_button_no_codes_reply():
    async def scenario():
        view = _rbx_view()
        it = _RobloxInteraction(_Author(1))
        await view._on_codes(it, None)
        assert it.response.messages
        content, _kw = it.response.messages[0]
        assert "ไม่มีโค้ด" in content
    run(scenario())


# ── _QuizView button logic ──────────────────────────────────────────────

def test_quiz_view_pick_correct_highlights():
    async def scenario():
        view = fun._QuizView("2", ["1", "2", "3", "4"], 1)
        it = _RobloxInteraction(_Author(1))
        button = next(c for c in view.children if c.label.split(" ", 1)[1] == "2")
        await button.callback(it)                        # partial(_pick, opt="2")
        assert view.correct is True
        assert view.picked == "2"
        assert all(c.disabled for c in view.children)
        assert any(c.style == discord.ButtonStyle.success for c in view.children)
        assert it.response.edits                         # message re-rendered
    run(scenario())


def test_quiz_view_pick_wrong_highlights_red():
    async def scenario():
        view = fun._QuizView("2", ["1", "2", "3", "4"], 1)
        it = _RobloxInteraction(_Author(1))
        wrong = next(c for c in view.children if c.label.split(" ", 1)[1] == "1")
        await wrong.callback(it)
        assert view.correct is False
        assert any(c.style == discord.ButtonStyle.danger for c in view.children)
        assert any(c.style == discord.ButtonStyle.success for c in view.children)  # answer revealed
    run(scenario())


def test_quiz_view_non_owner_rejected():
    async def scenario():
        view = fun._QuizView("2", ["1", "2", "3", "4"], 1)
        it = _RobloxInteraction(_Author(99))
        button = next(c for c in view.children if c.label.split(" ", 1)[1] == "2")
        await button.callback(it)
        assert it.response.messages                     # "ไม่ใช่โจทย์ของแก!"
        assert view.correct is None                     # no pick recorded
    run(scenario())


# ── emoji / geometry commands ─────────────────────────────────────────

def _texts(ctx):
    return [s[0][0] for s in ctx.channel.sent if s[0]]


def _geometry_embed(ctx):
    return ctx.channel.sent[0][1].get("embed")


def test_emoji_command_usage_and_length():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "emoji", ctx)          # missing
        assert any("ใช้แบบนี้" in t for t in _texts(ctx))
        await invoke_command(_cog_with(_Bot("")), "emoji", ctx, text="x" * 81)
        assert any("ยาวเกินไป" in t for t in _texts(ctx))
    run(scenario())


def test_emoji_command_sends_emojified():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "emoji", ctx, text="hi")
        assert ctx.channel.sent[-1][0][0] == ":regional_indicator_h::regional_indicator_i:"
    run(scenario())


def test_emoji_command_unsupported_chars():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "emoji", ctx, text="สวัสดี")
        assert any("รองรับตัวอักษร" in t for t in _texts(ctx))
    run(scenario())


def test_calr_rectangle_area():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "calr", ctx, "12", "5")
        em = _geometry_embed(ctx)
        assert em.title == "พื้นที่สี่เหลี่ยม"
        assert any("60" in f.value for f in em.fields)   # 12 × 5
    run(scenario())


def test_calr_invalid_input_shows_usage():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "calr", ctx)          # missing
        await invoke_command(_cog_with(_Bot("")), "calr", ctx, "x", "5")
        assert all("ใช้แบบนี้" in t for t in _texts(ctx))
    run(scenario())


def test_calt_triangle_area():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "calt", ctx, "10", "6")
        em = _geometry_embed(ctx)
        assert em.title == "พื้นที่สามเหลี่ยม"
        assert any("30" in f.value for f in em.fields)   # 10 × 6 ÷ 2
    run(scenario())


def test_calc_circle_area():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "calc", ctx, "7")
        em = _geometry_embed(ctx)
        assert em.title == "พื้นที่วงกลม"
        assert any("153.94" in f.value for f in em.fields)   # π×49
    run(scenario())


def test_calc_invalid_radius():
    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "calc", ctx, "-3")
        assert any("ใช้แบบนี้" in t for t in _texts(ctx))
    run(scenario())


def test_wait_command_sends_twice(monkeypatch):
    async def _fast(*_a):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)

    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "wait", ctx)
        assert _texts(ctx) == ["wait what", "wait what"]
    run(scenario())


# ── mrp (movie recommendation) ────────────────────────────────────────

def test_mrp_falls_back_to_builtin_list(monkeypatch):
    async def _no_movie(*_a, **_kw):
        return None
    monkeypatch.setattr(fun, "recommend_movie", _no_movie)
    monkeypatch.setattr(fun.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(fun, "tmdb_configured", lambda: False)

    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "mrp", ctx, query="อะไรดี")
        em = ctx.channel.sent[0][1]["embed"]
        assert em.title.startswith("หนังแนะนำ: ")
        assert any("TMDB_ACCESS_TOKEN" in f.value for f in em.fields)
    run(scenario())


def test_mrp_uses_tmdb_recommendation(monkeypatch):
    from bobcoin.movies import MovieRecommendation

    async def _movie(*_a, **_kw):
        return MovieRecommendation(
            title="Interstellar", original_title="Interstellar",
            overview="ผ่านหลุมดำ", year="2014", rating=8.6, vote_count=30000,
            tmdb_url="https://x/1", poster_url=None, source_label="TMDb Search: interstellar",
        )
    monkeypatch.setattr(fun, "recommend_movie", _movie)

    async def scenario():
        ctx = _Ctx(1)
        await invoke_command(_cog_with(_Bot("")), "mrp", ctx, query="interstellar")
        em = ctx.channel.sent[0][1]["embed"]
        assert em.title == "หนังแนะนำ: Interstellar"
        assert any("8.6/10" in f.value for f in em.fields)
    run(scenario())
