"""sovereign.py — The Sovereign: Meta-Evolution Orchestrator.

Top-level controller that combines:
1. Distributed Genetic Algorithm (DGA) for deck evolution
2. Neural MCTS agents for pro-level gameplay
3. Elo-weighted fitness with novelty scoring
4. Meta analysis and anomaly detection

Usage:
    python sovereign.py --format standard --generations 50 --population 100
    python sovereign.py --format commander --cEDH --generations 20
    python sovereign.py --test  # Smoke test
"""

import os
import sys
import json
import time
import copy
import random
import logging
import multiprocessing as mp
from typing import List, Dict, Tuple, Optional
from datetime import datetime

# Add project root
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from engine.game import Game
from engine.player import Player
from engine.card import Card
from engine.deck import Deck
from engine.bo3 import Bo3Match
from optimizer.genetic import GeneticOptimizer
from agents.heuristic_agent import HeuristicAgent
from agents.neural_agent import NeuralAgent, SimpleNeuralNet

logger = logging.getLogger("sovereign")

# --- Distributed Worker ---
def rl_worker_task(deck_a_cards: List[dict], deck_b_cards: List[dict], model_weights_path: str, games: int) -> List[Tuple]:
    """Worker task simulating a K8s pod running self-play games."""
    from engine.card_pool import _scryfall_to_card
    
    # Rebuild decks
    da = Deck()
    for c in deck_a_cards:
        card = _scryfall_to_card(c)
        if card:
            da.add_card(card, 1)
    db = Deck()
    for c in deck_b_cards:
        card = _scryfall_to_card(c)
        if card:
            db.add_card(card, 1)
    
    agent = NeuralAgent()
    if os.path.exists(model_weights_path):
        agent.model.load(model_weights_path)
    
    collected_data = []
    
    for _ in range(games):
        p1 = Player("Neural-A", copy.deepcopy(da))
        p2 = Player("Neural-B", copy.deepcopy(db))
        game = Game([p1, p2])
        game.start_game()
        
        agent.training_data.clear()
        
        turns = 0
        while not game.game_over and turns < 100:
            actions = game.get_legal_actions()
            if not actions:
                game.advance_phase()
                continue
            chosen = agent.get_action(game, game.priority_player)
            game.apply_action(chosen)
            if game.current_phase == "Cleanup":
                turns += 1
                
        won = (game.winner == p1)
        agent.update_training_outcomes(won)
        collected_data.extend(agent.training_data)
        
    return collected_data



class EloSystem:
    """Elo rating system for deck/agent pairs."""
    
    def __init__(self, k_factor=32, initial_elo=1200):
        self.k_factor = k_factor
        self.initial_elo = initial_elo
        self.ratings: Dict[str, float] = {}
    
    def get_rating(self, name: str) -> float:
        return self.ratings.get(name, self.initial_elo)
    
    def update(self, winner: str, loser: str):
        """Update Elo ratings after a match."""
        r_w = self.get_rating(winner)
        r_l = self.get_rating(loser)
        
        e_w = 1.0 / (1.0 + 10 ** ((r_l - r_w) / 400))
        e_l = 1.0 - e_w
        
        self.ratings[winner] = r_w + self.k_factor * (1.0 - e_w)
        self.ratings[loser] = r_l + self.k_factor * (0.0 - e_l)
    
    def top_k(self, k: int) -> List[Tuple[str, float]]:
        """Get top K rated entries."""
        sorted_ratings = sorted(self.ratings.items(), key=lambda x: x[1], reverse=True)
        return sorted_ratings[:k]


class NoveltyTracker:
    """Track and score novel strategies that deviate from established meta."""
    
    def __init__(self):
        self.known_archetypes: Dict[str, Dict] = {}  # archetype → {cards, win_rate}
        self.interaction_graphs: Dict[str, set] = {}  # deck_id → set of card-pair interactions
    
    def register_archetype(self, name: str, card_names: List[str], win_rate: float):
        self.known_archetypes[name] = {'cards': set(card_names), 'win_rate': win_rate}
    
    def score_novelty(self, deck_cards: List[str]) -> float:
        """Score how novel a deck is vs known archetypes (0.0 = identical, 1.0 = completely novel)."""
        if not self.known_archetypes:
            return 0.5  # No baseline → moderate novelty
        
        deck_set = set(deck_cards)
        min_overlap = 1.0
        for arch_name, arch_data in self.known_archetypes.items():
            overlap = len(deck_set & arch_data['cards']) / max(len(deck_set | arch_data['cards']), 1)
            min_overlap = min(min_overlap, overlap)
        
        return 1.0 - min_overlap  # Higher = more novel
    
    def track_interaction(self, deck_id: str, card_a: str, card_b: str):
        if deck_id not in self.interaction_graphs:
            self.interaction_graphs[deck_id] = set()
        self.interaction_graphs[deck_id].add((card_a, card_b))


class SovereignEngine:
    """The Sovereign: Meta-Evolution Orchestrator.
    
    Evolves a population of deck+agent pairs through:
    1. Genetic deck construction (crossover, mutation, curve-aware)
    2. Competitive evaluation via Bo3 matches
    3. Elo-weighted fitness with novelty bonuses
    4. Strategy crossover between agent play-styles
    """
    
    def __init__(self, card_pool: List[dict], format_name: str = 'standard',
                 population_size: int = 50, generations: int = 20,
                 use_neural: bool = False, agent_type: str = 'heuristic'):
        self.card_pool = card_pool
        self.format_name = format_name
        self.population_size = population_size
        self.generations = generations
        self.use_neural = use_neural
        self.agent_type = agent_type
        
        self.elo = EloSystem()
        self.novelty = NoveltyTracker()
        
        # Initialize Database Persistence
        try:
            from engine.persistence import SovereignDB
            self.db = SovereignDB()
            self.db.connect()
        except ImportError:
            self.db = None
            
        self.population: List[Dict] = []  # [{deck, name, elo, wins, losses, fitness}]
        self.generation = 0
        self.meta_snapshots: List[Dict] = []
        self.anomalies: List[Dict] = []
        
        # Stats
        self.total_games = 0
        self.start_time = None
    
    def initialize_population(self, color_combos: List[str] = None):
        """Create initial diverse population across color archetypes."""
        if color_combos is None:
            color_combos = ['W', 'U', 'B', 'R', 'G', 'WU', 'WB', 'UB', 'BR', 'RG',
                           'WG', 'UR', 'BG', 'WR', 'UG']
        
        per_color = max(1, self.population_size // len(color_combos))
        
        for colors in color_combos:
            optimizer = GeneticOptimizer(
                card_pool=self.card_pool,
                population_size=per_color,
                generations=1,
                colors=colors
            )
            decks = optimizer.generate_initial_population()
            
            for i, deck in enumerate(decks):
                name = f"{colors}-G0-{i}"
                self.population.append({
                    'deck': deck,
                    'name': name,
                    'colors': colors,
                    'elo': self.elo.initial_elo,
                    'wins': 0,
                    'losses': 0,
                    'fitness': 0.0,
                    'novelty': 0.0,
                    'efficiency': 0.0,
                })
        
        logger.info(f"Initialized population: {len(self.population)} decks across {len(color_combos)} color combos")
    
    def evaluate_generation(self, matches_per_deck: int = 3):
        """Run tournament-style evaluation: each deck plays N matches against random opponents."""
        n = len(self.population)
        if n < 2:
            return
        
        for entry in self.population:
            for _ in range(matches_per_deck):
                # Pick a random opponent (not self)
                opponent = random.choice([e for e in self.population if e != entry])
                
                result = self._play_match(entry, opponent)
                self.total_games += 1
                
                if result['winner'] == 'Player 1':
                    entry['wins'] += 1
                    opponent['losses'] += 1
                    self.elo.update(entry['name'], opponent['name'])
                elif result['winner'] == 'Player 2':
                    opponent['wins'] += 1
                    entry['losses'] += 1
                    self.elo.update(opponent['name'], entry['name'])
        
        # Update fitness scores
        for entry in self.population:
            total_games = entry['wins'] + entry['losses']
            win_rate = entry['wins'] / max(total_games, 1)
            
            # Elo-weighted fitness
            elo_score = (self.elo.get_rating(entry['name']) - 1000) / 400  # Normalized
            
            # Novelty score
            deck_cards = [card.name for card in entry['deck'].get_game_deck() if not card.is_land]
            novelty = self.novelty.score_novelty(deck_cards)
            entry['novelty'] = novelty
            
            # Combined fitness: 60% win rate, 25% Elo, 15% novelty
            entry['fitness'] = 0.60 * win_rate + 0.25 * elo_score + 0.15 * novelty
            entry['elo'] = self.elo.get_rating(entry['name'])
    
    def _play_match(self, entry_a: dict, entry_b: dict) -> dict:
        """Play a single match between two deck entries."""
        p1 = Player("Player 1", copy.deepcopy(entry_a['deck']))
        p2 = Player("Player 2", copy.deepcopy(entry_b['deck']))
        
        game = Game([p1, p2])
        
        # Select agent based on engine configuration
        if self.agent_type == 'mcts':
            from agents.mcts_agent import MCTSAgent
            agent = MCTSAgent(max_iterations=200, max_rollout_depth=8)
        elif self.agent_type == 'strategic':
            from agents.strategic_agent import StrategicAgent
            agent = StrategicAgent()
        else:
            agent = HeuristicAgent()
        
        game.start_game()
        turns = 0
        max_turns = 80
        action_count = 0
        max_actions = 500  # Safety valve: cap total actions per game
        
        while not game.game_over and turns < max_turns and action_count < max_actions:
            actions = game.get_legal_actions()
            if not actions:
                game.advance_phase()
                action_count += 1
                continue
            chosen = agent.get_action(game, game.priority_player)
            game.apply_action(chosen)
            action_count += 1
            if game.current_phase == "Cleanup":
                turns += 1
        
        winner = None
        if game.winner:
            if game.winner == p1:
                winner = 'Player 1'
            else:
                winner = 'Player 2'
        
        return {'winner': winner, 'turns': turns}
    
    def evolve(self):
        """Run one generation of evolution: select → crossover → mutate."""
        # Sort by fitness
        self.population.sort(key=lambda e: e['fitness'], reverse=True)
        
        # Elite preservation: top 20%
        elite_count = max(2, len(self.population) // 5)
        elites = self.population[:elite_count]
        
        # Record meta snapshot
        self.meta_snapshots.append({
            'generation': self.generation,
            'timestamp': datetime.now().isoformat(),
            'top_5': [(e['name'], e['fitness'], e['elo'], e['colors']) 
                     for e in self.population[:5]],
            'avg_fitness': sum(e['fitness'] for e in self.population) / len(self.population),
            'total_games': self.total_games,
        })
        
        # Create new generation
        new_population = []
        
        # Keep elites
        for elite in elites:
            new_entry = copy.deepcopy(elite)
            new_entry['wins'] = 0
            new_entry['losses'] = 0
            new_population.append(new_entry)
        
        # Fill rest with crossover + mutation
        while len(new_population) < self.population_size:
            # Tournament selection (pick 3, take best)
            tournament = random.sample(self.population, min(3, len(self.population)))
            parent_a = max(tournament, key=lambda e: e['fitness'])
            parent_b = random.choice([e for e in self.population if e != parent_a])
            
            # Crossover
            child_deck = self._crossover_decks(parent_a['deck'], parent_b['deck'])
            
            # Mutation
            child_deck = self._mutate_deck(child_deck, parent_a['colors'])
            
            self.generation += 1
            child_name = f"{parent_a['colors']}-G{self.generation}-{len(new_population)}"
            
            new_population.append({
                'deck': child_deck,
                'name': child_name,
                'colors': parent_a['colors'],
                'elo': self.elo.initial_elo,
                'wins': 0,
                'losses': 0,
                'fitness': 0.0,
                'novelty': 0.0,
                'efficiency': 0.0,
            })
        
        self.population = new_population
    
    def _crossover_decks(self, deck_a: Deck, deck_b: Deck) -> Deck:
        """Crossover two decks: take spells from each parent randomly."""
        cards_a = [c for c in deck_a.get_game_deck() if not c.is_land]
        cards_b = [c for c in deck_b.get_game_deck() if not c.is_land]
        lands = [c for c in deck_a.get_game_deck() if c.is_land]
        
        # 50/50 split from each parent
        combined = []
        all_spells = cards_a + cards_b
        random.shuffle(all_spells)
        
        # Take up to 36 spells (60 - 24 lands)
        seen_names = {}
        for card in all_spells:
            count = seen_names.get(card.name, 0)
            if count < 4 and len(combined) < 36:
                combined.append(copy.deepcopy(card))
                seen_names[card.name] = count + 1
        
        # Fill remainder
        while len(combined) < 36:
            filler = random.choice(all_spells)
            combined.append(copy.deepcopy(filler))
        
        child_cards = combined + [copy.deepcopy(l) for l in lands[:24]]
        child_deck = Deck()
        for c in child_cards:
            child_deck.add_card(c, 1)
        return child_deck
    
    def _mutate_deck(self, deck: Deck, colors: str) -> Deck:
        """Mutate a deck: swap 2-4 cards with random cards from pool."""
        cards = deck.get_game_deck()
        non_lands = [c for c in cards if not c.is_land]
        lands = [c for c in cards if c.is_land]
        
        # Filter pool to matching colors
        from optimizer.genetic import card_quality_score
        pool = [c for c in self.card_pool 
                if any(color in (c.get('color_identity', []) or ['C']) for color in colors)
                or not c.get('color_identity')]
        
        if not pool:
            return deck
        
        mutations = random.randint(1, 4)
        for _ in range(mutations):
            if non_lands and pool:
                # Remove a random card
                idx = random.randint(0, len(non_lands) - 1)
                non_lands.pop(idx)
                
                # Add a random card from pool
                new_card_data = random.choice(pool)
                from engine.card_pool import _scryfall_to_card
                try:
                    new_card = _scryfall_to_card(new_card_data)
                    if new_card:
                        non_lands.append(new_card)
                except Exception:
                    pass
        
        mutated = Deck()
        for c in non_lands + lands:
            mutated.add_card(c, 1)
        return mutated
    
    def distributed_rl_train(self, num_workers: int = 4, games_per_worker: int = 10, epochs: int = 5):
        """K8s-ready Distributed Reinforcement Learning training loop.
        
        Spawns multiprocess workers (simulating distributed pods) to play self-play
        games concurrently using the current NeuralAgent weights, aggregates the
        collected (state, action, value) data, and trains the central model.
        """
        if not self.population:
            self.initialize_population()
        
        print(f"\n🧠 Starting Distributed RL Training ({num_workers} parallel workers, {games_per_worker} games/worker)...")
        
        model_path = os.path.join(BASE_DIR, 'data', 'best_neural_model.npz')
        main_agent = NeuralAgent()
        if os.path.exists(model_path):
            main_agent.model.load(model_path)
        
        os.makedirs(os.path.join(BASE_DIR, 'data'), exist_ok=True)
        
        for epoch in range(epochs):
            print(f"  Epoch {epoch+1}/{epochs} | Spawning workers...")
            main_agent.model.save(model_path)
            
            # Select random decks for self play
            deck_params = []
            for _ in range(num_workers):
                a = random.choice(self.population)
                b = random.choice(self.population)
                # Serialize decks to native dicts for IPC
                da_serial = [c.to_dict() if hasattr(c, 'to_dict') else {'name': c.name} for c in a['deck'].get_game_deck()]
                db_serial = [c.to_dict() if hasattr(c, 'to_dict') else {'name': c.name} for c in b['deck'].get_game_deck()]
                deck_params.append((da_serial, db_serial, model_path, games_per_worker))
            
            with mp.Pool(num_workers) as pool:
                results = pool.starmap(rl_worker_task, deck_params)
            
            # Flatten results
            all_data = []
            for batch in results:
                all_data.extend(batch)
            
            print(f"  Epoch {epoch+1}/{epochs} | Collected {len(all_data)} state transitions. Training...")
            main_agent.training_data = all_data
            main_agent.train_batch(lr=0.005)
            
            # Save updated weights
            main_agent.model.save(model_path)
        
        print(f"✅ RL Training complete. Model saved to {model_path}.")
    
    def run(self):
        """Run the full evolutionary process."""
        self.start_time = time.time()
        
        print(f"🏛️ The Sovereign — Meta-Evolution Engine")
        print(f"   Format: {self.format_name}")
        print(f"   Population: {self.population_size}")
        print(f"   Generations: {self.generations}")
        print(f"{'='*60}")
        
        self.initialize_population()
        
        for gen in range(self.generations):
            print(f"\n── Generation {gen+1}/{self.generations} ──")
            
            # Evaluate
            self.evaluate_generation()
            
            # Report
            top = self.population[:3]
            for i, entry in enumerate(top):
                print(f"  #{i+1} {entry['name']}: fitness={entry['fitness']:.3f} "
                      f"elo={entry['elo']:.0f} W/L={entry['wins']}/{entry['losses']} "
                      f"novelty={entry['novelty']:.2f}")
            
            # Evolve (skip last generation)
            if gen < self.generations - 1:
                self.evolve()
        
        elapsed = time.time() - self.start_time
        print(f"\n{'='*60}")
        print(f"✅ Sovereign complete: {self.total_games} games in {elapsed:.1f}s")
        print(f"   ({self.total_games / max(elapsed, 1):.0f} games/sec)")
        
        # Final top 5
        self.population.sort(key=lambda e: e['fitness'], reverse=True)
        print(f"\n🏆 Final Rankings:")
        for i, entry in enumerate(self.population[:5]):
            print(f"  #{i+1} {entry['name']}: fitness={entry['fitness']:.3f} elo={entry['elo']:.0f}")
        
        return {
            'champion': self.population[0] if self.population else None,
            'meta_snapshots': self.meta_snapshots,
            'total_games': self.total_games,
            'elapsed': elapsed,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="The Sovereign — MTG Meta-Evolution Engine")
    parser.add_argument("--format", default="modern", help="MTG format")
    parser.add_argument("--generations", type=int, default=5, help="Evolution generations")
    parser.add_argument("--population", type=int, default=20, help="Population size")
    parser.add_argument("--test", action="store_true", help="Quick smoke test")
    parser.add_argument("--agent", default="heuristic", choices=["heuristic", "mcts", "strategic"],
                        help="Agent type for match evaluation")
    parser.add_argument("--train-rl", action="store_true", help="Run the distributed Neural RL training loop")
    args = parser.parse_args()
    
    # Load card pool
    data_path = os.path.join(BASE_DIR, 'data', 'processed_cards.json')
    if os.path.exists(data_path):
        with open(data_path, 'r') as f:
            card_pool = json.load(f)
    else:
        print(f"Card pool not found at {data_path}. Run sync_scryfall.py first.")
        sys.exit(1)
    
    if args.test:
        args.generations = 1
        args.population = 4
    
    engine = SovereignEngine(
        card_pool=card_pool,
        format_name=args.format,
        population_size=args.population,
        generations=args.generations,
        agent_type=args.agent,
    )
    
    if getattr(args, 'train_rl', False):
        engine.distributed_rl_train(num_workers=4, games_per_worker=5, epochs=3)
        return
        
    result = engine.run()
    
    # Save results
    results_path = os.path.join(BASE_DIR, 'data', 'sovereign_results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'meta_snapshots': result['meta_snapshots'],
            'total_games': result['total_games'],
            'elapsed': result['elapsed'],
        }, f, indent=2, default=str)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
