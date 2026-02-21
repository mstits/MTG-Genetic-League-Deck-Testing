"""Manual test — Validates mana parsing and payment logic for complex lands.

Tests dual lands (Adarkar Wastes, City of Brass) and the backtracking
mana payment solver against various cost requirements.
"""

from engine.card_builder import dict_to_card

def test_adarkar():
    data = {
        'name': 'Adarkar Wastes',
        'type_line': 'Land',
        'oracle_text': '{T}: Add {C}.\n{T}, Pay 1 life: Add {W} or {U}.',
        'produced_mana': [] # Simulate missing data
    }
    card = dict_to_card(data)
    print(f"Adarkar Wastes produced_mana: {card.produced_mana}")

def test_city_of_brass():
    data = {
        'name': 'City of Brass',
        'type_line': 'Land',
        'oracle_text': 'Whenever City of Brass becomes tapped, it deals 1 damage to you.\n{T}: Add one mana of any color.',
        'produced_mana': []
    }
    card = dict_to_card(data)
    print(f"City of Brass produced_mana: {card.produced_mana}")
    return card

def test_payment():
    print("\n--- Testing Payment Logic ---")
    from engine.player import Player
    from engine.deck import Deck
    from engine.game import Game
    
    # Mock mocks
    class MockDeck:
        def get_game_deck(self): return []
    
    p = Player("TestBot", MockDeck())
    
    # 1. Test Adarkar Wastes (C, W, U) + Plains (W) paying {1}{W}{U}
    adarkar_data = {
        'name': 'Adarkar Wastes', 'type_line': 'Land',
        'oracle_text': '{T}: Add {C}.\n{T}: Add {W} or {U}.',
        'produced_mana': [] 
    }
    adarkar = dict_to_card(adarkar_data)
    
    plains_data = {'name': 'Plains', 'type_line': 'Basic Land — Plains', 'produced_mana': ['W']}
    plains = dict_to_card(plains_data)
    
    # Mock game object for play_land
    class MockBattlefield:
        def __init__(self): self.cards = []
        def add(self, card): self.cards.append(card)
        
    bf = MockBattlefield()
    game = type('MockGame', (), {'battlefield': bf, 'players': [p], 'log_event': lambda x: None})()
    
    p.play_land(adarkar, game)
    p.lands_played_this_turn = 0 # Cheat to play second land
    p.play_land(plains, game)
    
    # Test 1: Pay {1}{W}{U} -> Needs Adarkar for U, Plains for W, Adarkar for 1? No.
    # Total available: W + (C/W/U). Max 2 mana.
    # Cost {1}{W}{U} = 3 mana. Should FAIL.
    print(f"Can pay {{1}}{{W}}{{U}} with 2 lands? {p.can_pay_cost('{1}{W}{U}', game)} (Expected: False)")
    
    # Test 2: Pay {W}{U} -> Adarkar(U) + Plains(W). Should PASS.
    print(f"Can pay {{W}}{{U}}? {p.can_pay_cost('{W}{U}', game)} (Expected: True)")
    
    # Test 3: Pay {W}{W} -> Adarkar(W) + Plains(W). Should PASS.
    print(f"Can pay {{W}}{{W}}? {p.can_pay_cost('{W}{W}', game)} (Expected: True)")
    
    # Test 4: Pay {2} -> C + W. Should PASS.
    print(f"Can pay {{2}}? {p.can_pay_cost('{2}', game)} (Expected: True)")
    
    # Test 5: Pay {U}{U} -> Adarkar(U) + Plains(cannot). Should FAIL.
    print(f"Can pay {{U}}{{U}}? {p.can_pay_cost('{U}{U}', game)} (Expected: False)")

if __name__ == "__main__":
    test_adarkar()
    test_city_of_brass()
    test_payment()
