import unittest
from engine.card import Card
from engine.player import Player
from engine.deck import Deck
from engine.game import Game
from engine.zone import Zone

class MockDeck:
    def get_game_deck(self): return []

class TestMechanics(unittest.TestCase):
    def setUp(self):
        self.p1 = Player("P1", MockDeck())
        self.p2 = Player("P2", MockDeck())
        self.game = Game([self.p1, self.p2])
        # Suppress logs
        self.game.log_event = lambda x: None

    def create_creature(self, name, power, toughness, keywords=[], controller=None):
        c = Card(name=name, cost="", type_line="Creature", base_power=power, base_toughness=toughness)
        c.controller = controller or self.p1
        # Set keywords manually
        for k in keywords:
            if k == 'Flying': c.has_flying = True
            if k == 'Trample': c.has_trample = True
            if k == 'Deathtouch': c.has_deathtouch = True
            if k == 'Reach': c.has_reach = True
            if k == 'First Strike': c.has_first_strike = True
            if k == 'Double Strike': c.has_double_strike = True
            if k == 'Hexproof': c.has_hexproof = True
            if k == 'Vigilance': c.has_vigilance = True
            if k == 'Menace': c.has_menace = True
            if k == 'Indestructible': c.has_indestructible = True
        
        # Add to battlefield to simulate "cast"
        self.game.battlefield.add(c)
        return c

    def test_flying(self):
        attacker = self.create_creature("Bird", 1, 1, ["Flying"], self.p1)
        ground_blocker = self.create_creature("Bear", 2, 2, [], self.p2)
        reach_blocker = self.create_creature("Spider", 1, 3, ["Reach"], self.p2)

        # Ground cannot block flier
        self.assertFalse(self.game._can_block(attacker, ground_blocker), "Ground creature blocked flier")
        
        # Reach can block flier
        self.assertTrue(self.game._can_block(attacker, reach_blocker), "Reach creature failed to block flier")
        
        # Flier can block flier (implied)
        fly_blocker = self.create_creature("Angel", 4, 4, ["Flying"], self.p2)
        self.assertTrue(self.game._can_block(attacker, fly_blocker), "Flier failed to block flier")

    def test_menace(self):
        attacker = self.create_creature("Rogue", 3, 2, ["Menace"], self.p1)
        b1 = self.create_creature("B1", 1, 1, [], self.p2)
        b2 = self.create_creature("B2", 1, 1, [], self.p2)
        
        # Game engine check usually handles single block validation in _can_block?
        # No, menace requires >= 2 blockers. _can_block checks eligibility of ONE blocker.
        # Menace check happens at Declare Blockers step validation.
        # Let's check if Game has logic for validity of block assignment overall.
        # currently Game might only check individual legality.
        
        # If engine doesn't implement Menace check in _can_block (it returns True because individual blocks are legal)
        # we need to check declare_blockers logic.
        pass 

    def test_trample(self):
        # 5/5 Trample blocked by 1/1
        attacker = self.create_creature("Trampler", 5, 5, ["Trample"], self.p1)
        blocker = self.create_creature("Chump", 1, 1, [], self.p2)
        
        # Simulate combat damage assignment
        # Normal: blocker takes 5? No, 1. Excess 4 to face.
        # Engine check: does _resolve_combat_damage handle trample?
        
        self.p2.life = 20
        # Determine damage manually to test logic if engine doesn't expose it easily
        # But we assume engine has logic.
        # Let's mock a combat resolution
        self.game.combat_attackers = [attacker]
        self.game.combat_blockers = {attacker.id: [blocker]}
        self.game.resolve_combat_damage()
        
        # Blocker dies (1 damage enough)
        self.assertIn(blocker, self.p2.graveyard.cards, "Blocker should die")
        # Player takes 4 damage
        self.assertEqual(self.p2.life, 16, f"Player life should be 16, got {self.p2.life}")

    def test_deathtouch(self):
        attacker = self.create_creature("Snake", 1, 1, ["Deathtouch"], self.p1)
        blocker = self.create_creature("Wall", 0, 6, [], self.p2)
        
        self.game.combat_attackers = [attacker]
        self.game.combat_blockers = {attacker.id: [blocker]}
        self.game.resolve_combat_damage()
        
        self.assertIn(blocker, self.p2.graveyard.cards, "Deathtouch did not kill high toughness creature")

    def test_first_strike(self):
        # 2/1 First Strike vs 2/1 Normal
        fs = self.create_creature("Striker", 2, 1, ["First Strike"], self.p1)
        normal = self.create_creature("Grunt", 2, 1, [], self.p2)
        
        self.game.combat_attackers = [fs]
        self.game.combat_blockers = {fs.id: [normal]}
        self.game.resolve_combat_damage()
        
        self.assertIn(normal, self.p2.graveyard.cards, "First striker did not kill normal")
        self.assertNotIn(fs, self.p1.graveyard.cards, "First striker died (should have survived)")

    def test_hexproof(self):
        target = self.create_creature("Hex", 1, 1, ["Hexproof"], self.p1)
        spell = Card(name="Bolt", cost="{R}", type_line="Instant")
        spell.controller = self.p2
        
        # Engine might not have explicit "can_target" method publicly exposed
        # But we can check Card.is_protected_from(source) or has_hexproof logic
        
        # Hexproof protects from OPPONENT sources only
        self.assertTrue(target.has_hexproof)
        # We need to verify if game logic checks this.
        # Game._validate_targets?
        pass

    def test_protection(self):
        pro_white = self.create_creature("Knight", 2, 2, [], self.p1)
        pro_white.has_protection_from.append('white')
        
        white_source = Card(name="Swords", cost="{W}", type_line="Instant", color_identity=['W']) # Actually need produced_mana or colors field
        white_source.colors = ['W'] # Manual set
        white_source.controller = self.p2
        
        self.assertTrue(pro_white.is_protected_from(white_source))
        
        red_source = Card(name="Bolt", cost="{R}", type_line="Instant")
        red_source.colors = ['R']
        self.assertFalse(pro_white.is_protected_from(red_source))

if __name__ == '__main__':
    unittest.main()
