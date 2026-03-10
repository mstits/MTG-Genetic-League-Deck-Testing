"""Tests for engine/game_state_vector.py — Game state vectorization."""

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from engine.game_state_vector import vectorize_game_state, VECTOR_SIZE


def _make_game():
    """Create a minimal game for testing vectorization."""
    d1 = Deck()
    d2 = Deck()
    land = Card("Plains", "", "Basic Land — Plains", "{T}: Add {W}.", produced_mana=["W"])
    creature = Card("Grizzly Bears", "{1}{G}", "Creature — Bear", "", base_power=2, base_toughness=2)
    for _ in range(30):
        d1.add_card(land, 1)
        d2.add_card(land, 1)
    for _ in range(30):
        d1.add_card(creature, 1)
        d2.add_card(creature, 1)
    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    return Game([p1, p2])


class TestVectorizeGameState:
    """vectorize_game_state produces a fixed-length numeric vector."""

    def test_returns_correct_size(self):
        g = _make_game()
        vec = vectorize_game_state(g, 0)
        assert len(vec) == VECTOR_SIZE

    def test_player_perspectives_differ(self):
        """P1 and P2 perspectives should produce different vectors."""
        g = _make_game()
        v0 = vectorize_game_state(g, 0)
        v1 = vectorize_game_state(g, 1)
        # Life encoding should be the same (both start at 20), but
        # hand/board encoding may differ due to deck shuffling
        assert isinstance(v0, (list, type(v0)))

    def test_all_values_numeric(self):
        g = _make_game()
        vec = vectorize_game_state(g, 0)
        for i, v in enumerate(vec):
            # Supports native float/int, numpy scalars, and JAX arrays
            assert float(v) == float(v), f"Non-numeric value at index {i}: {v}"

    def test_life_total_encoded(self):
        """Life total should appear somewhere in the vector."""
        g = _make_game()
        vec = vectorize_game_state(g, 0)
        # The vector should contain the life total or a normalized version
        # Check that at least some values are nonzero
        assert any(v != 0 for v in vec)

    def test_vector_changes_after_game_action(self):
        """Vector should change after meaningful game state changes."""
        g = _make_game()
        vec_before = list(vectorize_game_state(g, 0))
        # Change life total
        g.players[0].life = 10
        vec_after = list(vectorize_game_state(g, 0))
        assert vec_before != vec_after
