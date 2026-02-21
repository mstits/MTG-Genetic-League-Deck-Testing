"""Main — Quick simulation test harness for the MTG engine.

Runs N goldfish simulations in parallel to test engine throughput
and deck performance.  This is the original entry point for testing;
for the full evolutionary league, use `run_league.py` instead.
"""

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from agents.goldfish import GoldfishAgent
from simulation.runner import SimulationRunner
from simulation.stats import StatsCollector
import multiprocessing
import time

def lightning_bolt_effect(game, card):
    # Simplified: Always target opponent
    target = game.opponent
    target.life -= 3
    game.log_event(f"Lightning Bolt deals 3 damage to {target.name}")

def create_sample_deck(name_prefix):
    deck = Deck()
    # 20 Lands, 30 Creatures, 10 Burn Spells
    pk_land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
    pk_creature = Card(name="Goblin Guide", cost="{R}", type_line="Creature - Goblin Scout", base_power=2, base_toughness=2)
    pk_bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant", effect=lightning_bolt_effect)
    
    deck.add_card(pk_land, 20)
    deck.add_card(pk_creature, 30)
    deck.add_card(pk_bolt, 10)
    return deck

def run_single_simulation(_):
    deck1 = create_sample_deck("P1")
    deck2 = create_sample_deck("P2") # Opponent just for presence, goldfish ignores them mostly
    
    player1 = Player("Red Deck 1", deck1)
    player2 = Player("Opponent", deck2) # Dummy opponent
    
    game = Game([player1, player2])
    
    # We want to test Player 1's speed.
    # Opponent does nothing (Passes).
    agent1 = GoldfishAgent() 
    
    # Simple 'pass' agent for opponent to speed things up
    from agents.base_agent import BaseAgent
    class PassAgent(BaseAgent):
        def get_action(self, game, player):
            return {'type': 'pass'}
            
    agent2 = PassAgent()
    
    runner = SimulationRunner(game, [agent1, agent2])
    return runner.run()

def main():
    print("MTG Deck Testing Tool Initialized")
    
    num_simulations = 1000
    start_time = time.time()
    
    print(f"Running {num_simulations} simulations...")
    
    # Sequential for debugging, parallel for speed
    # results = [run_single_simulation(i) for i in range(num_simulations)]

    with multiprocessing.Pool() as pool:
        results = pool.map(run_single_simulation, range(num_simulations))
        
    duration = time.time() - start_time
    print(f"Completed in {duration:.2f} seconds ({num_simulations/duration:.0f} games/sec)")
    
    stats = StatsCollector()
    for res in results:
        stats.add_result(res)
    stats.print_summary()

if __name__ == "__main__":
    main()
