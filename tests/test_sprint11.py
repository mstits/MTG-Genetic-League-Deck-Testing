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


# ─── Strategic Agent Scoring Functions ────────────────────────────────────────

class TestStrategicAgentScoring:
    """Tests for StrategicAgent's 4-dimension scoring system."""

    def _make_game(self):
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

    def test_board_power_empty(self):
        """Board power with no creatures should be 0."""
        from agents.strategic_agent import StrategicAgent
        game = self._make_game()
        score = StrategicAgent._board_power(game, game.players[0])
        assert score == 0.0

    def test_board_power_with_creature(self):
        """Board power should increase when a creature is on the battlefield."""
        from agents.strategic_agent import StrategicAgent
        from engine.card import Card
        game = self._make_game()
        creature = Card(name="Grizzly Bears", cost="{1}{G}", type_line="Creature — Bear",
                       base_power=2, base_toughness=2)
        creature.controller = game.players[0]
        game.battlefield.add(creature)
        score = StrategicAgent._board_power(game, game.players[0])
        assert score > 0.0

    def test_tempo_pass_is_zero(self):
        """Passing should have 0 tempo score."""
        from agents.strategic_agent import StrategicAgent
        agent = StrategicAgent()
        game = self._make_game()
        score = agent._evaluate_tempo(game, game.players[0], {'type': 'pass'})
        assert score == 0.0

    def test_card_cmc_parsing(self):
        """_card_cmc should correctly parse mana costs."""
        from agents.strategic_agent import StrategicAgent
        from engine.card import Card
        bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant")
        assert StrategicAgent._card_cmc(bolt) == 1
        wrath = Card(name="Wrath of God", cost="{2}{W}{W}", type_line="Sorcery")
        assert StrategicAgent._card_cmc(wrath) == 4


# ─── Heuristic Agent Scoring & Role Detection ────────────────────────────────

class TestHeuristicAgentScoring:
    """Tests for HeuristicAgent's scoring and role detection methods."""

    def _make_game(self):
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

    def test_assess_role_returns_valid_string(self):
        """_assess_role should return 'aggro', 'control', or 'midrange'."""
        from agents.heuristic_agent import HeuristicAgent
        agent = HeuristicAgent()
        game = self._make_game()
        role = agent._assess_role(game, game.players[0], game.players[1])
        assert role in ('aggro', 'control', 'midrange')

    def test_count_artifacts_empty(self):
        """_count_artifacts should return 0 with no artifacts on board."""
        from agents.heuristic_agent import HeuristicAgent
        agent = HeuristicAgent()
        game = self._make_game()
        count = agent._count_artifacts(game, game.players[0])
        assert count == 0

    def test_score_threat_creature(self):
        """_score_threat should return a positive score for creatures."""
        from agents.heuristic_agent import HeuristicAgent
        from engine.card import Card
        game = self._make_game()
        creature = Card(name="Tarmogoyf", cost="{1}{G}",
                       type_line="Creature — Lhurgoyf",
                       base_power=4, base_toughness=5)
        score = HeuristicAgent._score_threat(creature, game, 'midrange')
        assert score > 0

    def test_evaluate_hidden_interaction_no_lands(self):
        """With no untapped opponent lands, interaction risk should be 0."""
        from agents.heuristic_agent import HeuristicAgent
        agent = HeuristicAgent()
        game = self._make_game()
        # Tap all opponent lands
        for c in game.battlefield.cards:
            if c.controller == game.players[1] and c.is_land:
                c.tapped = True
        risk = agent._evaluate_hidden_interaction(game, game.players[1])
        assert risk == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
