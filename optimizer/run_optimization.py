"""Run Optimization — Legacy v1 genetic optimizer (mono-red only).

This was the original prototype optimizer that evolves decks against a
goldfish opponent. Superseded by `optimizer/genetic.py` which supports
all 25 color combinations and uses the HeuristicAgent.
"""

import random
from typing import List
from engine.deck import Deck
from engine.card import Card
from engine.game import Game
from engine.player import Player
from simulation.runner import SimulationRunner
from agents.goldfish import GoldfishAgent
import json

class GeneticOptimizer:
    def __init__(self, card_pool: List[dict], population_size=20, generations=10):
        self.card_pool = card_pool
        self.population_size = population_size
        self.generations = generations
        self.population: List[Deck] = []
        
    def generate_initial_population(self):
        for _ in range(self.population_size):
            deck = self._create_random_deck()
            self.population.append(deck)
            
    def _create_random_deck(self) -> Deck:
        deck = Deck()
        # Ensure 24 lands
        mountains = next(c for c in self.card_pool if c['name'] == 'Mountain')
        mountain_card = self._dict_to_card(mountains)
        deck.add_card(mountain_card, 24)
        
        # Add 36 random non-lands
        non_lands = [c for c in self.card_pool if 'Land' not in c['type_line']]
        for _ in range(36):
            card_data = random.choice(non_lands)
            deck.add_card(self._dict_to_card(card_data), 1)
            
        return deck
        
    def _dict_to_card(self, data: dict) -> Card:
        # Helper to convert JSON dict to Card object
        # We need to handle power/toughness carefully as they can be "*"
        power = None
        toughness = None
        if 'power' in data:
            try:
                power = int(data['power'])
            except (ValueError, TypeError):
                power = 0
        if 'toughness' in data:
            try:
                toughness = int(data['toughness'])
            except (ValueError, TypeError):
                toughness = 0
                
        return Card(
            name=data['name'],
            cost=data.get('mana_cost', ''),
            type_line=data.get('type_line', ''),
            oracle_text=data.get('oracle_text', ''),
            base_power=power,
            base_toughness=toughness
        )

    def evaluate_fitness(self, deck: Deck) -> float:
        # Run X games against Goldfish
        agent = GoldfishAgent()
        
        # Opponent is dummy
        from agents.base_agent import BaseAgent
        class PassAgent(BaseAgent):
             def get_action(self, g, p): return {'type': 'pass'}
        
        fitness_sum = 0
        num_games = 5 # Small sample for speed
        
        for _ in range(num_games):
            player = Player("Hero", deck)
            opponent = Player("Dummy", Deck()) # Empty deck for dummy
            game = Game([player, opponent])
            runner = SimulationRunner(game, [agent, PassAgent()])
            result = runner.run()
            
            # Penalize losses (though unlikely against goldfish)
            if result.winner == "Hero":
                # High fitness for low turns. Max turns ~20.
                fitness_sum += (30 - result.turns) 
            else:
                fitness_sum += 0 
                
        return fitness_sum / num_games

    def evolve(self):
        self.generate_initial_population()
        
        for gen in range(self.generations):
            print(f"Generation {gen+1}/{self.generations}")
            
            # Evaluate all
            scores = []
            for i, deck in enumerate(self.population):
                score = self.evaluate_fitness(deck)
                scores.append((score, deck))
            
            # Sort by fitness (descending)
            scores.sort(key=lambda x: x[0], reverse=True)
            
            best_score = scores[0][0]
            print(f"  Best Fitness: {best_score:.2f} (Avg Turns: {30-best_score:.2f})")
            
            # Select top 50%
            survivors = [x[1] for x in scores[:self.population_size//2]]
            
            # Breed new population
            new_population = [s for s in survivors] # Elitism (keep top half)
            
            while len(new_population) < self.population_size:
                parent1 = random.choice(survivors)
                parent2 = random.choice(survivors)
                child = self._crossover(parent1, parent2)
                self._mutate(child)
                new_population.append(child)
                
            self.population = new_population

        return self.population[0] # Return best

    def _crossover(self, p1: Deck, p2: Deck) -> Deck:
        child = Deck()
        # Lands (assume mono red mountain for now)
        mountains = next(c for c in self.card_pool if c['name'] == 'Mountain')
        child.add_card(self._dict_to_card(mountains), 24)
        
        # Non-lands crossover
        p1_spells = [c for c in p1.maindeck if not c.is_land]
        p2_spells = [c for c in p2.maindeck if not c.is_land]
        
        # Shuffle logic to pick random subset
        combined = p1_spells[:18] + p2_spells[18:]
        if len(combined) < 36: # If shortage, fill with p1
            combined += p1_spells[len(combined):]
            
        for c in combined[:36]:
            child.add_card(c)
        
        return child
        
    def _mutate(self, deck: Deck):
        # Swap 1-3 cards with random cards from pool
        num_mutations = random.randint(1, 4)
        non_lands = [c for c in deck.maindeck if not c.is_land]
        
        if not non_lands: return
            
        for _ in range(num_mutations):
            if len(non_lands) == 0: break
            
            # Remove a random card
            target = random.choice(non_lands)
            if target in deck.maindeck:
                deck.maindeck.remove(target)
            
            # Add a random card from pool
            new_card_data = random.choice([c for c in self.card_pool if 'Land' not in c['type_line']])
            deck.add_card(self._dict_to_card(new_card_data))

def run_optimizer():
    # Load processed cards
    with open('../data/processed_cards.json', 'r') as f:
        all_cards = json.load(f)
        
    # Filter only Red cards for now (Simplifies mana base)
    red_pool = [c for c in all_cards if 'R' in c.get('mana_cost', '') and len(c.get('colors', [])) == 1]
    
    # Also add Mountain
    mountain = next(c for c in all_cards if c['name'] == 'Mountain')
    red_pool.append(mountain)
    
    print(f"Optimizer Pool Size: {len(red_pool)} red cards")
    
    optimizer = GeneticOptimizer(red_pool, population_size=20, generations=10)
    best_deck = optimizer.evolve()
    
    print("--- Best Deck Found ---")
    card_counts = {}
    for card in best_deck.maindeck:
        card_counts[card.name] = card_counts.get(card.name, 0) + 1
    
    for name, count in sorted(card_counts.items(), key=lambda item: item[1], reverse=True):
        print(f"{count}x {name}")

if __name__ == "__main__":
    run_optimizer()
