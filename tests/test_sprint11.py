"""Tests for Sprint 11 improvements: shared deck builder, agent method extraction, engine exports."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ─── Shared Deck Builder ─────────────────────────────────────────────────────

class TestBuildDeck:
    def test_builds_deck_from_dict(self):
        """build_deck should create a Deck with correct card counts."""
        from engine.deck_builder_util import build_deck
        from engine.card import Card

        # Create a minimal card pool
        pool = {
            "Lightning Bolt": {
                "name": "Lightning Bolt",
                "mana_cost": "{R}",
                "type_line": "Instant",
                "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            },
            "Mountain": {
                "name": "Mountain",
                "mana_cost": "",
                "type_line": "Basic Land — Mountain",
                "oracle_text": "{T}: Add {R}.",
            },
        }
        card_dict = {"Lightning Bolt": 4, "Mountain": 20}
        deck = build_deck(card_dict, pool)

        # Deck should contain 24 cards total
        game_deck = deck.get_game_deck()
        assert len(game_deck) == 24, f"Expected 24 cards, got {len(game_deck)}"

    def test_handles_list_format(self):
        """build_deck should normalize list format to dict."""
        from engine.deck_builder_util import build_deck

        pool = {
            "Mountain": {
                "name": "Mountain",
                "mana_cost": "",
                "type_line": "Basic Land — Mountain",
                "oracle_text": "{T}: Add {R}.",
            },
        }
        card_list = ["Mountain", "Mountain", "Mountain"]
        deck = build_deck(card_list, pool)
        game_deck = deck.get_game_deck()
        assert len(game_deck) == 3

    def test_skips_missing_cards_by_default(self):
        """build_deck should silently skip cards not in the pool."""
        from engine.deck_builder_util import build_deck

        pool = {
            "Mountain": {
                "name": "Mountain",
                "mana_cost": "",
                "type_line": "Basic Land — Mountain",
                "oracle_text": "{T}: Add {R}.",
            },
        }
        card_dict = {"Mountain": 10, "Nonexistent Card": 4}
        deck = build_deck(card_dict, pool)
        game_deck = deck.get_game_deck()
        assert len(game_deck) == 10  # Only Mountains

    def test_raises_on_missing_when_skip_disabled(self):
        """build_deck with skip_missing=False should raise KeyError."""
        from engine.deck_builder_util import build_deck

        pool = {}
        with pytest.raises(KeyError):
            build_deck({"Nonexistent": 1}, pool, skip_missing=False)


# ─── Engine Package Exports ──────────────────────────────────────────────────

class TestEngineExports:
    def test_all_exports_defined(self):
        """engine.__all__ should contain the expected public symbols."""
        import engine
        assert hasattr(engine, '__all__')
        expected = {"Card", "Deck", "Game", "Player", "Zone", "build_deck"}
        assert set(engine.__all__) == expected

    def test_lazy_import_card(self):
        """from engine import Card should work via lazy __getattr__."""
        from engine import Card
        assert Card is not None
        # Card.name is an instance attribute, not a class attribute
        c = Card(name="Test", cost="", type_line="Creature")
        assert c.name == "Test"

    def test_lazy_import_game(self):
        """from engine import Game should work via lazy __getattr__."""
        from engine import Game
        assert Game is not None

    def test_lazy_import_build_deck(self):
        """from engine import build_deck should work via lazy __getattr__."""
        from engine import build_deck
        assert callable(build_deck)


# ─── Heuristic Agent Extracted Methods ────────────────────────────────────────

class TestHeuristicAgentExtractedMethods:
    """Tests for the methods extracted from get_action in Sprint 10."""

    def _make_game(self):
        """Create a minimal Game for testing."""
        from engine.card import Card
        from engine.deck import Deck
        from engine.player import Player
        from engine.game import Game

        land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
        d1 = Deck()
        d1.add_card(land, 40)
        d2 = Deck()
        d2.add_card(land, 40)
        game = Game([Player("P1", d1), Player("P2", d2)])
        game.start_game()
        return game

    def test_handle_pending_cast_returns_dict(self):
        """_handle_pending_cast should return a dict action or None."""
        from agents.heuristic_agent import HeuristicAgent

        agent = HeuristicAgent()
        game = self._make_game()
        player = game.players[0]
        opp = game.players[1]

        # With no pending cast, should return None
        game.pending_cast = None
        legal = game.get_legal_actions()
        result = agent._handle_pending_cast(game, player, opp, legal)
        assert result is None

    def test_handle_stack_response_returns_pass(self):
        """_handle_stack_response with empty stack should return None."""
        from agents.heuristic_agent import HeuristicAgent

        agent = HeuristicAgent()
        game = self._make_game()
        player = game.players[0]
        opp = game.players[1]
        legal = game.get_legal_actions()

        result = agent._handle_stack_response(game, player, opp, legal)
        # With empty stack, should return None
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
