"""Tests for bobcoin.cogs.fun — the fun/feature commands.

Pure helpers (_answers_match/_safe_reward/_positive_number/_format_number/
_emojify_text/_get_ai_question) get full edge-case coverage, and the quiz
command is exercised end-to-end with a fake ctx + fake bot.wait_for:
AI-question path, fallback math, correct/wrong/timeout outcomes and the
unregistered-reward guard.
"""

import asyncio

from conftest import invoke_command

import bobcoin.cogs.fun as fun
from bobcoin.bank import get_balance, open_account


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


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))

    def typing(self):
        return _Typing()


class _Ctx:
    def __init__(self, uid):
        self.author = _Author(uid)
        self.channel = _Channel()
        self.prefix = "$"
        self.bot = None

    async def send(self, *a, **kw):
        self.channel.sent.append((a, kw))

    def typing(self):
        return _Typing()


class _Bot:
    """Fake bot whose wait_for returns a canned message or raises."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def wait_for(self, event, check=None, timeout=None):
        self.calls.append((event, timeout))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        msg = _Message(self.outcome)
        return msg


def run(coro):
    return asyncio.run(coro)


def _cog_with(bot):
    cog = fun.FunCog(bot)
    return cog


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


# ── quiz command ───────────────────────────────────────────────────────

def test_quiz_correct_answer_pays_reward(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)

    async def scenario():
        store = __import__("bobcoin.bank.core", fromlist=["_db"])._db._store
        await open_account(_Author(1))
        bot = _Bot("2")                              # player answers correctly
        ctx = _Ctx(1)
        await invoke_command(_cog_with(bot), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [1000, 0]
        assert store["users/1"]["xp"] == 2           # reward//500 = 2 xp
        assert any("✅ ถูกต้อง" in t for t in _sent_text(ctx))
    run(scenario())


def test_quiz_wrong_answer_pays_nothing(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)

    async def scenario():
        await open_account(_Author(1))
        bot = _Bot("3")                              # wrong
        ctx = _Ctx(1)
        await invoke_command(_cog_with(bot), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("❌ ผิด" in t for t in _sent_text(ctx))
        assert any("**2**" in t for t in _sent_text(ctx))  # shows the answer
    run(scenario())


def test_quiz_timeout_shows_answer(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "2+2", "answer": "4", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)

    async def scenario():
        await open_account(_Author(1))
        bot = _Bot(TimeoutError())
        ctx = _Ctx(1)
        await invoke_command(_cog_with(bot), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("หมดเวลา" in t and "**4**" in t for t in _sent_text(ctx))
    run(scenario())


def test_quiz_fallback_math_when_ai_fails(monkeypatch):
    async def _no_question(*_a, **_kw):
        return None
    monkeypatch.setattr(fun, "_get_ai_question", _no_question)

    def _fake_randint(a, b):
        return 300 if b <= 800 else 10000           # a=300, b=10000
    monkeypatch.setattr(fun.random, "randint", _fake_randint)

    async def scenario():
        await open_account(_Author(1))
        bot = _Bot("10300")                         # 300 + 10000
        ctx = _Ctx(1)
        await invoke_command(_cog_with(bot), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [500, 0]   # fallback reward
        embeds = [kw.get("embed") for _a, kw in ctx.channel.sent if kw.get("embed")]
        assert any(e.description == "300 + 10000" for e in embeds)
    run(scenario())


def test_quiz_correct_but_unregistered_gets_nothing(monkeypatch):
    async def _question(*_a, **_kw):
        return {"question": "1+1", "answer": "2", "reward": 1000}
    monkeypatch.setattr(fun, "_get_ai_question", _question)

    async def scenario():
        ctx = _Ctx(1)                               # no account opened
        bot = _Bot("2")
        await invoke_command(_cog_with(bot), "quiz", ctx)
        assert (await get_balance(_Author(1))) == [0, 0]
        assert any("ยังไม่มีบัญชี" in t for t in _sent_text(ctx))
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
