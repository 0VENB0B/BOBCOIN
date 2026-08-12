"""Roblox top-map recommendations.

Two public, keyless Roblox APIs are used to enrich a curated list of the most
popular experiences:

- ``games.roblox.com/v1/games?universeIds=...``  → live playing/visits/votes
- ``thumbnails.roblox.com/v1/games/icons``       → game thumbnail

Both are optional: when they fail (offline, rate-limited, Roblox down) the bot
falls back to the curated list alone, mirroring the ``movies.py`` pattern.
Every entry below was verified against the live API (name/creator match), so
universe IDs never change once a game is created.
"""

import logging
import random
import re
import time
from dataclasses import dataclass, field

import aiohttp

from . import settings

logger = logging.getLogger("bobcoin.roblox")

_GAMES_API = "https://games.roblox.com/v1/games"
_THUMB_API = "https://thumbnails.roblox.com/v1/games/icons"

# Live game stats are cached briefly — repeated $roblox calls / rerolls must
# not hammer Roblox's API for data that barely moves within minutes.
_CACHE_TTL = 300  # seconds
_CODES_TTL = 600  # seconds (external codes source refreshes slower)
_live_cache: dict[tuple[int, ...], tuple[float, dict[int, dict]]] = {}
_codes_cache: dict[str, tuple[float, dict[str, list[str]]]] = {}

_GOOGLE_CSE_API = "https://www.googleapis.com/customsearch/v1"
_BRAVE_API = "https://api.search.brave.com/res/v1/web/search"
# Web-searched codes change slowly — cache per game name a full day; empty
# results are cached only briefly so a transient search outage self-heals.
_SEARCH_TTL = 24 * 3600
_SEARCH_EMPTY_TTL = 600
_search_cache: dict[str, tuple[float, tuple[str, ...]]] = {}

# When live stats are available the recommendation is data-driven: pick
# randomly among the TOP_K most-played maps of the chosen genre instead of
# rolling a flat dice over the whole list.
_TOP_K = 5


# ── Genre buckets (used by the filter select + $roblox <แนวเกม>) ──────────

GENRE_BUCKETS: dict[str, frozenset[str]] = {
    "จำลองชีวิต": frozenset({"sim"}),
    "ต่อสู้": frozenset({"fighting"}),
    "สยองขวัญ": frozenset({"horror"}),
    "ปริศนา/Obby": frozenset({"obby"}),
    "RPG/ผจญภัย": frozenset({"rpg"}),
}

_GENRE_ALIASES = {
    "จำลองชีวิต": "sim", "life": "sim", "roleplay": "sim", "simulation": "sim", "rp": "sim", "บ้าน": "sim",
    "ต่อสู้": "fighting", "fight": "fighting", "combat": "fighting", "pvp": "fighting", "ยิง": "fighting", "shooter": "fighting", "fps": "fighting",
    "สยองขวัญ": "horror", "horror": "horror", "scary": "horror", "ผี": "horror", "หลอน": "horror",
    "ปริศนา": "obby", "obby": "obby", "obstacle": "obby", "ท้าทาย": "obby", "course": "obby",
    "rpg": "rpg", "ผจญภัย": "rpg", "adventure": "rpg", "เกมส์เรื่อง": "rpg",
}


@dataclass(slots=True)
class _Entry:
    name: str
    place_id: int
    universe_id: int
    tags: frozenset[str]
    genre_label: str
    blurb: str
    creator: str
    codes: tuple[str, ...] = field(default_factory=tuple)


# Curated "top maps" — universe/place IDs verified live (Aug 2026). Codes are
# last-known redeem codes (they expire!) — the owner can keep them fresh via
# GUCOIN_ROBLOX_CODES_URL, which merges over these.
ROBLOX_GAMES: tuple[_Entry, ...] = (
    _Entry("Adopt Me!", 920587237, 383310974, frozenset({"sim", "rpg"}), "🏠 จำลองชีวิต / RPG",
           "เลี้ยงสัตว์สุดน่ารัก แลกเพตหายาก สร้างบ้านในฝัน และเล่น roleplay กับเพื่อน",
           "Uplift Games"),
    _Entry("Brookhaven RP", 4924922222, 1686885941, frozenset({"sim"}), "🏠 จำลองชีวิต",
           "เมืองจำลองชีวิต แต่งบ้าน แต่งตัว ขับรถ เที่ยว ใช้ชีวิตแบบที่อยากเป็น",
           "Brookhaven by Voldex"),
    _Entry("Blox Fruits", 2753915549, 994732206, frozenset({"fighting", "rpg"}), "⚔️ ต่อสู้ / RPG",
           "ล่าผลปีศาจ ฝึกวิชา ต่อสู้กับบอส ขึ้นทะเลใหม่ อัปเดตตลอดเวลา",
           "Gamer Robot Inc",
           ("BIGNEWS", "EASTEREXP", "THEGREATACE", "LIGHTNINGABUSE", "JCWK",
            "SUB2GAMERROBOT_EXP1", "Sub2Daigrock", "fudd10_v2", "FUDD10", "TantaiGaming")),
    _Entry("Tower of Hell", 1962086868, 703124385, frozenset({"obby"}), "🧗 ปริศนา/Obby",
           "หอคอยสุดโหด ปีนให้ไวที่สุด ผิดพลาดทีเดียวตกแน่นอน",
           "YXceptional Studios"),
    _Entry("Murder Mystery 2", 142823291, 66654135, frozenset({"horror", "fighting"}), "👻 สยองขวัญ",
           "ฆาตกรซ่อนตัวอยู่ในหมู่พวกเรา ใครคือฆาตกร? ระวังหลังไว้ให้ดี",
           "Nikilis"),
    _Entry("DOORS", 6516141723, 2440500124, frozenset({"horror"}), "👻 สยองขวัญ",
           "เดินเข้าอาคารผีสิง เปิดประตูไปทีละบาน แต่อย่าเจอ Entity เข้าเด็ดขาด",
           "LSPLASH"),
    _Entry("Pet Simulator X", 6284583030, 2316994223, frozenset({"sim", "rpg"}), "🏠 จำลองชีวิต / RPG",
           "สะสมเพตแสนน่ารัก ฟาร์มห้อง เปิดไข่ยักษ์ลุ้นเพตหายากระดับตำนาน",
           "BIG Games Pets"),
    _Entry("Jailbreak", 606849621, 245662005, frozenset({"sim", "fighting"}), "🏠 จำลองชีวิต / ต่อสู้",
           "ปล้นธนาคารหรือเป็นตำรวจไล่จับ ใช้รถสุดหรูหนีตำรวจให้สุดมันส์",
           "Badimo"),
    _Entry("Arsenal", 286090429, 111958650, frozenset({"fighting"}), "⚔️ ต่อสู้",
           "FPS ดวลปืนเร็วปรื๊อ อาวุธสุ่มเปลี่ยนทุกครั้งที่ฆ่า ใครหัวไวสุดชนะ",
           "ROLVe"),
    _Entry("Bee Swarm Simulator", 1537690962, 601130232, frozenset({"sim", "rpg"}), "🏠 จำลองชีวิต / RPG",
           "เลี้ยงผึ้ง เก็บน้ำผึ้ง สู้แมลงยักษ์ อัปเกรดรังให้ใหญ่ขึ้นเรื่อยๆ",
           "Onett",
           ("FrogFix", "777", "MarchIsMerry", "ThreeBeeVee", "15MMembers", "Octobersmas", "BoxWhoops")),
    _Entry("Natural Disaster Survival", 189707, 65241, frozenset({"obby"}), "🧗 ปริศนา/Obby",
           "เอาตัวรอดจากภัยพิบัติสุ่มๆ ทั้งแผ่นดินไหว น้ำท่วม อุกกาบาต หนีให้ทันก่อนโดน",
           "Stickmasterluke"),
    _Entry("Work at a Pizza Place", 192800, 47545, frozenset({"sim"}), "🏠 จำลองชีวิต",
           "ทำงานในร้านพิซซ่าตั้งแต่พนักงานเสิร์ฟจนถึงผู้จัดการ รับเงินเดือนซื้อของ",
           "Dued1"),
    _Entry("Tower Defense Simulator", 3260590327, 1176784616, frozenset({"rpg"}), "🐉 RPG/ผจญภัย",
           "วางหอคอยยิงคลื่นมอนสเตอร์ วางแผนตำแหน่งให้ดี ไม่งั้นฐานแตกแน่นอน",
           "Paradoxum Games",
           ("TDS5YEARS!", "IMCONSUMING", "1MILCOMMUNITY", "2MILLION")),
    _Entry("Welcome to Bloxburg", 185655149, 88070565, frozenset({"sim"}), "🏠 จำลองชีวิต",
           "สร้างบ้านในฝันทีละห้อง ทำงานเก็บเงิน ใช้ชีวิตจริงในเมืองจำลองสุดละเอียด",
           "Bloxburg Development"),
    _Entry("King Legacy", 4520749081, 1451439645, frozenset({"fighting", "rpg"}), "⚔️ ต่อสู้ / RPG",
           "ผจญภัยในโลกโจรสลัด ฝึกวิชา เก็บผลปีศาจ ต่อสู้กับบอสระดับตำนาน",
           "Sea King Games",
           ("2MFAV", "FREESTATSRESET", "WELCOMETOKINGLEGACY", "<3LEEPUNGG", "DragonColorRefund")),
    _Entry("Piggy", 4623386862, 1516533665, frozenset({"horror"}), "👻 สยองขวัญ",
           "หนีให้พ้นจากพิกกี้และเพื่อนๆ ในแผนที่ปริศนา ไขปริศนาให้ครบก่อนโดนจับ",
           "Piggy Dev Team"),
    _Entry("The Strongest Battlegrounds", 10449761463, 3808081382, frozenset({"fighting"}), "⚔️ ต่อสู้",
           "ดวล PvP สุดมันส์ในโลกอนิเมะ ฟาดคอมโบขั้นเทพ ใครเก่งสุดครองสังเวียน",
           "Yielding Arts",
           ("81305726611791", "103976786559083", "1837879082", "168208965", "82573794711963")),
    _Entry("Slap Battles", 6403373529, 2380077519, frozenset({"fighting"}), "⚔️ ต่อสู้",
           "ตบกันให้สุด! เก็บมือพิเศษสุดเพี้ยน ใช้ลูกเล่นแกล้งเพื่อนกลางสนาม",
           "Slap Battles",
           ("Happy5lappiversary", "spookyseason25", "LoneOrange", "BeMyAwesomeValentine", "Beginner")),
    _Entry("Islands", 4872321990, 1659645941, frozenset({"sim"}), "🏠 จำลองชีวิต",
           "ฟาร์ม ขุดแร่ สร้างฟาร์มอัตโนมัติ ค้าขายกับผู้เล่น ตั้งอาณาจักรบนเกาะของคุณเอง",
           "Easy.gg"),
)


@dataclass(slots=True)
class RobloxGame:
    name: str
    place_id: int
    universe_id: int
    genre_label: str
    blurb: str
    creator: str
    playing: int | None
    visits: int | None
    favorites: int | None
    price: int | None
    description: str
    thumb_url: str | None
    url: str
    source_label: str
    codes: tuple[str, ...] = ()


# ── Genre resolution ─────────────────────────────────────────────────────────

def _resolve_genre(query: str | None) -> frozenset[str] | None:
    """Map a free-text query to a genre bucket, or None when no filter applies.

    Accepts Thai bucket labels, English aliases and even a bucket name written
    sloppily (contains-match). ``None`` means "no filter" (random pick).
    """
    query = (query or "").strip().lower()
    if not query or query in ("สุ่ม", "random", "อะไรก็ได้"):
        return None
    # Longest aliases first: "rpg" must win over the bare "rp" substring.
    for alias, tag in sorted(_GENRE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in query:
            return frozenset({tag})
    for label, tags in GENRE_BUCKETS.items():
        if label.lower() in query:
            return tags
    return frozenset()  # recognized text but no genre → empty filter (no match)


# ── Live data ───────────────────────────────────────────────────────────────

async def _fetch_live_games(universe_ids: list[int]) -> dict[int, dict]:
    """Batch fetch live game stats. Returns {} on any failure (→ fallback)."""
    ids = tuple(sorted(set(universe_ids)))
    if not ids:
        return {}
    cached = _live_cache.get(ids)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(_GAMES_API, params={"universeIds": ",".join(str(i) for i in ids)}) as resp,
        ):
            if resp.status >= 400:
                logger.warning("Roblox games request failed: %s", resp.status)
                return {}
            data = await resp.json(content_type=None)
    except Exception:
        logger.exception("Roblox games request failed")
        return {}

    result: dict[int, dict] = {}
    for item in (data or {}).get("data", []):
        try:
            result[int(item["id"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    _live_cache[ids] = (time.time(), result)
    return result


async def _fetch_thumb_url(universe_id: int) -> str | None:
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(
                _THUMB_API,
                params={"universeIds": universe_id, "size": "512x512", "format": "Png", "isCircular": "false"},
            ) as resp,
        ):
            if resp.status >= 400:
                return None
            data = await resp.json(content_type=None)
        entries = (data or {}).get("data") or []
        if entries and entries[0].get("imageUrl"):
            return entries[0]["imageUrl"]
    except Exception:
        logger.exception("Roblox thumbnail request failed")
    return None


# ── Redeem codes ────────────────────────────────────────────────────────────

async def _fetch_external_codes() -> dict[str, list[str]]:
    """Pull owner-maintained redeem codes JSON (GUCOIN_ROBLOX_CODES_URL).

    Shape: {"Blox Fruits": ["CODE1", ...]} — game-name keys are matched
    case-insensitively. Returns {} when the URL is unset or unreachable;
    the result is cached briefly since codes change slowly.
    """
    url = settings.ROBLOX_CODES_URL
    if not url:
        return {}
    cached = _codes_cache.get(url)
    if cached and time.time() - cached[0] < _CODES_TTL:
        return cached[1]
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(url) as resp,
        ):
            if resp.status >= 400:
                logger.warning("Roblox codes request failed: %s", resp.status)
                return {}
            data = await resp.json(content_type=None)
    except Exception:
        logger.exception("Roblox codes fetch failed")
        return {}

    result: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for name, codes in data.items():
            if not isinstance(codes, (list, tuple)):
                continue
            cleaned = [str(c).strip() for c in codes if str(c).strip()]
            if cleaned:
                result[str(name)] = cleaned
    _codes_cache[url] = (time.time(), result)
    return result


def _merged_codes(entry: _Entry, external: dict[str, list[str]]) -> tuple[str, ...]:
    """External codes (fresher) win over the built-in list, deduped."""
    for name, codes in external.items():
        if name.strip().lower() == entry.name.lower():
            merged = list(codes) + [c for c in entry.codes if c not in codes]
            return tuple(merged)
    return entry.codes


# ── Web search for live redeem codes ────────────────────────────────────────

# Common words that would otherwise look like codes in search snippets.
_COMMON_WORDS = frozenset(
    {
        "the", "and", "for", "with", "you", "are", "not", "all", "codes", "code",
        "roblox", "game", "games", "new", "redeem", "active", "list", "working",
        "update", "updated", "how", "use", "get", "free", "this", "that", "your",
        "from", "will", "what", "when", "about", "they", "there", "their", "into",
        "them", "then", "just", "like", "some", "other", "only", "also", "these",
        "would", "could", "should", "which", "while", "after", "before", "because",
        "between", "have", "has", "been", "were", "was", "its", "out", "more",
        "than", "one", "can", "may", "see", "now", "find", "found", "check",
        "click", "button", "section", "page", "website", "here", "make", "made",
        "come", "came", "latest", "expired", "expire", "expires", "added", "guide",
        "guides", "tutorial", "official", "xp", "vip", "tips", "help", "info",
        "text", "read", "share", "best", "top", "way", "first", "last",
        "please", "thanks", "thank", "welcome", "submit", "enter", "claim", "save",
        "next", "time", "year", "month", "day", "week", "today", "tomorrow",
        "july", "august", "september", "october", "november", "december",
        "january", "february", "march", "april", "june", "known",
    }
)


def _web_search_configured() -> bool:
    return bool(settings.GOOGLE_API_KEY and settings.GOOGLE_CSE_ID) or bool(settings.BRAVE_API_KEY)


def _clean_search_name(game_name: str) -> str:
    """Strip decorations (e.g. "[2D TUESDAY] Adopt Me!") for a cleaner query."""
    name = re.sub(r"\[[^\]]*\]", "", game_name)
    return " ".join(name.split())


async def _web_search(query: str) -> list[dict]:
    """Search the web for ``query`` — Google CSE first, Brave as fallback.

    Returns a normalized list of {"title", "snippet"} dicts; [] when unset,
    unreachable, or rate-limited.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if settings.GOOGLE_API_KEY and settings.GOOGLE_CSE_ID:
                async with session.get(
                    _GOOGLE_CSE_API,
                    params={
                        "key": settings.GOOGLE_API_KEY,
                        "cx": settings.GOOGLE_CSE_ID,
                        "q": query,
                        "num": "5",
                    },
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("Google CSE search failed: %s", resp.status)
                    else:
                        data = await resp.json(content_type=None)
                        items = (data or {}).get("items") or []
                        if items:  # empty results fall through to Brave
                            return [
                                {"title": str(i.get("title") or ""), "snippet": str(i.get("snippet") or "")}
                                for i in items
                            ]
            if settings.BRAVE_API_KEY:
                async with session.get(
                    _BRAVE_API,
                    params={"q": query, "count": "5"},
                    headers={"X-Subscription-Token": settings.BRAVE_API_KEY, "Accept": "application/json"},
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("Brave search failed: %s", resp.status)
                    else:
                        data = await resp.json(content_type=None)
                        results = ((data or {}).get("web") or {}).get("results") or []
                        if results:
                            return [
                                {"title": str(r.get("title") or ""), "snippet": str(r.get("description") or "")}
                                for r in results
                            ]
    except Exception:
        logger.exception("Web search failed")
    return []


def _looks_like_code(token: str, banned: set[str]) -> bool:
    """Heuristic: codes are loud tokens (digits/uppercase), not dictionary words."""
    if token.lower() in banned:
        return False
    upper = sum(c.isupper() for c in token)
    digits = sum(c.isdigit() for c in token)
    if not (digits or upper):
        return False                       # plain lowercase words
    if token.isdigit() and len(token) < 6:
        return False                       # years / small numbers
    return not (upper == 1 and not digits)  # reject capitalized words like "August"


def _extract_codes(game_name: str, results: list[dict]) -> tuple[str, ...]:
    """Pull code-like tokens out of search titles/snippets, frequency-ranked."""
    text = " ".join(f"{r.get('title') or ''} {r.get('snippet') or ''}" for r in results)
    if not text.strip():
        return ()
    banned = set(_COMMON_WORDS)
    banned.update(w for w in re.split(r"\W+", game_name.lower()) if len(w) >= 3)
    counts: dict[str, int] = {}
    for token in re.findall(r"[A-Za-z0-9_]{4,24}", text):
        if _looks_like_code(token, banned):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)
    return tuple(c for c, _ in ranked[:12])


async def _search_codes(game_name: str) -> tuple[str, ...]:
    """Live redeem-code lookup for a game, cached per cleaned game name."""
    clean = _clean_search_name(game_name)
    key = clean.lower()
    cached = _search_cache.get(key)
    if cached:
        ttl = _SEARCH_TTL if cached[1] else _SEARCH_EMPTY_TTL
        if time.time() - cached[0] < ttl:
            return cached[1]
    found = _extract_codes(clean, await _web_search(f"{clean} redeem codes {time.localtime().tm_year}"))
    _search_cache[key] = (time.time(), found)
    return found


# ── Recommendation ──────────────────────────────────────────────────────────

async def recommend_roblox_game(query: str | None = None, exclude: set[int] | None = None) -> RobloxGame | None:
    """Pick a top map, filtered by genre/name keyword, with live stats + codes.

    When live player counts are available the pick is data-driven — random
    among the TOP_K most-played maps of the pool — instead of a flat dice.
    ``exclude`` (set of universe ids) is used by the reroll button so the same
    map isn't suggested twice in a row. Redeem codes are looked up live from
    the web (Google CSE / Brave, keyed on the API-reported game name) with the
    owner-maintained GUCOIN_ROBLOX_CODES_URL and the built-in list as fallbacks.
    """
    exclude = exclude or set()
    query = (query or "").strip().lower()
    tags = _resolve_genre(query)

    pool = [g for g in ROBLOX_GAMES if g.universe_id not in exclude]
    if tags:
        pool = [g for g in pool if tags & g.tags]
    elif query:
        matched = [g for g in pool if query in g.name.lower()]
        if matched:
            pool = matched

    if not pool:  # exclude emptied the genre pool → allow repeats
        pool = [g for g in ROBLOX_GAMES if (not tags or tags & g.tags)]
        if not pool:
            return None

    # Live-popularity ranking: with real player counts the pick is random among
    # the TOP_K most-played maps, otherwise a flat dice over the whole pool.
    live = await _fetch_live_games([g.universe_id for g in pool])
    ranked = sorted(((int((live.get(g.universe_id, {}) or {}).get("playing") or 0), g) for g in pool), key=lambda t: t[0])
    if ranked and ranked[-1][0] > 0:
        entry = random.choice([g for p, g in ranked if p > 0][-_TOP_K:])
    else:
        entry = random.choice(pool)
    live_data = live.get(entry.universe_id) or {}
    live_name = str(live_data.get("name") or entry.name)

    playing = live_data.get("playing")
    visits = live_data.get("visits")
    favorites = live_data.get("favoritedCount")
    price = live_data.get("price")
    description = str(live_data.get("description") or "").strip()
    thumb_url = await _fetch_thumb_url(entry.universe_id)

    # Redeem codes: live web search first (keyed on the API-reported name),
    # then the owner-maintained JSON, then the built-in list.
    codes = await _search_codes(live_name) if _web_search_configured() else ()
    if not codes:
        codes = _merged_codes(entry, await _fetch_external_codes())

    return RobloxGame(
        name=live_name,
        place_id=int(live_data.get("rootPlaceId") or entry.place_id),
        universe_id=entry.universe_id,
        genre_label=entry.genre_label,
        blurb=entry.blurb,
        creator=str((live_data.get("creator") or {}).get("name") or entry.creator),
        playing=int(playing) if playing is not None else None,
        visits=int(visits) if visits is not None else None,
        favorites=int(favorites) if favorites is not None else None,
        price=int(price) if price is not None else None,
        description=description[:1000] or entry.blurb,
        thumb_url=thumb_url,
        url=f"https://www.roblox.com/games/{int(live_data.get('rootPlaceId') or entry.place_id)}",
        source_label="live" if live_data else "curated",
        codes=codes,
    )
