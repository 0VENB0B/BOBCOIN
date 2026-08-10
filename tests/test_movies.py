"""Tests for bobcoin.movies — the TMDb movie recommendation module.

The network layer (_tmdb_get) is never hit: _auth/tmdb_configured are env
pure, and recommend_movie's search/discover steps are mocked. Genres cover
the English + Thai aliases the bot advertises.
"""

import asyncio

import pytest

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
