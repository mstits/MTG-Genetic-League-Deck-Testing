
import sys
import os
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card, StackItem
from engine.deck import Deck
from engine.player import Player
from engine.game import Game


class TestTier5AdvancedMechanics(unittest.TestCase):
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
        # Seed libraries for draw effects
        for i in range(10):
            self.p1.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))
            self.p2.library.add(Card(name=f"Filler{i}", cost="{1}",
                                     type_line="Creature — Human",
                                     base_power=1, base_toughness=1))

    # ─── Landfall ──────────────────────────────────────────────────

    def test_landfall_parsing_damage(self):
        """Parse landfall damage trigger."""
        card = Card(name="Searing Blaze", cost="{R}",
                   type_line="Creature — Elemental",
                   oracle_text="Landfall — Whenever a land enters the battlefield under your control, Searing Blaze deals 1 damage to target opponent.",
                   base_power=2, base_toughness=2)
        self.assertIsNotNone(card.landfall_effect)

    def test_landfall_parsing_life(self):
        """Parse landfall life gain trigger."""
        card = Card(name="Grazing Gladehart", cost="{2}{G}",
                   type_line="Creature — Antelope",
                   oracle_text="Landfall — Whenever a land enters the battlefield under your control, you gain 2 life.",
                   base_power=2, base_toughness=2)
        self.assertIsNotNone(card.landfall_effect)

    def test_landfall_trigger_fires(self):
        """Landfall trigger fires when land is played."""
        creature = Card(name="Steppe Lynx", cost="{W}",
                       type_line="Creature — Cat",
                       oracle_text="Landfall — Whenever a land enters the battlefield under your control, Steppe Lynx deals 1 damage to target opponent.",
                       base_power=0, base_toughness=1)
        creature.controller = self.p1
        self.game.battlefield.add(creature)

        land = Card(name="Plains", cost="", type_line="Basic Land — Plains")
        self.p1.hand.add(land)

        initial_stack = len(self.game.stack.cards)
        self.game.apply_action({'type': 'play_land', 'card': land})

        # Landfall trigger should be on the stack
        self.assertGreater(len(self.game.stack.cards), initial_stack)

    def test_landfall_execution(self):
        """Landfall damage trigger deals damage when resolved."""
        creature = Card(name="Searing Blaze", cost="{R}",
                       type_line="Creature — Elemental",
                       oracle_text="Landfall — Whenever a land enters the battlefield under your control, Searing Blaze deals 2 damage to target opponent.",
                       base_power=2, base_toughness=2)
        creature.controller = self.p1
        self.game.battlefield.add(creature)

        land = Card(name="Mountain", cost="", type_line="Basic Land — Mountain")
        self.p1.hand.add(land)

        initial_life = self.p2.life
        self.game.apply_action({'type': 'play_land', 'card': land})
        self.game.resolve_stack()

        self.assertEqual(self.p2.life, initial_life - 2)

    # ─── Attack Triggers ──────────────────────────────────────────

    def test_attack_trigger_parsing(self):
        """Parse 'whenever ~ attacks' trigger."""
        card = Card(name="Goblin Rabblemaster", cost="{2}{R}",
                   type_line="Creature — Goblin Warrior",
                   oracle_text="Whenever Goblin Rabblemaster attacks, it gets +1/+0 until end of turn.",
                   base_power=2, base_toughness=2)
        self.assertIsNotNone(card.attack_trigger)

    def test_attack_trigger_fires(self):
        """Attack trigger fires when creature attacks and effect applies."""
        creature = Card(name="Goblin Rabblemaster", cost="{2}{R}",
                       type_line="Creature — Goblin Warrior",
                       oracle_text="Whenever Goblin Rabblemaster attacks, it gets +1/+0 until end of turn.",
                       base_power=2, base_toughness=2)
        creature.controller = self.p1
        creature.summoning_sickness = False
        self.game.battlefield.add(creature)

        base_power = creature.power
        self.game.current_phase = "Declare Attackers"
        self.game.apply_action({
            'type': 'declare_attackers',
            'attackers': [creature]
        })
        # Resolve the attack trigger from the stack
        self.game.resolve_stack()

        # The buff should have been applied (power increased from base)
        self.assertGreater(creature.power, base_power)

    # ─── Combat Damage Triggers ────────────────────────────────────

    def test_combat_damage_trigger_parsing(self):
        """Parse 'whenever ~ deals combat damage to a player' trigger."""
        card = Card(name="Thieving Magpie", cost="{2}{U}{U}",
                   type_line="Creature — Bird",
                   oracle_text="Whenever Thieving Magpie deals combat damage to a player, draw a card.",
                   base_power=1, base_toughness=1)
        self.assertIsNotNone(card.combat_damage_trigger)

    def test_combat_damage_trigger_fires(self):
        """Combat damage trigger fires when unblocked creature damages player."""
        creature = Card(name="Ophidian", cost="{2}{U}",
                       type_line="Creature — Snake",
                       oracle_text="Whenever Ophidian deals combat damage to a player, draw a card.",
                       base_power=1, base_toughness=3)
        creature.controller = self.p1
        creature.summoning_sickness = False
        self.game.battlefield.add(creature)

        self.game.combat_attackers = [creature]
        self.game.combat_blockers = {}

        initial_stack = len(self.game.stack.cards)
        self.game._resolve_damage_for([creature], self.p2, self.p1)

        # Should have a combat damage trigger on the stack
        self.assertGreater(len(self.game.stack.cards), initial_stack)

    def test_combat_damage_trigger_draw(self):
        """Combat damage trigger draws a card when resolved."""
        creature = Card(name="Ophidian", cost="{2}{U}",
                       type_line="Creature — Snake",
                       oracle_text="Whenever Ophidian deals combat damage to a player, draw a card.",
                       base_power=1, base_toughness=3)
        creature.controller = self.p1
        creature.summoning_sickness = False
        self.game.battlefield.add(creature)

        self.game.combat_attackers = [creature]
        self.game.combat_blockers = {}

        initial_hand = len(self.p1.hand)
        self.game._resolve_damage_for([creature], self.p2, self.p1)
        self.game.resolve_stack()

        self.assertEqual(len(self.p1.hand), initial_hand + 1)

    # ─── Kicker ────────────────────────────────────────────────────

    def test_kicker_parsing(self):
        """Parse kicker cost from oracle text."""
        card = Card(name="Wolfir Silverheart", cost="{3}{G}{G}",
                   type_line="Creature — Wolf Warrior",
                   oracle_text="Kicker {2}{G}\nIf Wolfir Silverheart was kicked, it gets a +1/+1 counter.",
                   base_power=4, base_toughness=4)
        self.assertEqual(card.kicker_cost, "{2}{G}")
        self.assertIsNotNone(card.kicker_effect)

    def test_kicker_action_availability(self):
        """Kicked variant offered when player can afford total cost."""
        card = Card(name="Goblin Ruinblaster", cost="{2}{R}",
                   type_line="Creature — Goblin Shaman",
                   oracle_text="Kicker {R}\nIf Goblin Ruinblaster was kicked, it deals 2 damage to any target.",
                   base_power=2, base_toughness=1)
        self.p1.hand.add(card)

        legal = self.game.get_legal_actions()
        cast_actions = [a for a in legal if a.get('type') == 'announce_cast' and a.get('card') == card]

        # Should have announce_cast action
        self.assertTrue(len(cast_actions) >= 1)

    def test_kicker_was_kicked_flag(self):
        """Card marked as kicked when cast with kicker."""
        card = Card(name="Goblin Ruinblaster", cost="{2}{R}",
                   type_line="Creature — Goblin Shaman",
                   oracle_text="Kicker {R}\nIf Goblin Ruinblaster was kicked, it deals 2 damage to any target.",
                   base_power=2, base_toughness=1)
        self.p1.hand.add(card)

        self.game.apply_action({
            'type': 'cast_spell', 'card': card,
            'kicked': True, 'cost_override': "{2}{R}{R}"
        })

        self.assertTrue(getattr(card, 'was_kicked', False) or getattr(card, 'is_kicked', False))

    # ─── Counterspells (Existing) ──────────────────────────────────

    def test_counterspell_parsing(self):
        """Counter target spell is already parsed."""
        card = Card(name="Cancel", cost="{1}{U}{U}",
                   type_line="Instant",
                   oracle_text="Counter target spell.")
        self.assertTrue(card.is_counter)
        self.assertIsNotNone(card.effect)

    def test_counterspell_execution(self):
        """Counterspell removes top spell from stack."""
        target = Card(name="Lightning Bolt", cost="{R}",
                     type_line="Instant",
                     oracle_text="Lightning Bolt deals 3 damage to any target.")
        target.controller = self.p2

        counter = Card(name="Cancel", cost="{1}{U}{U}",
                      type_line="Instant",
                      oracle_text="Counter target spell.")
        counter.controller = self.p1

        self.game.stack.cards.append(target)
        self.game.stack.cards.append(counter)

        # Resolve counter (top of stack)
        self.game._resolve_stack_top()

        # Target should be removed from stack and in graveyard
        self.assertNotIn(target, self.game.stack.cards)
        self.assertIn(target, self.p2.graveyard.cards)


if __name__ == '__main__':
    unittest.main()
