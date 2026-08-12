"""Tests for bobcoin.roblox — Roblox top-map recommendations.

The network is never hit: _fetch_live_games / _fetch_thumb_url are either
monkeypatched or driven through a fake aiohttp session (mirroring test_movies).
"""

import asyncio

import bobcoin.roblox as roblox


def run(coro):
    return asyncio.run(coro)


def _clear_cache():
    roblox._live_cache.clear()


# ── genre resolution ──────────────────────────────────────────────────

def test_resolve_genre_bucket_labels_and_aliases():
    assert roblox._resolve_genre("ต่อสู้") == frozenset({"fighting"})
    assert roblox._resolve_genre("สยองขวัญ") == frozenset({"horror"})
    assert roblox._resolve_genre("จำลองชีวิต") == frozenset({"sim"})
    assert roblox._resolve_genre("ปริศนา") == frozenset({"obby"})
    assert roblox._resolve_genre("RPG") == frozenset({"rpg"})
    assert roblox._resolve_genre("horror") == frozenset({"horror"})
    assert roblox._resolve_genre("fps") == frozenset({"fighting"})
    assert roblox._resolve_genre("obby") == frozenset({"obby"})
    assert roblox._resolve_genre("adventure") == frozenset({"rpg"})


def test_resolve_genre_none_for_random_or_empty():
    assert roblox._resolve_genre(None) is None
    assert roblox._resolve_genre("") is None
    assert roblox._resolve_genre("   ") is None
    assert roblox._resolve_genre("สุ่ม") is None
    assert roblox._resolve_genre("random") is None
    assert roblox._resolve_genre("อะไรก็ได้") is None


def test_resolve_genre_unknown_text_is_empty_filter():
    assert roblox._resolve_genre("zzzz") == frozenset()


# ── recommend_roblox_game (network mocked) ────────────────────────────

async def _no_live(_ids):
    return {}


async def _no_thumb(_universe_id):
    return None


def _pick_first(seq):
    return seq[0]


def test_recommend_falls_back_to_curated(monkeypatch):
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game()
        assert game is not None
        assert game.name == "Adopt Me!"               # first curated entry
        assert game.source_label == "curated"
        assert game.playing is None
        assert game.universe_id == 383310974
        assert game.url == "https://www.roblox.com/games/920587237"
        assert game.genre_label                     # Thai display label
    run(scenario())


def test_recommend_uses_live_stats_when_available(monkeypatch):
    live = {
        383310974: {
            "id": 383310974,
            "rootPlaceId": 920587237,
            "name": "[2D TUESDAY] Adopt Me!",
            "description": "Adopt and raise pets",
            "playing": 150000,
            "visits": 44000000000,
            "favoritedCount": 5000000,
            "price": None,
            "creator": {"name": "Uplift Games"},
        }
    }

    async def _live(_ids):
        return live

    monkeypatch.setattr(roblox, "_fetch_live_games", _live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game()
        assert game.source_label == "live"
        assert game.name == "[2D TUESDAY] Adopt Me!"
        assert game.playing == 150000
        assert game.visits == 44000000000
        assert game.favorites == 5000000
        assert game.creator == "Uplift Games"
    run(scenario())


def test_recommend_filters_by_genre(monkeypatch):
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)

    picked_pools = []

    def _record(seq):
        picked_pools.append(list(seq))
        return seq[0]

    monkeypatch.setattr(roblox.random, "choice", _record)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("สยองขวัญ")
        assert game is not None
        assert picked_pools and all("horror" in g.tags for g in picked_pools[0])
    run(scenario())


def test_recommend_name_keyword_matches(monkeypatch):
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("doors")
        assert game is not None
        assert game.name == "DOORS"
    run(scenario())


def test_recommend_exclude_skips_current_map(monkeypatch):
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)

    picked_pools = []

    def _record(seq):
        picked_pools.append(list(seq))
        return seq[0]

    monkeypatch.setattr(roblox.random, "choice", _record)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game(exclude={383310974})
        assert game is not None
        assert picked_pools and 383310974 not in [g.universe_id for g in picked_pools[0]]
    run(scenario())


def test_recommend_genre_pool_returns_none_when_no_games(monkeypatch):
    """An unknown genre with zero matches falls back to the full pool — never None."""
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("แมพที่ไม่มีในลิสต์")
        assert game is not None                      # falls back to any map
    run(scenario())


def test_recommend_exclude_emptied_genre_pool_allows_repeats(monkeypatch):
    """Excluding every game of a genre must not return None — repeats allowed."""
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        horror_ids = {g.universe_id for g in roblox.ROBLOX_GAMES if "horror" in g.tags}
        game = await roblox.recommend_roblox_game("สยองขวัญ", exclude=horror_ids)
        assert game is not None
    run(scenario())


def test_recommend_ranks_by_live_popularity(monkeypatch):
    """With live data the pick comes from the TOP_K most-played pool."""
    live = {
        383310974: {"id": 383310974, "name": "Adopt Me!", "playing": 200000, "visits": 1},
        1686885941: {"id": 1686885941, "name": "Brookhaven RP", "playing": 150000, "visits": 1},
        994732206: {"id": 994732206, "name": "Blox Fruits", "playing": 100000, "visits": 1},
    }

    async def _live(_ids):
        return live

    monkeypatch.setattr(roblox, "_fetch_live_games", _live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)

    picked = []

    def _record(seq):
        picked.append(list(seq))
        return seq[0]

    monkeypatch.setattr(roblox.random, "choice", _record)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game()
        assert game is not None
        assert picked and len(picked[0]) <= roblox._TOP_K   # hot list, not full pool
        assert {g.universe_id for g in picked[0]} == {383310974, 1686885941, 994732206}
        assert game.name in {"Adopt Me!", "Brookhaven RP", "Blox Fruits"}  # from the hot list
    run(scenario())


def test_recommend_includes_curated_codes(monkeypatch):
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("blox fruits")
        assert game is not None and game.name == "Blox Fruits"
        assert "BIGNEWS" in game.codes
    run(scenario())


# ── redeem codes ───────────────────────────────────────────────────────

def test_merged_codes_external_wins_and_dedupes():
    entry = next(g for g in roblox.ROBLOX_GAMES if g.name == "Blox Fruits")
    merged = roblox._merged_codes(entry, {"blox fruits": ["FRESH", "BIGNEWS"]})
    assert merged[0] == "FRESH"                        # external first
    assert merged.count("BIGNEWS") == 1                # deduped
    assert "FUDD10" in merged                          # curated kept


def test_merged_codes_no_match_keeps_curated():
    entry = next(g for g in roblox.ROBLOX_GAMES if g.name == "DOORS")
    assert roblox._merged_codes(entry, {"Blox Fruits": ["X"]}) == ()


def test_fetch_external_codes_unset_url_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "")

    async def scenario():
        roblox._codes_cache.clear()
        assert await roblox._fetch_external_codes() == {}
    run(scenario())


def test_fetch_external_codes_parses_filters_and_caches(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "https://example.com/codes.json")
    session = _install_fake_session(monkeypatch, payload={"Blox Fruits": ["A1"], "Other": 42})

    async def scenario():
        roblox._codes_cache.clear()
        data = await roblox._fetch_external_codes()
        assert data == {"Blox Fruits": ["A1"]}       # non-list values dropped
        await roblox._fetch_external_codes()            # second call hits cache
        assert len(session.calls) == 1
    run(scenario())


def test_fetch_external_codes_non_dict_payload_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "https://example.com/codes.json")
    _install_fake_session(monkeypatch, payload=["not", "a", "dict"])

    async def scenario():
        roblox._codes_cache.clear()
        assert await roblox._fetch_external_codes() == {}
    run(scenario())


def test_fetch_external_codes_error_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "https://example.com/codes.json")
    monkeypatch.setattr(roblox.logger, "exception", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, error=RuntimeError("boom"))

    async def scenario():
        roblox._codes_cache.clear()
        assert await roblox._fetch_external_codes() == {}
    run(scenario())


def test_fetch_external_codes_status_error_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "https://example.com/codes.json")
    monkeypatch.setattr(roblox.logger, "warning", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, status=503)

    async def scenario():
        roblox._codes_cache.clear()
        assert await roblox._fetch_external_codes() == {}
    run(scenario())


def test_recommend_live_all_zero_playing_falls_back_to_flat_pool(monkeypatch):
    """Live data present but every map at 0 players → flat dice over the pool."""
    live = {383310974: {"id": 383310974, "name": "Adopt Me!", "playing": 0, "visits": 1}}

    async def _live(_ids):
        return live

    monkeypatch.setattr(roblox, "_fetch_live_games", _live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)

    picked = []

    def _record(seq):
        picked.append(list(seq))
        return seq[0]

    monkeypatch.setattr(roblox.random, "choice", _record)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game()
        assert game is not None
        assert picked and len(picked[0]) == len(roblox.ROBLOX_GAMES)   # full pool, not hot list
    run(scenario())


def test_external_codes_merge_into_recommendation(monkeypatch):
    monkeypatch.setattr(roblox.settings, "ROBLOX_CODES_URL", "https://example.com/codes.json")
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)
    _install_fake_session(monkeypatch, payload={"Blox Fruits": ["NEWCODE", "BIGNEWS"]})

    async def scenario():
        roblox._codes_cache.clear()
        _clear_cache()
        game = await roblox.recommend_roblox_game("blox fruits")
        assert game is not None
        assert game.codes[0] == "NEWCODE"                 # external wins
        assert game.codes.count("BIGNEWS") == 1           # deduped
    run(scenario())


# ── web-search redeem codes ─────────────────────────────────────────────

def test_web_search_configured_variants(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "")
    assert roblox._web_search_configured() is False
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    assert roblox._web_search_configured() is False          # needs both
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    assert roblox._web_search_configured() is True
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "b")
    assert roblox._web_search_configured() is True


def test_clean_search_name_strips_brackets():
    assert roblox._clean_search_name("[2D TUESDAY] Adopt Me!") == "Adopt Me!"
    assert roblox._clean_search_name("DOORS 👁️") == "DOORS 👁️"
    assert roblox._clean_search_name("  Blox   Fruits ") == "Blox Fruits"


def test_looks_like_code_heuristics():
    banned = {"roblox", "blox", "fruits", "xp"}
    assert roblox._looks_like_code("BIGNEWS", banned)
    assert roblox._looks_like_code("3BVISITS", banned)
    assert roblox._looks_like_code("fudd10_v2", banned)
    assert roblox._looks_like_code("Sub2Daigrock", banned)
    assert not roblox._looks_like_code("working", banned)    # lowercase word
    assert not roblox._looks_like_code("August", banned)     # one capital, no digits
    assert not roblox._looks_like_code("2026", banned)       # short number
    assert not roblox._looks_like_code("blox", banned)       # banned word
    assert not roblox._looks_like_code("xp", banned)         # stopword


def test_extract_codes_finds_loud_tokens_frequency_ranked():
    results = [
        {"title": "Blox Fruits Codes (August 2026) — 3BVISITS, BIGNEWS",
         "snippet": "Active codes: BIGNEWS 3BVISITS JCWK fudd10_v2 Sub2Daigrock"},
        {"title": "New working Blox Fruits codes",
         "snippet": "redeem BIGNEWS for free XP"},
    ]
    codes = roblox._extract_codes("Blox Fruits", results)
    for want in ("BIGNEWS", "3BVISITS", "JCWK", "fudd10_v2", "Sub2Daigrock"):
        assert want in codes, want
    assert codes[0] == "BIGNEWS"                       # frequency-ranked first
    for banned in ("working", "blox", "fruits", "August", "xp", "active"):
        assert banned not in codes, banned


def test_extract_codes_empty_results():
    assert roblox._extract_codes("Blox Fruits", []) == ()
    assert roblox._extract_codes("Blox Fruits", [{"title": "", "snippet": ""}]) == ()


def test_search_codes_caches_per_name(monkeypatch):
    async def _google(_q):
        return [{"title": "t", "snippet": "BIGNEWS JCWK"}]
    monkeypatch.setattr(roblox, "_web_search", _google)

    async def scenario():
        roblox._search_cache.clear()
        first = await roblox._search_codes("Blox Fruits")
        assert "BIGNEWS" in first
        calls = []

        async def _empty(_q):
            calls.append(1)
            return []

        monkeypatch.setattr(roblox, "_web_search", _empty)
        assert await roblox._search_codes("Blox Fruits") == first   # cache hit
        assert not calls
    run(scenario())


def test_search_codes_ttl_positive_and_empty(monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(roblox.time, "time", lambda: clock["now"])

    async def _google(_q):
        return [{"title": "t", "snippet": "BIGNEWS"}]
    monkeypatch.setattr(roblox, "_web_search", _google)

    async def scenario():
        roblox._search_cache.clear()
        await roblox._search_codes("Blox Fruits")            # positive → cached 24h
        calls = []

        async def _spy(_q):
            calls.append(1)
            return []

        monkeypatch.setattr(roblox, "_web_search", _spy)
        clock["now"] += 23 * 3600                             # within 24h → cached
        await roblox._search_codes("Blox Fruits")
        assert not calls

        clock["now"] += 2 * 3600                              # past 24h → refetch (empty)
        assert await roblox._search_codes("Blox Fruits") == ()
        assert calls

        calls.clear()                                          # empty cached only 10 min
        clock["now"] += 5 * 60
        await roblox._search_codes("Blox Fruits")
        assert not calls
        clock["now"] += 6 * 60                                # past empty TTL → refetch
        await roblox._search_codes("Blox Fruits")
        assert calls
    run(scenario())


def test_web_search_google_path(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "")
    session = _install_fake_session(
        monkeypatch,
        payload={"items": [{"title": "T", "link": "L", "snippet": "S"}]},
    )

    async def scenario():
        results = await roblox._web_search("Blox Fruits codes")
        assert results == [{"title": "T", "snippet": "S"}]
        url, kw = session.calls[0]
        assert url == roblox._GOOGLE_CSE_API
        assert kw["params"]["key"] == "k"
        assert kw["params"]["cx"] == "c"
        assert kw["params"]["q"] == "Blox Fruits codes"
    run(scenario())


def test_web_search_brave_fallback(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "b")
    session = _install_fake_session(
        monkeypatch,
        payload={"web": {"results": [{"title": "T", "url": "U", "description": "S"}]}},
    )

    async def scenario():
        results = await roblox._web_search("q")
        assert results == [{"title": "T", "snippet": "S"}]
        url, kw = session.calls[0]
        assert url == roblox._BRAVE_API
        assert kw["headers"]["X-Subscription-Token"] == "b"
    run(scenario())


def test_web_search_google_empty_items_falls_through_to_brave(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "b")
    session = _install_fake_session(monkeypatch, payload={"items": []})

    async def scenario():
        assert await roblox._web_search("q") == []
        assert len(session.calls) == 2                       # Google empty → Brave tried
        assert session.calls[0][0] == roblox._GOOGLE_CSE_API
        assert session.calls[1][0] == roblox._BRAVE_API
    run(scenario())


def test_web_search_google_error_falls_through_to_brave(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "b")
    monkeypatch.setattr(roblox.logger, "warning", lambda *a, **kw: None)

    class _SeqSession:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kw):
            self.calls.append((url, kw))
            if url == roblox._GOOGLE_CSE_API:
                return _FakeCtxMgr(_FakeResp(403, {}))
            return _FakeCtxMgr(_FakeResp(200, {"web": {"results": [{"title": "T", "url": "U", "description": "S"}]}}))

    session = _SeqSession()
    monkeypatch.setattr(roblox.aiohttp, "ClientSession", lambda **k: session)

    async def scenario():
        results = await roblox._web_search("q")
        assert results == [{"title": "T", "snippet": "S"}]   # recovered via Brave
        assert session.calls[0][0] == roblox._GOOGLE_CSE_API
        assert session.calls[1][0] == roblox._BRAVE_API
    run(scenario())


def test_web_search_status_error_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "")
    monkeypatch.setattr(roblox.logger, "warning", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, status=403)

    async def scenario():
        assert await roblox._web_search("q") == []
    run(scenario())


def test_web_search_exception_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox.settings, "BRAVE_API_KEY", "")
    monkeypatch.setattr(roblox.logger, "exception", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, error=RuntimeError("boom"))

    async def scenario():
        assert await roblox._web_search("q") == []
    run(scenario())


def test_recommend_searches_codes_when_configured(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def _search(name):
        assert name == "Adopt Me!"                       # API-reported name used
        return ("FRESH1", "FRESH2")

    monkeypatch.setattr(roblox, "_search_codes", _search)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("adopt me")
        assert game.codes == ("FRESH1", "FRESH2")
    run(scenario())


def test_recommend_search_empty_falls_back_to_curated(monkeypatch):
    monkeypatch.setattr(roblox.settings, "GOOGLE_API_KEY", "k")
    monkeypatch.setattr(roblox.settings, "GOOGLE_CSE_ID", "c")
    monkeypatch.setattr(roblox, "_fetch_live_games", _no_live)
    monkeypatch.setattr(roblox, "_fetch_thumb_url", _no_thumb)
    monkeypatch.setattr(roblox.random, "choice", _pick_first)

    async def _search(_name):
        return ()

    monkeypatch.setattr(roblox, "_search_codes", _search)

    async def scenario():
        _clear_cache()
        game = await roblox.recommend_roblox_game("blox fruits")
        assert game.codes and "BIGNEWS" in game.codes       # curated fallback
    run(scenario())


# ── _fetch_live_games (network faked) ─────────────────────────────────

class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
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
    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = payload if payload is not None else {"data": []}
        self.error = error
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.error:
            raise self.error
        return _FakeCtxMgr(_FakeResp(self.status, self.payload))


def _install_fake_session(monkeypatch, **kw):
    session = _FakeSession(**kw)
    monkeypatch.setattr(roblox.aiohttp, "ClientSession", lambda **k: session)
    return session


def test_fetch_live_games_parses_and_caches(monkeypatch):
    session = _install_fake_session(
        monkeypatch,
        payload={"data": [{"id": 383310974, "name": "Adopt Me!", "playing": 5}]},
    )

    async def scenario():
        _clear_cache()
        data = await roblox._fetch_live_games([383310974, 383310974])
        assert data[383310974]["name"] == "Adopt Me!"
        url, kw = session.calls[0]
        assert url == roblox._GAMES_API
        assert "383310974" in kw["params"]["universeIds"]
        # second call hits the cache — no new HTTP request
        await roblox._fetch_live_games([383310974])
        assert len(session.calls) == 1
    run(scenario())


def test_fetch_live_games_error_status_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.logger, "warning", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, status=503)

    async def scenario():
        _clear_cache()
        assert await roblox._fetch_live_games([1]) == {}
    run(scenario())


def test_fetch_live_games_network_error_returns_empty(monkeypatch):
    monkeypatch.setattr(roblox.logger, "exception", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, error=RuntimeError("boom"))

    async def scenario():
        _clear_cache()
        assert await roblox._fetch_live_games([1]) == {}
    run(scenario())


def test_fetch_live_games_skips_malformed_entries(monkeypatch):
    _install_fake_session(
        monkeypatch,
        payload={"data": [{"id": 1, "name": "ok"}, {"id": "bad"}]},
    )

    async def scenario():
        _clear_cache()
        data = await roblox._fetch_live_games([1, 2])
        assert data == {1: {"id": 1, "name": "ok"}}
    run(scenario())


def test_fetch_thumb_url_returns_image_url(monkeypatch):
    session = _install_fake_session(
        monkeypatch,
        payload={"data": [{"targetId": 1, "imageUrl": "https://tr.rbxcdn.com/x.png"}]},
    )

    async def scenario():
        url = await roblox._fetch_thumb_url(1)
        assert url == "https://tr.rbxcdn.com/x.png"
        assert session.calls[0][1]["params"]["universeIds"] == 1
    run(scenario())


def test_fetch_thumb_url_empty_or_error(monkeypatch):
    _install_fake_session(monkeypatch, payload={"data": []})

    async def scenario():
        assert await roblox._fetch_thumb_url(1) is None
    run(scenario())
