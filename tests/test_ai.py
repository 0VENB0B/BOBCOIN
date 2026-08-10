"""Tests for bobcoin.ai — the AI chat helper.

call_ai is a no-op returning the fallback when no API key is configured,
which is what every offline/CI environment sees. The session is created
lazily and reused.
"""

import asyncio

import bobcoin.ai as ai


def run(coro):
    return asyncio.run(coro)


def test_call_ai_returns_fallback_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    async def scenario():
        assert await ai.call_ai("sys", [], fallback="fb") == "fb"
        assert await ai.call_ai("sys", [{"role": "user", "content": "hi"}]) == ""
    run(scenario())


def test_call_ai_never_touches_network_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _boom(*a, **kw):
        raise AssertionError("must not create a session without a key")

    monkeypatch.setattr(ai, "_get_session", _boom)

    async def scenario():
        assert await ai.call_ai("sys", [], fallback="ok") == "ok"
    run(scenario())


def test_get_session_is_reused():
    async def scenario():
        s1 = ai._get_session()
        s2 = ai._get_session()
        assert s1 is s2
        await s1.close()
        assert ai._get_session() is not s1     # closed → new session
        await ai._session.close()
        ai._session = None
    run(scenario())


# ── call_ai with a configured API key (session faked) ───────────────────

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    async def json(self, **kw):
        return self._payload


class _FakeCtxMgr:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Records the request body; returns a canned response or raises."""

    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error
        self.sent = []

    def post(self, url, **kw):
        self.sent.append((url, kw))
        if self.error:
            raise self.error
        return _FakeCtxMgr(_FakeResp(self.payload))


def test_call_ai_success_returns_content(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    session = _FakeSession({"choices": [{"message": {"content": "  สวัสดีจ้า  "}}]})
    monkeypatch.setattr(ai, "_get_session", lambda: session)

    async def scenario():
        out = await ai.call_ai("sys", [{"role": "user", "content": "hi"}], fallback="fb")
        assert out == "สวัสดีจ้า"                    # stripped
        url, kw = session.sent[0]
        assert url == "https://gateway.9arm.co/v1/chat/completions"
        assert kw["headers"]["Authorization"] == "Bearer secret"
        assert kw["json"]["model"] == "qwen3.6-35b-a3b"
        assert kw["json"]["messages"][0]["role"] == "system"
        assert kw["json"]["messages"][1]["role"] == "user"
    run(scenario())


def test_call_ai_max_tokens_and_fallback_passthrough(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    session = _FakeSession({"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(ai, "_get_session", lambda: session)

    async def scenario():
        await ai.call_ai("sys", [], fallback="fb", max_tokens=42)
        assert session.sent[0][1]["json"]["max_tokens"] == 42
    run(scenario())


def test_call_ai_network_error_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr(ai.logger, "exception", lambda *a, **kw: None)
    session = _FakeSession({}, error=RuntimeError("boom"))
    monkeypatch.setattr(ai, "_get_session", lambda: session)

    async def scenario():
        assert await ai.call_ai("sys", [], fallback="fb") == "fb"
    run(scenario())


def test_call_ai_malformed_response_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setattr(ai.logger, "exception", lambda *a, **kw: None)
    session = _FakeSession({"weird": True})           # no choices key → KeyError
    monkeypatch.setattr(ai, "_get_session", lambda: session)

    async def scenario():
        assert await ai.call_ai("sys", [], fallback="fb") == "fb"
    run(scenario())
