
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card, StackItem
from engine.deck import Deck
from engine.player import Player
from engine.game import Game


class TestTier7CombatEconomy(unittest.TestCase):
    def setUp(self):
        self.p1 = Player("P1", Deck())
        self.p2 = Player("P2", Deck())
        self.game = Game([self.p1, self.p2])
        self.game.turn_count = 1
        self.game.active_player_index = 0
        self.game.priority_player_index = 0
        self.game.current_phase = "Main 1"
        self.game.phase_index = 3
        for _ in range(8):
            land = Card(name="Island", cost="", type_line="Basic Land")
            land.controller = self.p1
            self.game.battlefield.add(land)
        for i in range(15):
            self.p1.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))
            self.p2.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))

    # ─── Vehicles / Crew ───────────────────────────────────────────

    def test_vehicle_parsing(self):
        """Parse Vehicle type and Crew N."""
        card = Card(name="Heart of Kiran", cost="{2}",
                   type_line="Legendary Artifact — Vehicle",
                   base_power=4, base_toughness=4,
                   oracle_text="Flying, vigilance\nCrew 3")
        self.assertTrue(card.is_vehicle)
        self.assertEqual(card.crew_cost, 3)

    def test_crew_action_availability(self):
        """Crew action offered when total creature power >= crew cost."""
        vehicle = Card(name="Smuggler's Copter", cost="{2}",
                      type_line="Artifact — Vehicle",
                      base_power=3, base_toughness=3,
                      oracle_text="Crew 1")
        vehicle.controller = self.p1
        self.game.battlefield.add(vehicle)

        creature = Card(name="Soldier", cost="{W}",
                       type_line="Creature — Human Soldier",
                       base_power=2, base_toughness=2)
        creature.controller = self.p1
        self.game.battlefield.add(creature)

        legal = self.game.get_legal_actions()
        crew_actions = [a for a in legal if a.get('type') == 'crew']
        self.assertEqual(len(crew_actions), 1)
        self.assertEqual(crew_actions[0]['vehicle'], vehicle)

    def test_crew_execution(self):
        """Crewing taps creatures and marks vehicle as crewed."""
        vehicle = Card(name="Smuggler's Copter", cost="{2}",
                      type_line="Artifact — Vehicle",
                      base_power=3, base_toughness=3,
                      oracle_text="Crew 1")
        vehicle.controller = self.p1
        self.game.battlefield.add(vehicle)

        creature = Card(name="Soldier", cost="{W}",
                       type_line="Creature — Human Soldier",
                       base_power=2, base_toughness=2)
        creature.controller = self.p1
        self.game.battlefield.add(creature)

        self.game.apply_action({'type': 'crew', 'vehicle': vehicle})
        self.assertTrue(vehicle.is_crewed)
        self.assertTrue(creature.tapped)

    def test_crew_uncrews_at_cleanup(self):
        """Vehicle un-crews during cleanup step."""
        vehicle = Card(name="Copter", cost="{2}",
                      type_line="Artifact — Vehicle",
                      base_power=3, base_toughness=3,
                      oracle_text="Crew 1")
        vehicle.controller = self.p1
        vehicle.is_crewed = True
        self.game.battlefield.add(vehicle)

        self.game._do_cleanup()
        self.assertFalse(vehicle.is_crewed)

    # ─── Prowess ───────────────────────────────────────────────────

    def test_prowess_parsing(self):
        """Parse prowess keyword."""
        card = Card(name="Monastery Swiftspear", cost="{R}",
                   type_line="Creature — Human Monk",
                   base_power=1, base_toughness=2,
                   oracle_text="Haste\nProwess")
        self.assertTrue(card.has_prowess)
        self.assertTrue(card.has_haste)

    def test_prowess_triggers_on_noncreature_cast(self):
        """Prowess gives +1/+1 when noncreature spell is cast."""
        monk = Card(name="Monastery Swiftspear", cost="{R}",
                   type_line="Creature — Human Monk",
                   base_power=1, base_toughness=2,
                   oracle_text="Prowess")
        monk.controller = self.p1
        self.game.battlefield.add(monk)

        spell = Card(name="Lightning Bolt", cost="{R}",
                    type_line="Instant",
                    oracle_text="Lightning Bolt deals 3 damage to any target.")
        self.p1.hand.add(spell)

        self.game.apply_action({'type': 'cast_spell', 'card': spell})

        # Prowess should have added a temp modifier
        self.assertTrue(len(monk._temp_modifiers) > 0)

    # ─── Bounce Spells ─────────────────────────────────────────────

    def test_bounce_parsing(self):
        """Parse bounce spell."""
        card = Card(name="Unsummon", cost="{U}",
                   type_line="Instant",
                   oracle_text="Return target creature to its owner's hand.")
        self.assertTrue(card.is_bounce)
        self.assertIsNotNone(card.effect)

    def test_bounce_execution(self):
        """Bounce returns creature to opponent's hand."""
        card = Card(name="Unsummon", cost="{U}",
                   type_line="Instant",
                   oracle_text="Return target creature to its owner's hand.")
        card.controller = self.p1

        enemy = Card(name="Dragon", cost="{4}{R}{R}",
                    type_line="Creature — Dragon",
                    base_power=5, base_toughness=5)
        enemy.controller = self.p2
        self.game.battlefield.add(enemy)

        card.effect(self.game, card)

        self.assertNotIn(enemy, self.game.battlefield.cards)
        self.assertIn(enemy, self.p2.hand.cards)

    # ─── Sacrifice-a-Creature ──────────────────────────────────────

    def test_sac_creature_damage(self):
        """Sacrifice a creature, deal damage."""
        card = Card(name="Bone Splinters", cost="{B}",
                   type_line="Sorcery",
                   oracle_text="As an additional cost to cast this spell, sacrifice a creature. Deal 3 damage to any target.")
        card.controller = self.p1

        own = Card(name="Zombie", cost="{1}{B}",
                  type_line="Creature — Zombie",
                  base_power=2, base_toughness=2)
        own.controller = self.p1
        self.game.battlefield.add(own)

        card.effect(self.game, card)
        self.assertNotIn(own, self.game.battlefield.cards)

    # ─── Treasure Tokens ───────────────────────────────────────────

    def test_treasure_parsing(self):
        """Parse treasure token creation."""
        card = Card(name="Pirate's Pillage", cost="{3}{R}",
                   type_line="Sorcery",
                   oracle_text="Draw two cards. Create two Treasure tokens.")
        self.assertIsNotNone(card.effect)

    def test_treasure_execution(self):
        """Treasure tokens are created on battlefield."""
        card = Card(name="Treasure Maker", cost="{2}",
                   type_line="Sorcery",
                   oracle_text="Create two Treasure tokens.")
        card.controller = self.p1

        bf_before = len(self.game.battlefield)
        card.effect(self.game, card)
        bf_after = len(self.game.battlefield)

        self.assertEqual(bf_after - bf_before, 2)
        treasures = [c for c in self.game.battlefield.cards if c.name == "Treasure"]
        self.assertEqual(len(treasures), 2)
        self.assertTrue(all(t.is_treasure for t in treasures))


if __name__ == '__main__':
    unittest.main()
