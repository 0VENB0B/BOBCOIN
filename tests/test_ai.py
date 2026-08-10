"""Tests for bobcoin.ai — the AI chat helper.

call_ai is a no-op returning the fallback when no API key is configured,
which is what every offline/CI environment sees. The session is created
lazily and reused.
"""

import asyncio

import pytest

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
