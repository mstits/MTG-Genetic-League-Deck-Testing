
import sys
import os
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game


class TestTier3Mechanics(unittest.TestCase):
    def setUp(self):
        self.p1 = Player("P1", Deck())
        self.p2 = Player("P2", Deck())
        self.game = Game([self.p1, self.p2])
        # Manually set up game state
        self.game.turn_count = 1
        self.game.active_player_index = 0
        self.game.priority_player_index = 0
        self.game.current_phase = "Main 1"
        self.game.phase_index = 3
        # Add dummy lands (Mountains produce {R})
        for _ in range(6):
            land = Card(name="Mountain", cost="", type_line="Basic Land")
            land.controller = self.p1
            self.game.battlefield.add(land)

    # ─── Discard Effects ───────────────────────────────────────────

    def test_discard_parsing(self):
        """Parse 'target player discards a card'."""
        hymn = Card(name="Mind Rot", cost="{2}{B}",
                    type_line="Sorcery",
                    oracle_text="Target player discards two cards.")
        self.assertTrue(hymn.is_discard)
        self.assertIsNotNone(hymn.effect)

    def test_discard_execution(self):
        """Opponent loses a card from hand."""
        mind_rot = Card(name="Mind Rot", cost="{1}",
                        type_line="Sorcery",
                        oracle_text="Target player discards two cards.")
        # Give opponent cards
        for name in ("Bear", "Goblin", "Elf"):
            c = Card(name=name, cost="{1}", type_line="Creature — Bear",
                     base_power=2, base_toughness=2)
            self.p2.hand.add(c)

        initial_hand = len(self.p2.hand)
        self.p1.hand.add(mind_rot)
        self.game.apply_action({'type': 'cast_spell', 'card': mind_rot})
        self.game.resolve_stack()

        self.assertEqual(len(self.p2.hand), initial_hand - 2)
        self.assertEqual(len(self.p2.graveyard), 2)

    # ─── Graveyard Return ──────────────────────────────────────────

    def test_graveyard_return_to_hand(self):
        """Return creature from graveyard to hand."""
        raise_dead = Card(name="Raise Dead", cost="{1}",
                          type_line="Sorcery",
                          oracle_text="Return target creature card from your graveyard to your hand.")
        bear = Card(name="Grizzly Bears", cost="{1}{G}",
                    type_line="Creature — Bear",
                    base_power=2, base_toughness=2)
        self.p1.graveyard.add(bear)
        self.p1.hand.add(raise_dead)

        self.game.apply_action({'type': 'cast_spell', 'card': raise_dead})
        self.game.resolve_stack()

        self.assertIn(bear, self.p1.hand.cards)
        self.assertNotIn(bear, self.p1.graveyard.cards)

    def test_graveyard_return_to_battlefield(self):
        """Return creature from graveyard to battlefield."""
        reanimate = Card(name="Zombify", cost="{1}",
                         type_line="Sorcery",
                         oracle_text="Return target creature card from your graveyard to the battlefield.")
        dragon = Card(name="Shivan Dragon", cost="{4}{R}{R}",
                      type_line="Creature — Dragon",
                      base_power=5, base_toughness=5)
        self.p1.graveyard.add(dragon)
        self.p1.hand.add(reanimate)

        self.game.apply_action({'type': 'cast_spell', 'card': reanimate})
        self.game.resolve_stack()

        self.assertIn(dragon, self.game.battlefield.cards)
        self.assertNotIn(dragon, self.p1.graveyard.cards)

    # ─── Search Library ────────────────────────────────────────────

    def test_search_library(self):
        """Search library for a creature, put in hand."""
        tutor = Card(name="Worldly Tutor", cost="{1}",
                     type_line="Instant",
                     oracle_text="Search your library for a creature card, reveal it, then shuffle.")
        target = Card(name="Tarmogoyf", cost="{1}{G}",
                      type_line="Creature — Lhurgoyf",
                      base_power=4, base_toughness=5)
        filler = Card(name="Forest", cost="", type_line="Basic Land")

        self.p1.library.add(filler)
        self.p1.library.add(target)
        self.p1.library.add(filler)
        self.p1.hand.add(tutor)

        self.game.apply_action({'type': 'cast_spell', 'card': tutor})
        self.game.resolve_stack()

        self.assertIn(target, self.p1.hand.cards)
        self.assertNotIn(target, self.p1.library.cards)

    # ─── Debuff / Pump Spells ──────────────────────────────────────

    def test_pump_spell_positive(self):
        """Target creature gets +N/+N (temporary)."""
        giant_growth = Card(name="Giant Growth", cost="{1}",
                            type_line="Instant",
                            oracle_text="Target creature gets +3/+3 until end of turn.")
        self.assertTrue(giant_growth.is_buff)
        bear = Card(name="Bear", cost="{1}", type_line="Creature — Bear",
                    base_power=2, base_toughness=2)
        bear.controller = self.p1
        self.game.battlefield.add(bear)
        self.p1.hand.add(giant_growth)

        self.game.apply_action({'type': 'cast_spell', 'card': giant_growth})
        self.game.resolve_stack()

        self.assertGreater(bear.power, 2)  # 2 + buff
        self.assertGreater(bear.toughness, 2)  # 2 + buff

    def test_pump_spell_negative(self):
        """Target creature gets -N/-N (removal variant)."""
        grasp = Card(name="Grasp of Darkness", cost="{1}",
                     type_line="Instant",
                     oracle_text="Target creature gets -4/-4 until end of turn.")
        self.assertTrue(grasp.is_removal)
        bear = Card(name="Bear", cost="{1}", type_line="Creature — Bear",
                    base_power=2, base_toughness=2)
        bear.controller = self.p2
        self.game.battlefield.add(bear)
        self.p1.hand.add(grasp)

        self.game.apply_action({'type': 'cast_spell', 'card': grasp})
        self.game.resolve_stack()

        # Bear should have -2/-2 effective stats (killed by SBA)
        self.assertEqual(bear.toughness, -2)  # 2 - 4

    # ─── Scry ──────────────────────────────────────────────────────

    def test_scry_parsing(self):
        """Parse scry N from oracle text."""
        opt = Card(name="Opt", cost="{U}",
                   type_line="Instant",
                   oracle_text="Scry 1, then draw a card.")
        self.assertEqual(opt.scry_amount, 1)

    def test_scry_execution(self):
        """Scry reorders: keeps spells on top, bottoms lands."""
        # Set up library: land, creature, land
        land1 = Card(name="Forest1", cost="", type_line="Basic Land")
        creature = Card(name="Bear", cost="{1}", type_line="Creature — Bear",
                        base_power=2, base_toughness=2)
        land2 = Card(name="Forest2", cost="", type_line="Basic Land")

        self.p1.library.cards = [land1, creature, land2]  # top to bottom
        self.p1.scry(3)

        # After scry 3: creature should be on top, excess land on bottom
        self.assertEqual(self.p1.library.cards[0].name, "Bear",
                         "Creature should be on top after scry")

    # ─── Flashback ─────────────────────────────────────────────────

    def test_flashback_parsing(self):
        """Parse flashback cost from oracle text."""
        bolt = Card(name="Firebolt", cost="{R}",
                    type_line="Sorcery",
                    oracle_text="Firebolt deals 2 damage to any target.\\nFlashback {4}{R}")
        self.assertEqual(bolt.flashback_cost, "{4}{R}")

    def test_flashback_availability(self):
        """Flashback action offered from graveyard."""
        bolt = Card(name="Firebolt", cost="{R}",
                    type_line="Sorcery",
                    oracle_text="Firebolt deals 2 damage to any target.\\nFlashback {1}")
        bolt.controller = self.p1
        self.p1.graveyard.add(bolt)

        legal = self.game.get_legal_actions()
        fb_actions = [a for a in legal if a.get('from_zone') == 'Graveyard' and a.get('card') == bolt]
        self.assertTrue(len(fb_actions) > 0, "Should offer flashback action")

    def test_flashback_exile(self):
        """Card exiled after flashback resolution."""
        bolt = Card(name="Firebolt", cost="{R}",
                    type_line="Sorcery",
                    oracle_text="Firebolt deals 2 damage to any target.\\nFlashback {1}")
        bolt.controller = self.p1
        self.p1.graveyard.add(bolt)

        self.game.apply_action({
            'type': 'cast_spell', 'card': bolt,
            'from_graveyard': True, 'cost_override': '{1}'
        })
        self.game.resolve_stack()

        # Should NOT be in graveyard (exiled)
        self.assertNotIn(bolt, self.p1.graveyard.cards)
        # Should NOT be on battlefield
        self.assertNotIn(bolt, self.game.battlefield.cards)

    # ─── Aura Parsing (already from Tier 2, regression check) ─────

    def test_aura_parsing_regression(self):
        """Aura parsing from Tier 2 should still work."""
        unholy = Card(name="Unholy Strength", cost="{B}",
                      type_line="Enchantment — Aura",
                      oracle_text="Enchant creature\\nEnchanted creature gets +2/+1.")
        self.assertTrue(unholy.is_aura)
        self.assertEqual(unholy.enchant_target_type, "creature")


if __name__ == '__main__':
    unittest.main()
