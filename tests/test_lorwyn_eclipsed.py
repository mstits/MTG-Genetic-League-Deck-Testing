"""Tests for engine/mechanics/lorwyn_eclipsed.py — Set mechanics."""

from engine.card import Card
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.mechanics.lorwyn_eclipsed import process_lorwyn_eclipsed_mechanics


def _make_game():
    d1 = Deck()
    d2 = Deck()
    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    return Game([p1, p2])


class TestLorwynEclipsedMechanics:
    """Tests for the February 2026 Lorwyn Eclipsed mechanics parser."""

    def test_vivid_mechanic_applied(self):
        """Vivid cards should get the has_vivid flag and an ETB effect."""
        c = Card("Vivid Meadow", "", "Land", "Vivid.")
        assert getattr(c, 'has_vivid', False) is True
        assert c.etb_effect is not None

    def test_vivid_etb_effect(self):
        """Vivid ETB effect enters tapped with 2 charge counters."""
        game = _make_game()
        c = Card("Vivid Meadow", "", "Land", "Vivid.")
        
        # Simulate ETB
        c.etb_effect(game, c)
        
        # Verify state
        assert c.counters.get('charge', 0) == 2
        assert getattr(c, 'tapped', False) is True

    def test_vivid_preserves_existing_etb(self):
        """Vivid should chain onto existing ETB effects rather than overwriting."""
        game = _make_game()
        
        # Create without vivid first so we can inject a hook cleanly
        c = Card("Vivid Tracker", "{G}", "Creature", "When this enters, it gets a +1/+1 counter.")
        
        # Mock an existing ETB
        c.existing_hook_called = False
        def mock_etb(g, crd):
            crd.existing_hook_called = True
        c.etb_effect = mock_etb
        
        # Now apply the vivid mechanic over top of the existing ETB
        c.oracle_text += " Vivid."
        process_lorwyn_eclipsed_mechanics(c)
        
        # Simulate ETB
        c.etb_effect(game, c)
        
        assert c.counters.get('charge', 0) == 2
        assert getattr(c, 'tapped', False) is True
        assert c.existing_hook_called is True

    def test_card_without_mechanics(self):
        """Cards without Lorwyn Eclipsed mechanics are unaffected."""
        c = Card("Forest", "", "Land", "{T}: Add {G}.")
        process_lorwyn_eclipsed_mechanics(c)
        assert not getattr(c, 'has_vivid', False)
        assert c.etb_effect is None
