"""Tests for events._parse_intent — the chat→game command parser.

This is pure string parsing (no Discord needed), so every edge case gets
covered: game keywords, all-in/half keywords, 5-digit lottery tickets,
flip sides, amounts with commas, and non-game text.
"""


from bobcoin.cogs.events import _parse_intent

# ── Non-game text ───────────────────────────────────────────────────────

def test_non_game_text_returns_none():
    assert _parse_intent("สวัสดี") is None
    assert _parse_intent("") is None
    assert _parse_intent("โอนเงินให้หน่อย") is None
    assert _parse_intent("สวัสดีครับทุกคน") is None
    assert _parse_intent("ดู weather สิ") is None


# ── Slot ────────────────────────────────────────────────────────────────

def test_slot_basic():
    assert _parse_intent("slot") == {"game": "slot", "amount": None}
    assert _parse_intent("สล็อต") == {"game": "slot", "amount": None}
    assert _parse_intent("SLOT 500") == {"game": "slot", "amount": 500}
    assert _parse_intent("slot 1,000") == {"game": "slot", "amount": 1000}
    assert _parse_intent("เล่น slot 500 หน่อย") == {"game": "slot", "amount": 500}


def test_slot_allin_keywords():
    for kw in ("all in", "allin", "ทุ่มหมด", "ทั้งหมด", "หมดตัว", "หมดเลย", "ทุ่มทั้งหมด"):
        assert _parse_intent(f"slot {kw}") == {"game": "slot", "amount": "allin"}, kw


def test_slot_half_keywords():
    for kw in ("ครึ่ง", "half", "ครึ่งนึง"):
        assert _parse_intent(f"slot {kw}") == {"game": "slot", "amount": "half"}, kw


def test_slot_allin_overrides_number():
    assert _parse_intent("slot all in 500") == {"game": "slot", "amount": "allin"}


def test_slot_number_over_max_bet_ignored():
    assert _parse_intent("slot 99999999999") == {"game": "slot", "amount": None}  # 11 digits > MAX_BET
    assert _parse_intent("slot 1000000000") == {"game": "slot", "amount": 1_000_000_000}  # exactly MAX_BET


def test_slot_non_numeric_amount_ignored():
    assert _parse_intent("slot เยอะๆ") == {"game": "slot", "amount": None}
    assert _parse_intent("slot abc") == {"game": "slot", "amount": None}


# ── Flip ────────────────────────────────────────────────────────────────

def test_flip_head():
    for kw in ("หัว", "head"):
        assert _parse_intent(f"flip {kw} 100") == {"game": "flip", "amount": 100, "side": "1"}, kw


def test_flip_tail():
    for kw in ("ก้อย", "tail"):
        assert _parse_intent(f"flip {kw} 100") == {"game": "flip", "amount": 100, "side": "2"}, kw


def test_flip_no_side_random_but_valid():
    for _ in range(50):
        intent = _parse_intent("flip 500")
        assert intent["game"] == "flip"
        assert intent["amount"] == 500
        assert intent["side"] in ("1", "2")


def test_flip_thai_keywords():
    assert _parse_intent("ทอยเหรียญ หัว 200")["side"] == "1"
    assert _parse_intent("หัวก้อย ก้อย 50")["game"] == "flip"
    assert _parse_intent("โยนเหรียญ ก้อย 1000")["side"] == "2"


# ── Lottery ─────────────────────────────────────────────────────────────

def test_lottery_with_ticket_and_amount():
    intent = _parse_intent("หวย 12345 200")
    assert intent == {"game": "lottery", "amount": 200, "ticket": "12345"}


def test_lottery_allin():
    intent = _parse_intent("หวย 12345 all in")
    assert intent["game"] == "lottery"
    assert intent["ticket"] == "12345"
    assert intent["amount"] == "allin"


def test_lottery_ticket_only_no_amount():
    intent = _parse_intent("lottery 12345")
    assert intent["game"] == "lottery"
    assert intent["ticket"] == "12345"
    assert intent["amount"] is None  # default 100 handled by caller


def test_lottery_missing_ticket():
    intent = _parse_intent("หวย all in")
    assert intent["game"] == "lottery"
    assert intent["ticket"] is None  # caller must ask for the 5 digits
    assert intent["amount"] == "allin"


def test_lottery_bad_ticket_lengths():
    # 4-digit and 6-digit numbers are NOT tickets — they become the amount
    intent = _parse_intent("หวย 1234")
    assert intent["ticket"] is None
    assert intent["amount"] == 1234
    intent = _parse_intent("หวย 123456")
    assert intent["ticket"] is None
    assert intent["amount"] == 123456


def test_lottery_case_and_thai_aliases():
    for kw in ("หวย", "lottery", "ลอตเตอรี่", "ลอต"):
        intent = _parse_intent(f"{kw} 36412 all in")
        assert intent["game"] == "lottery" and intent["ticket"] == "36412", kw


# ── Roblox ────────────────────────────────────────────────────────────────

def test_roblox_keywords():
    for kw in ("roblox", "แมพ", "แมป", "หาแมพ", "แนะนำแมพ", "อยากได้แมพ"):
        intent = _parse_intent(f"{kw} หน่อย")
        assert intent["game"] == "roblox", kw


def test_roblox_parses_with_numbers_but_game_is_roblox():
    intent = _parse_intent("แนะนำแมพ roblox 500")
    assert intent["game"] == "roblox"              # amount is parsed but unused
    assert intent.get("amount") == 500


# ── Misc robustness ─────────────────────────────────────────────────────

def test_parse_intent_never_raises():
    # garbage input must never throw
    for text in (None, "", " ", "12345", "!!!", "🎰", "a" * 500, "slot " * 100):
        try:
            _parse_intent(text)
        except Exception:
            raise AssertionError(f"crash on {text!r}") from None


def test_amounts_with_commas_and_whitespace():
    intent = _parse_intent("slot   1,000,000   ")
    assert intent["amount"] == 1_000_000


def test_lottery_commas_do_not_break_ticket():
    # a ticket written with commas is a 5-digit ticket after cleaning
    intent = _parse_intent("หวย 36,412 500")
    assert intent["ticket"] == "36412"
    assert intent["amount"] == 500
