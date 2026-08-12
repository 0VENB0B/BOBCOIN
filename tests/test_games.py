"""Tests for bobcoin.games (pure blackjack + streak logic).

Includes property-style checks (invariants that must hold for ALL inputs) and
statistical sanity checks on the random draws.
"""

import random

import pytest

from bobcoin.games import (
    RPS_CHOICES,
    _bj_draw,
    _bj_str,
    _bj_total,
    _lucky_card,
    _rps_move_that_beats,
    _rps_move_that_loses_to,
    _rps_winner,
    _streak_effects,
)

# ── Blackjack totals: property tests ────────────────────────────────────

def test_bj_total_properties():
    # empty hand
    assert _bj_total([]) == 0
    # non-ace hands sum plainly (even past 21)
    assert _bj_total([10, 9, 8]) == 27
    assert _bj_total([2, 3, 4, 5, 6]) == 20
    # hands with enough aces can never bust
    assert _bj_total([11, 11]) == 12
    assert _bj_total([11, 11, 11, 11, 11]) == 15
    # single ace soft/hard transition
    assert _bj_total([11]) == 11
    assert _bj_total([11, 10]) == 21
    assert _bj_total([11, 10, 10]) == 21   # ace downgraded to 1
    assert _bj_total([11, 10, 10, 10]) == 31  # no aces left to downgrade


def test_bj_total_ace_softening():
    assert _bj_total([11, 11]) == 12  # both aces → 1+11
    assert _bj_total([11, 11, 11]) == 13
    assert _bj_total([11, 5]) == 16
    assert _bj_total([11, 11, 5]) == 17  # 22→12 after ace, +5 = 17
    assert _bj_total([11, 10, 10]) == 21  # 1 + 10 + 10


def test_bj_total_all_ace_hands_never_exceed_21():
    # a hand of k aces can always be softened to k+10 ≤ 21 for k ≤ 11
    for k in range(0, 12):
        assert _bj_total([11] * k) <= 21


# ── Draws: bounds + distribution ────────────────────────────────────────

def test_bj_draw_bounds():
    for _ in range(500):
        v = _bj_draw()
        assert 1 <= v <= 11  # ace is valued 11
        assert v != 1


def test_bj_draw_never_below_2():
    # _bj_draw maps card 1 → 11, so 2..10, J..K → 10, A → 11
    values = {_bj_draw() for _ in range(5_000)}
    assert 2 in values and 11 in values and 10 in values
    assert 1 not in values


def test_bj_draw_distribution_ten_more_common():
    # ranks 2..10 each p=1/13, J/Q/K (→10) p=3/13, A (→11) p=1/13
    counts = {v: 0 for v in range(2, 12)}
    for _ in range(20_000):
        counts[_bj_draw()] += 1
    assert counts[10] > counts[2] * 2      # 10 hits ~4x more than 2
    assert counts[10] > counts[11]         # 10 more common than ace


def test_lucky_card_bounds():
    for lk in (0.0, 0.5, 1.0, 1.5, 3.0):
        for _ in range(200):
            v = _lucky_card(lk)
            assert 1 <= v <= 11
            assert v != 1


def test_lucky_card_neutral_luck_matches_plain_draw():
    # lk == 1.0 means no luck modifier → same distribution as _bj_draw
    random.seed(1234)
    a = [_bj_draw() for _ in range(2_000)]
    random.seed(1234)
    b = [_lucky_card(1.0) for _ in range(2_000)]
    assert a == b


def test_lucky_card_high_luck_improves_average():
    random.seed(7)
    base = sum(_lucky_card(1.0) for _ in range(5_000)) / 5_000
    random.seed(7)
    lucky = sum(_lucky_card(3.0) for _ in range(5_000)) / 5_000
    assert lucky > base


def test_lucky_card_low_luck_lowers_average():
    random.seed(7)
    base = sum(_lucky_card(1.0) for _ in range(5_000)) / 5_000
    random.seed(7)
    unlucky = sum(_lucky_card(0.0) for _ in range(5_000)) / 5_000
    assert unlucky < base


# ── String rendering ────────────────────────────────────────────────────

def test_bj_str():
    assert _bj_str([10, 11]) == "[10]  [A]"
    assert _bj_str([11, 10], hide_second=True) == "[A]  [?]"
    assert _bj_str([]) == ""
    assert _bj_str([2]) == "[2]"
    # hide_second with a single card must NOT hide (only 1 card)
    assert _bj_str([5], hide_second=True) == "[5]"
    # every possible value renders
    for v in range(2, 12):
        assert _bj_str([v]) == f"[{v if v != 11 else 'A'}]"


# ── Streak effects: full boundary matrix ────────────────────────────────

def test_streak_effects_win_streak():
    assert _streak_effects(0, True, 1000) == (0.0, 0)
    assert _streak_effects(1, True, 1000) == (0.0, 0)
    assert _streak_effects(2, True, 1000) == (0.0, 0)   # needs 3+
    assert _streak_effects(3, True, 1000)[0] == pytest.approx(0.05)
    assert _streak_effects(4, True, 1000)[0] == pytest.approx(0.10)
    assert _streak_effects(5, True, 1000)[0] == pytest.approx(0.15)
    assert _streak_effects(6, True, 1000)[0] == pytest.approx(0.20)
    assert _streak_effects(7, True, 1000) == (0.25, 0)  # capped at +25%
    assert _streak_effects(50, True, 1000) == (0.25, 0)  # cap holds forever


def test_streak_effects_win_bonus_scales_with_streak():
    pcts = [_streak_effects(s, True, 100)[0] for s in range(3, 8)]
    assert pcts == pytest.approx([0.05, 0.10, 0.15, 0.20, 0.25])


def test_streak_effects_cold_streak_mercy():
    assert _streak_effects(4, False, 1000) == (0.0, 0)   # needs 5+
    assert _streak_effects(5, False, 1000) == (0.0, 30)  # 3% of 1000
    assert _streak_effects(9, False, 10_000) == (0.0, 300)
    assert _streak_effects(100, False, 10_000) == (0.0, 300)  # mercy never scales


def test_streak_effects_mercy_is_always_3_percent_rounded_down():
    for bet in (1, 7, 100, 999, 100_000):
        _, mercy = _streak_effects(5, False, bet)
        assert mercy == int(bet * 0.03)


def test_streak_effects_zero_bet_safe():
    assert _streak_effects(5, False, 0) == (0.0, 0)
    assert _streak_effects(7, True, 0) == (0.25, 0)


def test_streak_effects_negative_streak_never_crashes():
    # defensive: a broken counter must not raise
    assert _streak_effects(-5, True, 100) == (0.0, 0)
    assert _streak_effects(-5, False, 100) == (0.0, 0)


# ── Rock Paper Scissors ─────────────────────────────────────────────────

def test_rps_choices_complete():
    assert set(RPS_CHOICES) == {"rock", "scissors", "paper"}


def test_rps_winner_matrix():
    assert _rps_winner("rock", "scissors") == 1      # ค้อน ทุบ กรรไกร
    assert _rps_winner("scissors", "paper") == 1     # กรรไกร ตัด กระดาษ
    assert _rps_winner("paper", "rock") == 1         # กระดาษ ห่อ ค้อน
    assert _rps_winner("scissors", "rock") == -1
    assert _rps_winner("paper", "scissors") == -1
    assert _rps_winner("rock", "paper") == -1
    for move in RPS_CHOICES:
        assert _rps_winner(move, move) == 0            # ties


def test_rps_winner_antisymmetric():
    for a in RPS_CHOICES:
        for b in RPS_CHOICES:
            assert _rps_winner(a, b) == -_rps_winner(b, a)


def test_rps_move_helpers_are_consistent():
    for move in RPS_CHOICES:
        beating = _rps_move_that_beats(move)
        losing = _rps_move_that_loses_to(move)
        assert _rps_winner(beating, move) == 1          # beats move
        assert _rps_winner(move, losing) == 1           # move beats losing
        assert beating != move and losing != move
        assert beating != losing
        # exactly one winning and one losing move per choice
        others = [m for m in RPS_CHOICES if m != move]
        assert set(others) == {beating, losing}
