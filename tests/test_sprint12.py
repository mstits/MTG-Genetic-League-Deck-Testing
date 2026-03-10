"""Tests for Sprint 12 improvements: card builder, mulligan AI, commander brackets, ELO constant."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ─── Card Builder Utility ─────────────────────────────────────────────────────

class TestCardBuilder:
    """Tests for dict_to_card and inject_basic_lands."""

    def test_dict_to_card_basic_creature(self):
        """dict_to_card should create a valid creature card."""
        from engine.card_builder import dict_to_card
        data = {
            'name': 'Grizzly Bears',
            'mana_cost': '{1}{G}',
            'type_line': 'Creature — Bear',
            'oracle_text': '',
            'power': '2',
            'toughness': '2',
        }
        card = dict_to_card(data)
        assert card.name == 'Grizzly Bears'
        assert card.base_power == 2
        assert card.base_toughness == 2
        assert card.is_creature

    def test_dict_to_card_basic_land_produced_mana(self):
        """dict_to_card should infer produced_mana from basic land subtypes."""
        from engine.card_builder import dict_to_card
        data = {
            'name': 'Forest',
            'mana_cost': '',
            'type_line': 'Basic Land — Forest',
            'oracle_text': '{T}: Add {G}.',
        }
        card = dict_to_card(data)
        assert 'G' in card.produced_mana

    def test_dict_to_card_star_power(self):
        """dict_to_card should handle '*' power/toughness without crashing."""
        from engine.card_builder import dict_to_card
        data = {
            'name': 'Tarmogoyf',
            'mana_cost': '{1}{G}',
            'type_line': 'Creature — Lhurgoyf',
            'oracle_text': "Tarmogoyf's power is equal to...",
            'power': '*',
            'toughness': '1+*',
        }
        card = dict_to_card(data)
        assert card.base_power == 0  # '*' becomes 0
        assert card.base_toughness == 0

    def test_inject_basic_lands(self):
        """inject_basic_lands should add missing basics to a card pool."""
        from engine.card_builder import inject_basic_lands
        pool = {}
        result = inject_basic_lands(pool)
        assert 'Plains' in result
        assert 'Island' in result
        assert 'Swamp' in result
        assert 'Mountain' in result
        assert 'Forest' in result
        assert 'Wastes' in result

    def test_inject_basic_lands_idempotent(self):
        """inject_basic_lands should not overwrite existing basics."""
        from engine.card_builder import inject_basic_lands
        custom_mountain = {'name': 'Mountain', 'custom': True}
        pool = {'Mountain': custom_mountain}
        inject_basic_lands(pool)
        assert pool['Mountain']['custom'] is True  # Not overwritten


# ─── Mulligan AI ──────────────────────────────────────────────────────────────

class TestMulliganAI:
    """Tests for MulliganAI heuristic and decision logic."""

    def _make_hand(self, lands=3, spells=4):
        """Create a test hand with N lands and N spells."""
        from engine.card import Card
        hand = []
        for _ in range(lands):
            hand.append(Card(name="Mountain", cost="", type_line="Basic Land - Mountain"))
        for _ in range(spells):
            hand.append(Card(name="Lightning Bolt", cost="{R}", type_line="Instant",
                            oracle_text="Deals 3 damage."))
        return hand[:7]

    def test_unplayable_hand_no_lands(self):
        """A hand with 0 lands should score 99.0 (unplayable)."""
        from agents.mulligan_ai import MulliganAI
        ai = MulliganAI()
        hand = self._make_hand(lands=0, spells=7)
        score = ai.heuristic_goldfish_turn(hand)
        assert score == 99.0

    def test_unplayable_hand_all_lands(self):
        """A hand with all lands should score 99.0 (unplayable)."""
        from agents.mulligan_ai import MulliganAI
        ai = MulliganAI()
        hand = self._make_hand(lands=7, spells=0)
        score = ai.heuristic_goldfish_turn(hand)
        assert score == 99.0

    def test_good_hand_scores_low(self):
        """A balanced hand (3 lands, 4 spells) should score reasonably low."""
        from agents.mulligan_ai import MulliganAI
        ai = MulliganAI()
        hand = self._make_hand(lands=3, spells=4)
        score = ai.heuristic_goldfish_turn(hand)
        assert 3.0 <= score <= 12.0

    def test_should_mulligan_respects_max(self):
        """should_mulligan should never mulligan at 3+ mulligans."""
        from agents.mulligan_ai import MulliganAI
        from engine.card import Card
        from engine.deck import Deck
        ai = MulliganAI()
        hand = self._make_hand(lands=0, spells=7)  # Terrible hand
        deck = Deck()
        land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
        deck.add_card(land, 40)
        should_mull, reason = ai.should_mulligan(hand, deck, mulligan_count=3)
        assert should_mull is False
        assert "Never mulligan" in reason


# ─── Commander Bracket Classifier ─────────────────────────────────────────────

class TestCommanderBrackets:
    """Tests for classify_bracket and enforce_bracket."""

    def test_empty_deck_is_bracket_1(self):
        """An empty deck should classify as Bracket 1 (lowest power)."""
        from engine.commander import classify_bracket
        result = classify_bracket([])
        assert result['bracket'] == 1
        assert result['score'] == 0

    def test_fast_mana_increases_bracket(self):
        """A deck with fast mana should have higher bracket score."""
        from engine.commander import classify_bracket
        from engine.card import Card
        cards = [Card(name="Sol Ring", cost="{1}", type_line="Artifact",
                     oracle_text="{T}: Add {C}{C}.")]
        result = classify_bracket(cards)
        assert result['score'] > 0
        assert any('Fast mana' in s for s in result['signals'])

    def test_enforce_bracket_compliant(self):
        """A simple deck should have no violations at Bracket 4."""
        from engine.commander import enforce_bracket
        from engine.card import Card
        cards = [Card(name="Grizzly Bears", cost="{1}{G}",
                     type_line="Creature — Bear", oracle_text="")]
        violations = enforce_bracket(cards, max_bracket=4)
        assert violations == []


# ─── ELO K-Factor Constant ───────────────────────────────────────────────────

class TestELOConstant:
    """Tests for the extracted ELO K-factor constant in parallel.py."""

    def test_elo_k_factor_is_named_constant(self):
        """ELO_K_FACTOR should be a named constant (not magic number 32)."""
        parallel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'simulation', 'parallel.py')
        with open(parallel_path, 'r') as f:
            content = f.read()
        assert 'ELO_K_FACTOR' in content, "ELO_K_FACTOR constant should exist in parallel.py"

    def test_elo_k_factor_equals_32(self):
        """ELO_K_FACTOR should be set to 32 (standard chess K-factor)."""
        parallel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'simulation', 'parallel.py')
        with open(parallel_path, 'r') as f:
            content = f.read()
        assert 'ELO_K_FACTOR = 32' in content


# ─── Bare Except Regression Tests ────────────────────────────────────────────

class TestBareExceptRegression:
    """Verify that bare except: clauses have been replaced with specific ones."""

    def test_card_py_no_bare_except(self):
        """card.py should have no bare except: clauses."""
        card_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'engine', 'card.py')
        with open(card_path, 'r') as f:
            content = f.read()
        import re
        bare_excepts = re.findall(r'^\s*except\s*:\s*$', content, re.MULTILINE)
        assert len(bare_excepts) == 0, f"Found {len(bare_excepts)} bare except: clauses in card.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
