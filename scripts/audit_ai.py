import logging
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card import Card
from agents.heuristic_agent import HeuristicAgent

# Configure logging to capture the game log
logging.basicConfig(level=logging.INFO, format='%(message)s')

def test_fetchland_holding():
    print("\n--- TEST: Fetchland Holding ---")
    p1 = Player("Spike", Deck())
    p2 = Player("Johnny", Deck())
    game = Game([p1, p2])
    
    # Give Player 1 a fetchland
    fetchland = Card("Arid Mesa", "", "Land", "{T}, Pay 1 life, Sacrifice Arid Mesa: Search your library for a Mountain or Plains card, put it onto the battlefield, then shuffle.")
    fetchland.is_fetchland = True
    
    mountain = Card("Mountain", "", "Basic Land - Mountain", "{T}: Add {R}.", produced_mana=["R"])
    p1.library.add(mountain)
    p1.hand.add(fetchland)
    
    agent = HeuristicAgent()
    
    # Force state to Spike's Main Phase
    game.active_player_index = 0
    game.current_phase = 'Main 1'
    
    action = agent.get_action(game, p1)
    print(f"Spike's Action in Main Phase with Fetchland in hand: {action['type']}")
    
    # Play the land manually to test the next state
    if action['type'] == 'play_land':
        p1.hand.remove(fetchland)
        game.battlefield.add(fetchland)
        fetchland.controller = p1
    
    action2 = agent.get_action(game, p1)
    print(f"Spike's Action after playing Fetchland: {action2['type']}")
    if action2['type'] == 'pass':
        print("SUCCESS! Spike held the fetchland.")
        
    # Force state to Johnny's End Step
    game.active_player_index = 1
    game.current_phase = 'End'
    action3 = agent.get_action(game, p1)
    print(f"Spike's Action in Johnny's End Step: {action3['type']}")
    if action3['type'] in ('activate', 'sacrifice_ability'):
        print("SUCCESS! Spike cracked the fetchland on the End Step.")
        

def test_baiting_logic():
    print("\n--- TEST: Baiting Logic ---")
    p1 = Player("Spike", Deck())
    p2 = Player("Johnny", Deck())
    game = Game([p1, p2])
    
    # Spike has two spells, one is a wincon, one is low value
    oracle = Card("Thassa's Oracle", "{U}{U}", "Creature", "Win the game.")
    opt = Card("Opt", "{U}", "Instant", "Draw a card.")
    
    p1.hand.add(oracle)
    p1.hand.add(opt)
    
    # Spike has 3 mana available
    for _ in range(3):
        island = Card("Island", "", "Basic Land - Island", "{T}: Add {U}.", produced_mana=["U"])
        island.controller = p1
        game.battlefield.add(island)
        
    # Johnny has 2 Islands UNTAPPED (threatening Counterspell)
    for _ in range(2):
        opp_island = Card("Island", "", "Basic Land - Island", "{T}: Add {U}.", produced_mana=["U"])
        opp_island.controller = p2
        game.battlefield.add(opp_island)
        
    game.active_player_index = 0
    game.current_phase = 'Main 1'
    
    agent = HeuristicAgent()
    
    # What does the agent play first?
    action = agent.get_action(game, p1)
    
    if action['type'] == 'cast':
        print(f"Spike cast {action['card'].name} first.")
        if action['card'].name == "Opt":
            print("SUCCESS! Spike baited the counterspell with Opt.")
        else:
            print("FAILED! Spike ran the Oracle into the counterspell.")

if __name__ == "__main__":
    test_fetchland_holding()
    test_baiting_logic()
