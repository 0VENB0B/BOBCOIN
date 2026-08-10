"""Game statistics: house win-rate tracking for balance tuning (P2 #8).

Records every settled game (bet, net, and whether the house won) into a single
``system/stats`` doc so the house win rate can be measured and the slot jackpot
base rebalanced from real data instead of guesswork.
"""

from .core import _get_db, _in_txn


def _stats_ref():
    return _get_db().collection("system").document("stats")


def _empty() -> dict:
    return {"games": 0, "house_wins": 0, "bets": 0, "house_net": 0}


async def record_game_outcome(game: str, bet: int, player_net: int) -> None:
    """Record one settled game. ``player_net`` < 0 means the house won.

    Called fire-and-forget from the game runners; atomic per write. ``game`` is
    a short key like ``slot`` / ``flip`` / ``lottery`` / ``bj`` — each game type
    keeps its own counters under that key.
    """
    ref = _stats_ref()

    async def _work(t):
        doc = await ref.get(transaction=t)
        d = (doc.to_dict() or {}).get(game, _empty())
        d["games"] += 1
        d["bets"] += max(bet, 0)
        d["house_net"] += -player_net          # house gains what the player loses
        if player_net < 0:
            d["house_wins"] += 1
        t.set(ref, {game: d}, merge=True)

    await _in_txn(_work)


async def get_game_stats(game: str | None = None) -> dict:
    """Aggregate stats. Returns {game: {...}} or just one game's counters."""
    doc = await _stats_ref().get()
    d = doc.to_dict() or {}
    if game is not None:
        return d.get(game, _empty())
    return d
