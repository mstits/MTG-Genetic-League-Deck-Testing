"""Tests for FormatValidator — deck legality checking."""

import pytest
from engine.format_validator import FormatValidator, LegalityError


# Minimal card pool for testing
CARD_POOL = [
    {
        "name": "Lightning Bolt",
        "legalities": {"modern": "legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "legal"},
        "color_identity": ["R"],
    },
    {
        "name": "Counterspell",
        "legalities": {"modern": "legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "legal"},
        "color_identity": ["U"],
    },
    {
        "name": "Oko, Thief of Crowns",
        "legalities": {"modern": "banned", "standard": "banned", "pioneer": "banned",
                        "commander": "legal", "legacy": "banned", "vintage": "restricted", "pauper": "not_legal"},
        "color_identity": ["U", "G"],
    },
    {
        "name": "Black Lotus",
        "legalities": {"modern": "not_legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "banned", "legacy": "banned", "vintage": "restricted", "pauper": "not_legal"},
        "color_identity": [],
    },
    {
        "name": "Goblin Guide",
        "legalities": {"modern": "legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "not_legal"},
        "color_identity": ["R"],
    },
    {
        "name": "Lava Spike",
        "legalities": {"modern": "legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "legal"},
        "color_identity": ["R"],
    },
    {
        "name": "Rift Bolt",
        "legalities": {"modern": "legal", "standard": "not_legal", "pioneer": "not_legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "legal"},
        "color_identity": ["R"],
    },
]

# Add basic lands
for land_name in ["Mountain", "Plains", "Island", "Swamp", "Forest"]:
    CARD_POOL.append({
        "name": land_name,
        "legalities": {"modern": "legal", "standard": "legal", "pioneer": "legal",
                        "commander": "legal", "legacy": "legal", "vintage": "legal", "pauper": "legal"},
        "color_identity": [],
    })


def _make_legal_modern_deck() -> dict:
    """Build a minimal legal Modern deck (60 cards)."""
    return {
        "Lightning Bolt": 4,
        "Goblin Guide": 4,
        "Lava Spike": 4,
        "Rift Bolt": 4,
        "Counterspell": 4,
        "Mountain": 40,
    }


class TestFormatValidator:
    """Test FormatValidator correctness."""

    def test_valid_modern_deck(self):
        """A legal 60-card Modern deck should pass."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = _make_legal_modern_deck()
        v.validate(deck)  # Should not raise

    def test_banned_card_raises(self):
        """Oko is banned in Modern."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = _make_legal_modern_deck()
        deck["Oko, Thief of Crowns"] = 1
        with pytest.raises(LegalityError) as exc_info:
            v.validate(deck)
        assert "Banned" in str(exc_info.value)
        assert "Oko" in str(exc_info.value)

    def test_not_legal_card(self):
        """Black Lotus is not legal in Modern."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = _make_legal_modern_deck()
        deck["Black Lotus"] = 1
        violations = v.get_illegal_cards(deck)
        reasons = [v["reason"] for v in violations]
        assert any("Not legal" in r for r in reasons)

    def test_too_many_copies(self):
        """5 copies of a non-basic is illegal."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = _make_legal_modern_deck()
        deck["Lightning Bolt"] = 5
        violations = v.get_illegal_cards(deck)
        reasons = [v["reason"] for v in violations]
        assert any("Too many copies" in r for r in reasons)

    def test_basic_lands_exempt(self):
        """40 Mountains is legal."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = _make_legal_modern_deck()
        assert deck["Mountain"] == 40
        violations = v.get_illegal_cards(deck)
        assert not any(v["card"] == "Mountain" for v in violations)

    def test_deck_too_small(self):
        """A 10-card deck is illegal."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = {"Lightning Bolt": 4, "Mountain": 6}
        violations = v.get_illegal_cards(deck)
        assert any("minimum" in v["reason"] for v in violations)

    def test_restricted_vintage(self):
        """Black Lotus is restricted in Vintage — 1 copy OK, 2 copies illegal."""
        v = FormatValidator(CARD_POOL, "vintage")
        deck = {"Black Lotus": 1, "Lightning Bolt": 4, "Mountain": 55}
        violations = v.get_illegal_cards(deck)
        assert not any(v["card"] == "Black Lotus" for v in violations)

        deck2 = {"Black Lotus": 2, "Lightning Bolt": 4, "Mountain": 54}
        violations2 = v.get_illegal_cards(deck2)
        assert any("Restricted" in v["reason"] for v in violations2)

    def test_commander_singleton(self):
        """Commander decks can only have 1 copy of each non-basic."""
        v = FormatValidator(CARD_POOL, "commander")
        deck = {"Lightning Bolt": 2, "Mountain": 98}
        violations = v.get_illegal_cards(deck)
        assert any("Too many copies" in v["reason"] for v in violations)

    def test_commander_color_identity(self):
        """Cards must match commander's color identity."""
        v = FormatValidator(CARD_POOL, "commander")
        # Red commander deck with a blue card
        deck = {"Lightning Bolt": 1, "Counterspell": 1, "Mountain": 98}
        violations = v.get_illegal_cards(deck, commander="Lightning Bolt")
        assert any("Color identity" in v["reason"] and v["card"] == "Counterspell"
                    for v in violations)

    def test_unknown_format_raises(self):
        """Unsupported format name should ValueError."""
        with pytest.raises(ValueError, match="Unknown format"):
            FormatValidator(CARD_POOL, "tiny_leaders")

    def test_unknown_card(self):
        """A card not in the pool should be flagged."""
        v = FormatValidator(CARD_POOL, "modern")
        deck = {"Made Up Card": 4, "Mountain": 56}
        violations = v.get_illegal_cards(deck)
        assert any("not found" in v["reason"] for v in violations)

    def test_is_legal_helper(self):
        """Quick single-card legality check."""
        v = FormatValidator(CARD_POOL, "modern")
        assert v.is_legal("Lightning Bolt")
        assert not v.is_legal("Oko, Thief of Crowns")

    def test_get_status(self):
        """Get raw status string."""
        v = FormatValidator(CARD_POOL, "modern")
        assert v.get_status("Lightning Bolt") == "legal"
        assert v.get_status("Oko, Thief of Crowns") == "banned"
        assert v.get_status("Nonexistent") == "unknown"

    def test_incompatible_salt_brackets(self):
        """Matchups with different salt brackets in Commander should raise LegalityError."""
        v = FormatValidator(CARD_POOL, "commander")
        # deck1 has Black Lotus (Bracket 5)
        deck1 = {"Black Lotus": 1, "Mountain": 99}
        # deck2 has no bracketed cards (Bracket 1)
        deck2 = {"Lightning Bolt": 1, "Mountain": 99}
        
        with pytest.raises(LegalityError) as exc_info:
            v.validate_matchup(deck1, deck2)
        
        assert "Incompatible Salt Brackets" in str(exc_info.value)
        assert "5 vs 1" in str(exc_info.value)

