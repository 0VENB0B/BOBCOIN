"""Tests for bobcoin.movies — the TMDb movie recommendation module.

The network layer (_tmdb_get) is never hit: _auth/tmdb_configured are env
pure, and recommend_movie's search/discover steps are mocked. Genres cover
the English + Thai aliases the bot advertises.
"""

import asyncio

import bobcoin.movies as movies


def run(coro):
    return asyncio.run(coro)


# ── genre map ─────────────────────────────────────────────────────────

def test_genre_map_has_aliases():
    assert movies._GENRE_IDS["sci-fi"] == 878
    assert movies._GENRE_IDS["scifi"] == 878
    assert movies._GENRE_IDS["science fiction"] == 878
    assert movies._GENRE_IDS["ไซไฟ"] == 878
    assert movies._GENRE_IDS["แอคชั่น"] == 28
    assert movies._GENRE_IDS["horror"] == 27
    assert movies._GENRE_IDS["สยองขวัญ"] == 27


# ── auth / configured ─────────────────────────────────────────────────

def test_tmdb_not_configured_without_env(monkeypatch):
    monkeypatch.delenv("TMDB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    assert movies.tmdb_configured() is False
    assert movies._auth() is None


def test_tmdb_configured_with_token(monkeypatch):
    monkeypatch.setenv("TMDB_ACCESS_TOKEN", "tok")
    headers, params = movies._auth()
    assert headers == {"Authorization": "Bearer tok"}
    assert params == {}


def test_tmdb_configured_with_api_key(monkeypatch):
    monkeypatch.delenv("TMDB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("TMDB_API_KEY", "key123")
    headers, params = movies._auth()
    assert headers == {}
    assert params == {"api_key": "key123"}


# ── result cleaning / building ────────────────────────────────────────

def test_clean_results_filters_junk():
    results = [
        {"id": 1, "title": "Good", "vote_average": 8.0},
        {"id": 2, "title": "No rating", "vote_average": 0},     # dropped
        {"id": None, "title": "No id", "vote_average": 7.0},    # dropped
        {"title": "No id at all", "vote_average": 7.0},         # dropped
        {"id": 3, "title": "", "vote_average": 7.0},            # empty title kept? no
    ]
    cleaned = movies._clean_results(results)
    assert len(cleaned) == 1
    assert cleaned[0]["id"] == 1


def test_to_recommendation_builds_fields():
    item = {
        "id": 157336,
        "title": "Interstellar",
        "original_title": "Interstellar",
        "overview": "ผ่านหลุมดำ",
        "release_date": "2014-11-05",
        "vote_average": 8.6,
        "vote_count": 30000,
        "poster_path": "/p.jpg",
    }
    rec = movies._to_recommendation(item, "TMDb Search: interstellar")
    assert rec.title == "Interstellar"
    assert rec.year == "2014"
    assert rec.rating == 8.6
    assert rec.vote_count == 30000
    assert rec.tmdb_url == "https://www.themoviedb.org/movie/157336"
    assert rec.poster_url == "https://image.tmdb.org/t/p/w500/p.jpg"
    assert rec.source_label == "TMDb Search: interstellar"


def test_to_recommendation_handles_missing_fields():
    rec = movies._to_recommendation(
        {"id": 9}, "TMDb Discover: x",
    )
    assert rec.title == "Unknown"
    assert rec.year == "ไม่ทราบปี"
    assert rec.poster_url is None
    assert rec.overview  # fallback blurb


# ── recommend_movie routing (network mocked) ──────────────────────────

def test_recommend_movie_uses_search_for_non_genre_query(monkeypatch):
    async def _search(query):
        assert query == "interstellar"
        return [{"id": 1, "title": "Interstellar", "vote_average": 8.6}], "S"
    async def _discover(*_a):
        raise AssertionError("discover must not run when search hits")
    monkeypatch.setattr(movies, "_search_movies", _search)
    monkeypatch.setattr(movies, "_discover_movies", _discover)

    async def scenario():
        rec = await movies.recommend_movie("interstellar")
        assert rec.title == "Interstellar"
    run(scenario())


def test_recommend_movie_uses_discover_for_genre(monkeypatch):
    async def _discover(query):
        assert query == "sci-fi"
        return [{"id": 2, "title": "Arrival", "vote_average": 7.9}], "D"
    async def _search_must_not_run(_q):
        raise AssertionError("must not search for a genre")
    monkeypatch.setattr(movies, "_search_movies", _search_must_not_run)
    monkeypatch.setattr(movies, "_discover_movies", _discover)
    monkeypatch.setattr(movies.random, "choice", lambda seq: seq[0])

    async def scenario():
        rec = await movies.recommend_movie("sci-fi")
        assert rec.title == "Arrival"
    run(scenario())


def test_recommend_movie_none_when_no_results(monkeypatch):
    async def _search(_q):
        return [], "S"
    async def _discover(*_a):
        return [], "D"
    monkeypatch.setattr(movies, "_search_movies", _search)
    monkeypatch.setattr(movies, "_discover_movies", _discover)

    async def scenario():
        assert await movies.recommend_movie("ghostbusters") is None
    run(scenario())


def test_recommend_movie_none_when_api_unavailable(monkeypatch):
    async def _search(_q):
        return None                       # _tmdb_get failed (no auth)
    async def _discover(*_a):
        return None
    monkeypatch.setattr(movies, "_search_movies", _search)
    monkeypatch.setattr(movies, "_discover_movies", _discover)

    async def scenario():
        assert await movies.recommend_movie("anything") is None
    run(scenario())


# ── _tmdb_get (network faked) ───────────────────────────────────────────

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
    """Records the request URL + params; returns a canned response."""

    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = payload if payload is not None else {"results": []}
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
    monkeypatch.setenv("TMDB_ACCESS_TOKEN", "tok")
    session = _FakeSession(**kw)
    monkeypatch.setattr(movies.aiohttp, "ClientSession", lambda **k: session)
    return session


def test_tmdb_get_returns_json_on_200(monkeypatch):
    session = _install_fake_session(monkeypatch, payload={"results": [{"id": 1}]})

    async def scenario():
        data = await movies._tmdb_get("/discover/movie", {"page": 1})
        assert data == {"results": [{"id": 1}]}
        url, kw = session.calls[0]
        assert url == "https://api.themoviedb.org/3/discover/movie"
        assert kw["headers"]["Authorization"] == "Bearer tok"
        assert kw["params"]["language"] == "th-TH"     # default language
    run(scenario())


def test_tmdb_get_returns_none_on_error_status(monkeypatch):
    monkeypatch.setattr(movies.logger, "warning", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, status=503)

    async def scenario():
        assert await movies._tmdb_get("/x", {}) is None
    run(scenario())


def test_tmdb_get_returns_none_on_network_error(monkeypatch):
    monkeypatch.setattr(movies.logger, "exception", lambda *a, **kw: None)
    _install_fake_session(monkeypatch, error=RuntimeError("boom"))

    async def scenario():
        assert await movies._tmdb_get("/x", {}) is None
    run(scenario())


def test_tmdb_get_returns_none_without_auth(monkeypatch):
    monkeypatch.delenv("TMDB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)

    async def scenario():
        assert await movies._tmdb_get("/x", {}) is None
    run(scenario())


# ── _discover_movies / _search_movies (routing faked) ───────────────────

def test_discover_movies_adds_genre_param(monkeypatch):
    seen = {}

    async def _fake_get(path, params):
        seen["path"] = path
        seen["params"] = params
        return {"results": [{"id": 5, "title": "Alien", "vote_average": 8.5}]}

    monkeypatch.setattr(movies, "_tmdb_get", _fake_get)
    monkeypatch.setattr(movies.random, "randint", lambda a, b: 3)

    async def scenario():
        results, source = await movies._discover_movies("sci-fi")
        assert seen["params"]["with_genres"] == 878
        assert seen["params"]["page"] == 3
        assert source == "TMDb Discover: sci-fi"
        assert results[0]["id"] == 5
    run(scenario())


def test_discover_movies_no_genre_when_unknown_query(monkeypatch):
    seen = {}

    async def _fake_get(path, params):
        seen["params"] = params
        return {"results": []}

    monkeypatch.setattr(movies, "_tmdb_get", _fake_get)

    async def scenario():
        results, source = await movies._discover_movies("ไม่ใช่แนว")
        assert "with_genres" not in seen["params"]
        assert source == "TMDb Discover"
        assert results == []
    run(scenario())


def test_discover_movies_none_when_api_fails(monkeypatch):
    async def _fake_get(path, params):
        return None

    monkeypatch.setattr(movies, "_tmdb_get", _fake_get)

    async def scenario():
        assert await movies._discover_movies(None) is None
    run(scenario())


def test_search_movies_passes_query(monkeypatch):
    seen = {}

    async def _fake_get(path, params):
        seen["path"] = path
        seen["params"] = params
        return {"results": [{"id": 2, "title": "Hit", "vote_average": 7.0}]}

    monkeypatch.setattr(movies, "_tmdb_get", _fake_get)

    async def scenario():
        results, source = await movies._search_movies("interstellar")
        assert seen["path"] == "/search/movie"
        assert seen["params"]["query"] == "interstellar"
        assert source == "TMDb Search: interstellar"
        assert results[0]["id"] == 2
    run(scenario())


def test_search_movies_none_when_api_fails(monkeypatch):
    async def _fake_get(path, params):
        return None

    monkeypatch.setattr(movies, "_tmdb_get", _fake_get)

    async def scenario():
        assert await movies._search_movies("x") is None
    run(scenario())


# ── recommend_movie routing extras ──────────────────────────────────────

def test_recommend_movie_falls_back_to_discover_when_search_empty(monkeypatch):
    async def _search(_q):
        return [], "S"
    async def _discover(*_a):
        return [{"id": 3, "title": "From Discover", "vote_average": 6.5}], "D"
    monkeypatch.setattr(movies, "_search_movies", _search)
    monkeypatch.setattr(movies, "_discover_movies", _discover)
    monkeypatch.setattr(movies.random, "choice", lambda seq: seq[0])

    async def scenario():
        rec = await movies.recommend_movie("Ghostbusters")
        assert rec is not None
        assert rec.title == "From Discover"
    run(scenario())


def test_recommend_movie_none_query_uses_discover(monkeypatch):
    async def _search_must_not_run(_q):
        raise AssertionError("no query → must not search")
    async def _discover(*_a):
        return [{"id": 7, "title": "X", "vote_average": 8.0}], "D"
    monkeypatch.setattr(movies, "_search_movies", _search_must_not_run)
    monkeypatch.setattr(movies, "_discover_movies", _discover)
    monkeypatch.setattr(movies.random, "choice", lambda seq: seq[0])

    async def scenario():
        rec = await movies.recommend_movie(None)
        assert rec.title == "X"
    run(scenario())


def test_recommend_movie_strips_whitespace_query(monkeypatch):
    async def _search(q):
        assert q == "interstellar"
        return [{"id": 1, "title": "Interstellar", "vote_average": 8.6}], "S"
    monkeypatch.setattr(movies, "_search_movies", _search)
    monkeypatch.setattr(movies.random, "choice", lambda seq: seq[0])

    async def scenario():
        rec = await movies.recommend_movie("  interstellar  ")
        assert rec.title == "Interstellar"
    run(scenario())
