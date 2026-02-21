
import sys
import os
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game


class TestTier4Planeswalkers(unittest.TestCase):
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
        for _ in range(6):
            land = Card(name="Mountain", cost="", type_line="Basic Land")
            land.controller = self.p1
            self.game.battlefield.add(land)

    def _make_pw(self, name="Chandra", loyalty=4, oracle_text=""):
        """Helper to make a Planeswalker card."""
        return Card(
            name=name, cost="{2}{R}{R}",
            type_line="Legendary Planeswalker — Chandra",
            oracle_text=oracle_text,
            loyalty=loyalty
        )

    # ─── Planeswalker Parsing ──────────────────────────────────────

    def test_planeswalker_parsing_loyalty(self):
        """Parse starting loyalty from constructor."""
        pw = self._make_pw(loyalty=5)
        self.assertTrue(pw.is_planeswalker)
        self.assertEqual(pw.loyalty, 5)

    def test_planeswalker_parsing_abilities(self):
        """Parse loyalty abilities from oracle text."""
        pw = self._make_pw(oracle_text="[+1]: Chandra deals 2 damage to any target.\n[-2]: Draw 2 cards.\n[-7]: Destroy target creature.")
        self.assertTrue(pw.is_planeswalker)
        self.assertEqual(len(pw.loyalty_abilities), 3)
        self.assertEqual(pw.loyalty_abilities[0]['cost'], 1)   # +1
        self.assertEqual(pw.loyalty_abilities[1]['cost'], -2)  # -2
        self.assertEqual(pw.loyalty_abilities[2]['cost'], -7)  # -7

    # ─── Loyalty Ability Availability ──────────────────────────────

    def test_loyalty_ability_availability(self):
        """Loyalty ability actions offered during Main phases."""
        pw = self._make_pw(loyalty=4, oracle_text="[+1]: Chandra deals 2 damage to any target.\n[-2]: Draw 2 cards.")
        pw.controller = self.p1
        self.game.battlefield.add(pw)

        legal = self.game.get_legal_actions()
        pw_actions = [a for a in legal if a.get('type') == 'loyalty_ability' and a['card'] == pw]
        self.assertEqual(len(pw_actions), 2, "Should offer both +1 and -2 abilities")

    def test_loyalty_ability_insufficient_loyalty(self):
        """Can't activate if loyalty + cost < 0."""
        pw = self._make_pw(loyalty=1, oracle_text="[-3]: Destroy target creature.")
        pw.controller = self.p1
        self.game.battlefield.add(pw)

        legal = self.game.get_legal_actions()
        pw_actions = [a for a in legal if a.get('type') == 'loyalty_ability']
        self.assertEqual(len(pw_actions), 0, "Should not offer -3 with only 1 loyalty")

    def test_loyalty_ability_one_per_turn(self):
        """Only one loyalty ability per turn."""
        pw = self._make_pw(loyalty=5, oracle_text="[+1]: Chandra deals 2 damage to any target.\n[-2]: Draw 2 cards.")
        pw.controller = self.p1
        self.game.battlefield.add(pw)

        # Activate +1
        self.game.apply_action({
            'type': 'loyalty_ability', 'card': pw,
            'ability_index': 0, 'cost': 1
        })

        # Should not offer any more loyalty actions this turn
        legal = self.game.get_legal_actions()
        pw_actions = [a for a in legal if a.get('type') == 'loyalty_ability']
        self.assertEqual(len(pw_actions), 0, "No more loyalty abilities this turn")
        self.assertEqual(pw.loyalty, 6)  # 5 + 1

    # ─── Loyalty Ability Activation ────────────────────────────────

    def test_loyalty_ability_activation_plus(self):
        """Activating +N adds loyalty."""
        pw = self._make_pw(loyalty=4, oracle_text="[+2]: You gain 3 life.")
        pw.controller = self.p1
        self.game.battlefield.add(pw)

        self.game.apply_action({
            'type': 'loyalty_ability', 'card': pw,
            'ability_index': 0, 'cost': 2
        })
        self.assertEqual(pw.loyalty, 6)  # 4 + 2
        self.assertTrue(pw.loyalty_used_this_turn)

    def test_loyalty_ability_activation_minus(self):
        """Activating -N removes loyalty and resolves effect."""
        pw = self._make_pw(loyalty=4, oracle_text="[-2]: Draw 2 cards.")
        pw.controller = self.p1
        self.game.battlefield.add(pw)
        # Seed library for draw
        for i in range(5):
            self.p1.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))
        initial_hand = len(self.p1.hand)

        self.game.apply_action({
            'type': 'loyalty_ability', 'card': pw,
            'ability_index': 0, 'cost': -2
        })
        self.assertEqual(pw.loyalty, 2)  # 4 - 2
        
        # Resolve the ability from stack
        self.game.resolve_stack()
        self.assertEqual(len(self.p1.hand), initial_hand + 2)

    # ─── Combat Damage to Planeswalker ─────────────────────────────

    def test_planeswalker_combat_damage(self):
        """Unblocked attacker damages PW loyalty."""
        pw = self._make_pw(loyalty=4)
        pw.controller = self.p2
        self.game.battlefield.add(pw)

        attacker = Card(name="Goblin", cost="{R}", type_line="Creature — Goblin",
                       base_power=2, base_toughness=1)
        attacker.controller = self.p1
        attacker.summoning_sickness = False
        attacker._attacking_pw = pw  # Target the PW
        self.game.battlefield.add(attacker)

        self.game.combat_attackers = [attacker]
        self.game.combat_blockers = {}
        self.game.resolve_combat_damage()

        self.assertEqual(pw.loyalty, 2)  # 4 - 2

    # ─── SBA: 0 Loyalty ───────────────────────────────────────────

    def test_planeswalker_zero_loyalty_sba(self):
        """PW with 0 loyalty goes to graveyard via SBA."""
        pw = self._make_pw(loyalty=0)
        pw.controller = self.p2
        self.game.battlefield.add(pw)

        self.game.check_state_based_actions()

        self.assertNotIn(pw, self.game.battlefield.cards)
        self.assertIn(pw, self.p2.graveyard.cards)

    def test_planeswalker_enters_with_loyalty(self):
        """PW enters battlefield with starting loyalty."""
        pw = self._make_pw(loyalty=5)
        pw.controller = self.p1
        self.p1.hand.add(pw)

        self.game.apply_action({'type': 'cast_spell', 'card': pw})
        self.game.resolve_stack()

        bf_pw = [c for c in self.game.battlefield.cards if c.is_planeswalker]
        self.assertEqual(len(bf_pw), 1)
        self.assertEqual(bf_pw[0].loyalty, 5)

    # ─── Cleanup: Reset Loyalty Used ───────────────────────────────

    def test_loyalty_reset_on_cleanup(self):
        """loyalty_used_this_turn resets during cleanup."""
        pw = self._make_pw(loyalty=5, oracle_text="[+1]: You gain 3 life.")
        pw.controller = self.p1
        pw.loyalty_used_this_turn = True
        self.game.battlefield.add(pw)

        self.game._do_cleanup()

        self.assertFalse(pw.loyalty_used_this_turn)


if __name__ == '__main__':
    unittest.main()
