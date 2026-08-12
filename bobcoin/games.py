"""Pure game-logic helpers (no Discord / Firestore dependencies).

Kept dependency-free so the money/odds logic can be unit-tested without
spinning up a Discord client or emulating Firestore.
"""

import random

# ── Blackjack ───────────────────────────────────────────────────────────────

def _bj_draw() -> int:
    v = random.randint(1, 13)
    return 11 if v == 1 else min(v, 10)


def _lucky_card(lk: float) -> int:
    v = random.randint(1, 13)
    if lk > 1 and v <= 5 and random.random() < min((lk - 1) * 0.12, 0.45):
        v = random.randint(6, 13)
    elif lk < 1 and v >= 9 and random.random() < min((1 - lk) * 0.12, 0.45):
        v = random.randint(1, 8)
    return 11 if v == 1 else min(v, 10)


def _bj_total(hand: list[int]) -> int:
    total, aces = sum(hand), hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _bj_str(hand: list[int], hide_second: bool = False) -> str:
    _F = {11: "A", 10: "10", 9: "9", 8: "8", 7: "7", 6: "6", 5: "5", 4: "4", 3: "3", 2: "2"}
    cards = [f"[{_F[c]}]" for c in hand]
    if hide_second and len(cards) >= 2:
        cards[1] = "[?]"
    return "  ".join(cards)


# ── Streaks ─────────────────────────────────────────────────────────────────

def _streak_effects(streak: int, is_win: bool, bet: int) -> tuple[float, int]:
    """Return (win_bonus_pct, mercy_amount). Applied to payout after game."""
    if is_win and streak >= 3:
        return min((streak - 2) * 0.05, 0.25), 0  # +5% per win beyond 2, cap 25%
    if not is_win and streak >= 5:
        return 0.0, int(bet * 0.03)  # 3% mercy refund
    return 0.0, 0


# ── Rock-Paper-Scissors ────────────────────────────────────────────────────────

RPS_CHOICES = {"rock": "🪨 ค้อน", "scissors": "✂️ กรรไกร", "paper": "📄 กระดาษ"}


def _rps_beats(a: str, b: str) -> bool:
    """True when move ``a`` beats move ``b`` (ค้อน > กรรไกร > กระดาษ > ค้อน)."""
    return {"rock": "scissors", "scissors": "paper", "paper": "rock"}[a] == b


def _rps_winner(p1: str, p2: str) -> int:
    """1 if p1 beats p2, -1 if p2 beats p1, 0 for a tie."""
    if p1 == p2:
        return 0
    return 1 if _rps_beats(p1, p2) else -1


def _rps_move_that_beats(choice: str) -> str:
    """The move that defeats ``choice`` (used by the bot when it wins)."""
    return next(m for m in RPS_CHOICES if _rps_beats(m, choice))


def _rps_move_that_loses_to(choice: str) -> str:
    """The move that loses to ``choice`` (used by the bot when it lets you win)."""
    return next(m for m in RPS_CHOICES if _rps_beats(choice, m))
