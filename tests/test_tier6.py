
import sys
import os
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card, StackItem
from engine.deck import Deck
from engine.player import Player
from engine.game import Game


class TestTier6UtilityMechanics(unittest.TestCase):
    def setUp(self):
        self.p1 = Player("P1", Deck())
        self.p2 = Player("P2", Deck())
        self.game = Game([self.p1, self.p2])
        self.game.turn_count = 1
        self.game.active_player_index = 0
        self.game.priority_player_index = 0
        self.game.current_phase = "Main 1"
        self.game.phase_index = 3
        # Add lands for mana
        for _ in range(8):
            land = Card(name="Mountain", cost="", type_line="Basic Land")
            land.controller = self.p1
            self.game.battlefield.add(land)
        # Seed libraries
        for i in range(15):
            self.p1.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))
            self.p2.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))

    # ─── Fight ─────────────────────────────────────────────────────

    def test_fight_parsing(self):
        """Parse fight effect from oracle text."""
        card = Card(name="Prey Upon", cost="{G}",
                   type_line="Sorcery",
                   oracle_text="Target creature you control fights target creature you don't control.")
        self.assertTrue(card.is_fight)
        self.assertIsNotNone(card.effect)

    def test_fight_execution(self):
        """Fight deals mutual damage."""
        card = Card(name="Prey Upon", cost="{G}",
                   type_line="Sorcery",
                   oracle_text="Target creature you control fights target creature you don't control.")
        card.controller = self.p1

        own = Card(name="Bear", cost="{1}{G}", type_line="Creature — Bear",
                  base_power=4, base_toughness=4)
        own.controller = self.p1
        self.game.battlefield.add(own)

        enemy = Card(name="Goblin", cost="{R}", type_line="Creature — Goblin",
                    base_power=2, base_toughness=2)
        enemy.controller = self.p2
        self.game.battlefield.add(enemy)

        card.effect(self.game, card)

        # Bear took 2 damage from Goblin, Goblin took 4 from Bear
        self.assertEqual(enemy.damage_taken, 4)
        self.assertEqual(own.damage_taken, 2)

    def test_fight_with_deathtouch(self):
        """Fight respects deathtouch."""
        card = Card(name="Prey Upon", cost="{G}",
                   type_line="Sorcery",
                   oracle_text="Target creature you control fights target creature you don't control.")
        card.controller = self.p1

        own = Card(name="Vampire", cost="{B}", type_line="Creature — Vampire",
                  base_power=1, base_toughness=1, oracle_text="Deathtouch")
        own.controller = self.p1
        self.game.battlefield.add(own)

        enemy = Card(name="Giant", cost="{4}{G}", type_line="Creature — Giant",
                    base_power=5, base_toughness=5)
        enemy.controller = self.p2
        self.game.battlefield.add(enemy)

        card.effect(self.game, card)

        # Giant should be marked with deathtouch_damaged
        self.assertTrue(enemy.deathtouch_damaged)

    # ─── Mill ──────────────────────────────────────────────────────

    def test_mill_parsing(self):
        """Parse mill N from oracle text."""
        card = Card(name="Tome Scour", cost="{U}",
                   type_line="Sorcery",
                   oracle_text="Target player mills 5 cards.")
        self.assertTrue(card.is_mill)
        self.assertIsNotNone(card.effect)

    def test_mill_alt_parsing(self):
        """Parse 'put top N cards into graveyard' as mill."""
        card = Card(name="Glimpse the Unthinkable", cost="{U}{B}",
                   type_line="Sorcery",
                   oracle_text="Put the top 10 cards of target player's library into their graveyard.")
        self.assertTrue(card.is_mill)

    def test_mill_execution(self):
        """Mill moves cards from library to graveyard."""
        card = Card(name="Tome Scour", cost="{U}",
                   type_line="Sorcery",
                   oracle_text="Target player mills 5 cards.")
        card.controller = self.p1

        initial_lib = len(self.p2.library)
        initial_gy = len(self.p2.graveyard)
        card.effect(self.game, card)

        self.assertEqual(len(self.p2.library), initial_lib - 5)
        self.assertEqual(len(self.p2.graveyard), initial_gy + 5)

    # ─── Cycling ───────────────────────────────────────────────────

    def test_cycling_parsing(self):
        """Parse cycling cost from oracle text."""
        card = Card(name="Renewed Faith", cost="{2}{W}",
                   type_line="Instant",
                   oracle_text="You gain 6 life.\nCycling {1}{W}")
        self.assertEqual(card.cycling_cost, "{1}{W}")

    def test_cycling_action_availability(self):
        """Cycling action offered when player can afford."""
        card = Card(name="Ash Barrens", cost="{2}",
                   type_line="Instant",
                   oracle_text="You gain 6 life.\nCycling {2}")
        self.p1.hand.add(card)

        legal = self.game.get_legal_actions()
        cycle_actions = [a for a in legal if a.get('type') == 'cycle' and a.get('card') == card]
        self.assertEqual(len(cycle_actions), 1)

    def test_cycling_execution(self):
        """Cycling discards card and draws one."""
        card = Card(name="Renewed Faith", cost="{2}{W}",
                   type_line="Instant",
                   oracle_text="You gain 6 life.\nCycling {1}{W}")
        self.p1.hand.add(card)

        initial_hand = len(self.p1.hand)
        self.game.apply_action({'type': 'cycle', 'card': card})

        # Card should be in graveyard, hand size unchanged (discard 1, draw 1)
        self.assertIn(card, self.p1.graveyard.cards)
        self.assertNotIn(card, self.p1.hand.cards)
        self.assertEqual(len(self.p1.hand), initial_hand)  # -1 from discard, +1 from draw

    # ─── Proliferate ───────────────────────────────────────────────

    def test_proliferate_parsing(self):
        """Parse proliferate from oracle text."""
        card = Card(name="Tezzeret's Gambit", cost="{3}{U}",
                   type_line="Sorcery",
                   oracle_text="Draw two cards. Proliferate.")
        self.assertTrue(card.is_proliferate)

    def test_proliferate_execution(self):
        """Proliferate adds counters to controlled permanents with counters."""
        card = Card(name="Proliferate Spell", cost="{2}",
                   type_line="Sorcery",
                   oracle_text="Proliferate.")
        card.controller = self.p1

        creature = Card(name="Hydra", cost="{X}{G}{G}",
                       type_line="Creature — Hydra",
                       base_power=0, base_toughness=0)
        creature.controller = self.p1
        creature.counters['+1/+1'] = 3
        self.game.battlefield.add(creature)

        card.effect(self.game, card)

        self.assertEqual(creature.counters['+1/+1'], 4)

    def test_proliferate_no_targets(self):
        """Proliferate with no countered permanents doesn't crash."""
        card = Card(name="Proliferate Spell", cost="{2}",
                   type_line="Sorcery",
                   oracle_text="Proliferate.")
        card.controller = self.p1

        # Should not raise
        card.effect(self.game, card)


if __name__ == '__main__':
    unittest.main()
