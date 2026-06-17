import logging
import os
import random
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger("bobcoin.movies")

_TMDB_API_BASE = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
_TMDB_WEB_BASE = "https://www.themoviedb.org/movie"

_GENRE_IDS = {
    "action": 28,
    "adventure": 12,
    "animation": 16,
    "comedy": 35,
    "crime": 80,
    "documentary": 99,
    "drama": 18,
    "family": 10751,
    "fantasy": 14,
    "history": 36,
    "horror": 27,
    "music": 10402,
    "mystery": 9648,
    "romance": 10749,
    "sci-fi": 878,
    "scifi": 878,
    "science fiction": 878,
    "thriller": 53,
    "war": 10752,
    "western": 37,
    "แอคชั่น": 28,
    "ผจญภัย": 12,
    "การ์ตูน": 16,
    "ตลก": 35,
    "อาชญากรรม": 80,
    "สารคดี": 99,
    "ดราม่า": 18,
    "ครอบครัว": 10751,
    "แฟนตาซี": 14,
    "ประวัติศาสตร์": 36,
    "สยอง": 27,
    "สยองขวัญ": 27,
    "เพลง": 10402,
    "ลึกลับ": 9648,
    "โรแมนซ์": 10749,
    "รัก": 10749,
    "ไซไฟ": 878,
    "ระทึกขวัญ": 53,
    "สงคราม": 10752,
}


@dataclass(slots=True)
class MovieRecommendation:
    title: str
    original_title: str
    overview: str
    year: str
    rating: float
    vote_count: int
    tmdb_url: str
    poster_url: str | None
    source_label: str


def _auth() -> tuple[dict[str, str], dict[str, str]] | None:
    token = os.getenv("TMDB_ACCESS_TOKEN") or os.getenv("TMDB_BEARER_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}, {}

    api_key = os.getenv("TMDB_API_KEY")
    if api_key:
        return {}, {"api_key": api_key}

    return None


def tmdb_configured() -> bool:
    return _auth() is not None


async def _tmdb_get(path: str, params: dict[str, str | int | float]) -> dict | None:
    auth = _auth()
    if auth is None:
        return None

    headers, auth_params = auth
    request_params = {
        "language": os.getenv("TMDB_LANGUAGE", "th-TH"),
        "region": os.getenv("TMDB_REGION", "TH"),
        **auth_params,
        **params,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{_TMDB_API_BASE}{path}", headers=headers, params=request_params) as resp:
                if resp.status >= 400:
                    logger.warning("TMDb request failed: %s %s", path, resp.status)
                    return None
                return await resp.json(content_type=None)
    except Exception:
        logger.exception("TMDb request failed: %s", path)
        return None


def _clean_results(results: list[dict]) -> list[dict]:
    return [
        item for item in results
        if item.get("id") and item.get("title") and float(item.get("vote_average") or 0) > 0
    ]


async def _discover_movies(query: str | None) -> tuple[list[dict], str] | None:
    params: dict[str, str | int | float] = {
        "include_adult": "false",
        "include_video": "false",
        "sort_by": "vote_average.desc",
        "vote_count.gte": 500,
        "page": random.randint(1, 5),
    }
    source = "TMDb Discover"

    genre_id = _GENRE_IDS.get((query or "").strip().lower())
    if genre_id:
        params["with_genres"] = genre_id
        source = f"TMDb Discover: {query}"

    data = await _tmdb_get("/discover/movie", params)
    if not data:
        return None
    return _clean_results(data.get("results", [])), source


async def _search_movies(query: str) -> tuple[list[dict], str] | None:
    data = await _tmdb_get(
        "/search/movie",
        {
            "query": query,
            "include_adult": "false",
            "page": 1,
        },
    )
    if not data:
        return None
    return _clean_results(data.get("results", [])), f"TMDb Search: {query}"


def _to_recommendation(item: dict, source_label: str) -> MovieRecommendation:
    release_date = str(item.get("release_date") or "")
    poster_path = item.get("poster_path")
    return MovieRecommendation(
        title=str(item.get("title") or item.get("original_title") or "Unknown"),
        original_title=str(item.get("original_title") or item.get("title") or "Unknown"),
        overview=str(item.get("overview") or "ยังไม่มีเรื่องย่อจาก API"),
        year=release_date[:4] if len(release_date) >= 4 else "ไม่ทราบปี",
        rating=float(item.get("vote_average") or 0),
        vote_count=int(item.get("vote_count") or 0),
        tmdb_url=f"{_TMDB_WEB_BASE}/{item['id']}",
        poster_url=f"{_TMDB_IMAGE_BASE}{poster_path}" if poster_path else None,
        source_label=source_label,
    )


async def recommend_movie(query: str | None = None) -> MovieRecommendation | None:
    query = query.strip() if query else None
    result_pack = None

    if query and _GENRE_IDS.get(query.lower()) is None:
        result_pack = await _search_movies(query)

    if not result_pack:
        result_pack = await _discover_movies(query)

    if not result_pack:
        return None

    results, source_label = result_pack
    if not results:
        return None

    return _to_recommendation(random.choice(results[:10]), source_label)
