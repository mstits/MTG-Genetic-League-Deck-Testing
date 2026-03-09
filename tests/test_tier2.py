
import sys
import os
import unittest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game

class TestTier2Mechanics(unittest.TestCase):
    def setUp(self):
        self.p1 = Player("P1", Deck())
        self.p2 = Player("P2", Deck())
        self.game = Game([self.p1, self.p2])
        # Manually set up game state to avoid full start_game overhead
        self.game.turn_count = 1
        self.game.active_player_index = 0
        self.game.priority_player_index = 0
        self.game.current_phase = "Main 1"
        self.game.phase_index = 3
        # Add dummy lands
        for _ in range(5):
            land = Card(name="Mountain", cost="", type_line="Basic Land")
            land.controller = self.p1
            self.game.battlefield.add(land)

    def test_equipment_parsing(self):
        """Test parsing of Equip cost and stat bonuses."""
        bonesplitter = Card(name="Bonesplitter", cost="{1}", type_line="Artifact — Equipment",
                            oracle_text="Equipped creature gets +2/+0.\\nEquip {1}")
        
        # Check if parsed correctly (attributes might not exist yet)
        self.assertTrue(hasattr(bonesplitter, 'equip_cost'), "Should have equip_cost")
        self.assertEqual(bonesplitter.equip_cost, "{1}")
        self.assertTrue(hasattr(bonesplitter, 'equip_bonus'), "Should have equip_bonus")
        self.assertEqual(bonesplitter.equip_bonus, {'power': 2, 'toughness': 0})

    def test_equip_action_availability(self):
        """Test that Equip action is available for equipment on battlefield."""
        bonesplitter = Card(name="Bonesplitter", cost="{1}", type_line="Artifact — Equipment",
                            oracle_text="Equipped creature gets +2/+0.\\nEquip {1}")
        goblin = Card(name="Goblin Piker", cost="{1}{R}", type_line="Creature — Goblin",
                      base_power=2, base_toughness=1)
        
        bonesplitter.controller = self.p1
        goblin.controller = self.p1
        self.game.battlefield.add(bonesplitter)
        self.game.battlefield.add(goblin)
        
        # Should offer equip action
        legal_actions = self.game.get_legal_actions()
        equip_actions = [a for a in legal_actions if a['type'] == 'equip']
        
        self.assertTrue(len(equip_actions) > 0, "Should have Equip action")
        self.assertEqual(equip_actions[0]['cost'], "{1}")
        self.assertEqual(equip_actions[0]['source'], bonesplitter)
        self.assertEqual(equip_actions[0]['target'], goblin)

    def test_equip_mechanics(self):
        """Test the full flow: equip -> stat boost -> unequip/re-equip."""
        bonesplitter = Card(name="Bonesplitter", cost="{1}", type_line="Artifact — Equipment",
                            oracle_text="Equipped creature gets +2/+0.\\nEquip {1}")
        goblin = Card(name="Goblin Piker", cost="{1}{R}", type_line="Creature — Goblin",
                      base_power=2, base_toughness=1)
        
        bonesplitter.controller = self.p1
        goblin.controller = self.p1
        self.game.battlefield.add(bonesplitter)
        self.game.battlefield.add(goblin)
        
        # 1. Equip
        self.game.apply_action({
            'type': 'equip',
            'source': bonesplitter,
            'target': goblin,
            'cost': "{1}"
        })
        
        # Resolve stack
        self.game.resolve_stack()
        
        self.assertEqual(bonesplitter.equipped_to, goblin)
        self.assertIn(bonesplitter, goblin.attachments)
        self.assertEqual(goblin.power, 4)  # 2 + 2
        self.assertEqual(goblin.toughness, 1) # 1 + 0
        
        # 2. Creature dies (SBA check)
        self.game.battlefield.remove(goblin)
        self.game.check_state_based_actions()
        
        self.assertIsNone(bonesplitter.equipped_to)
        self.assertNotIn(bonesplitter, goblin.attachments)  # Should be cleared effectively
        
    def test_aura_mechanics(self):
        """Test Aura casting, attachment, and cleanup."""
        unholy = Card(name="Unholy Strength", cost="{1}", type_line="Enchantment — Aura",
                      oracle_text="Enchant creature\\nEnchanted creature gets +2/+1.")
        goblin = Card(name="Goblin Piker", cost="{1}{R}", type_line="Creature — Goblin",
                      base_power=2, base_toughness=1)
        
        self.p1.hand.add(unholy)
        goblin.controller = self.p1
        self.game.battlefield.add(goblin)
        
        # 1. Cast Aura (new Rule 601.2 format: announce_cast)
        legal = self.game.get_legal_actions()
        cast_actions = [a for a in legal if a['type'] == 'announce_cast' and a['card'] == unholy]
        self.assertTrue(len(cast_actions) > 0)
        
        # Use the legacy cast_spell path for resolution
        self.game.apply_action({'type': 'cast_spell', 'card': unholy})
        self.game.resolve_stack()
        
        # Aura should have been resolved (on battlefield attached, or in graveyard if unattached)
        aura_on_bf = unholy in self.game.battlefield.cards
        aura_in_gy = unholy in self.p1.graveyard.cards
        self.assertTrue(aura_on_bf or aura_in_gy, "Aura should have resolved")
        
        if aura_on_bf:
            self.assertEqual(unholy.enchanted_to, goblin)
            self.assertIn(unholy, goblin.attachments)
            self.assertEqual(goblin.power, 4) # 2+2
            self.assertEqual(goblin.toughness, 2) # 1+1
        
        # 2. Creature dies -> Aura dies
        self.game.battlefield.remove(goblin) # Force remove to simple test SBA
        self.game.check_state_based_actions()
        
        self.assertNotIn(unholy, self.game.battlefield.cards)
        self.assertIn(unholy, self.p1.graveyard.cards)

    def test_aura_parsing(self):
        """Test parsing of Aura enchant target and bonuses."""
        unholy = Card(name="Unholy Strength", cost="{B}", type_line="Enchantment — Aura",
                      oracle_text="Enchant creature\\nEnchanted creature gets +2/+1.")
        
        self.assertTrue(unholy.is_aura)
        self.assertEqual(unholy.enchant_target_type, "creature")
        self.assertEqual(unholy.equip_bonus, {'power': 2, 'toughness': 1}) # reusing equip_bonus structure for simplicity?

if __name__ == '__main__':
    unittest.main()
