"""
Parallel match simulation using multiprocessing.
Runs Bo3 matches across CPU cores for thousands-of-decks throughput.
"""
import multiprocessing as mp
from typing import List, Tuple, Optional, Dict
import os
from datetime import datetime

def _run_single_match(args) -> Optional[Dict]:
    """Worker function for parallel match execution.
    Returns dict with match results for batch DB write.
    Must be a top-level function for pickling.
    """
    try:
        d1_id, d2_id, d1_cards, d2_cards, card_pool = args
        
        # Import inside worker to avoid pickle issues
        from engine.game import Game
        from engine.player import Player
        from engine.deck import Deck
        from engine.card_builder import dict_to_card, inject_basic_lands
        from simulation.runner import SimulationRunner
        from agents.heuristic_agent import HeuristicAgent

        def build_deck(card_list, cp):
            inject_basic_lands(cp)
            deck = Deck()
            if isinstance(card_list, list):
                counts = {}
                for n in card_list: counts[n] = counts.get(n, 0) + 1
                card_list = counts
            for name, count in card_list.items():
                card_data = cp.get(name)
                if card_data:
                    card_obj = dict_to_card(card_data)
                    deck.add_card(card_obj, count)
            return deck
        
        deck1 = build_deck(d1_cards, card_pool)
        deck2 = build_deck(d2_cards, card_pool)
        
        d1_wins = 0
        d2_wins = 0
        total_turns = 0
        games_played = 0
        all_logs = []
        
        all_logs.append(f"{'='*60}")
        all_logs.append(f"MATCH: D{d1_id} vs D{d2_id}")
        all_logs.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        all_logs.append(f"{'='*60}")
        
        for game_num in range(3):
            p1 = Player(f"D{d1_id}", deck1)
            p2 = Player(f"D{d2_id}", deck2)
            game = Game([p1, p2])
            runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
            result = runner.run()
            total_turns += result.turns
            games_played += 1
            
            if result.winner == p1.name:
                d1_wins += 1
            elif result.winner == p2.name:
                d2_wins += 1
            
            all_logs.append(f"\n--- Game {game_num+1} ({result.turns} turns, winner: {result.winner or 'Draw'}) ---")
            all_logs.extend(result.game_log)
            
            if d1_wins >= 2 or d2_wins >= 2:
                break
        
        winner_id = None
        if d1_wins > d2_wins:
            winner_id = d1_id
        elif d2_wins > d1_wins:
            winner_id = d2_id
        
        all_logs.append(f"\nMATCH RESULT: {d1_wins}-{d2_wins} \u2192 Winner: {'D' + str(winner_id) if winner_id else 'Draw'}")
        
        # Save log file
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'matches')
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        log_path = os.path.join(log_dir, f"parallel_{d1_id}v{d2_id}_{ts}.log")
        with open(log_path, 'w') as f:
            f.write('\n'.join(all_logs))
        
        return {
            'deck1_id': d1_id,
            'deck2_id': d2_id,
            'winner_id': winner_id,
            'turns': total_turns // max(games_played, 1),
            'score': f"{d1_wins}-{d2_wins}",
            'd1_cards': set(d1_cards.keys()) if isinstance(d1_cards, dict) else set(d1_cards),
            'd2_cards': set(d2_cards.keys()) if isinstance(d2_cards, dict) else set(d2_cards),
            'log_path': log_path,
        }
        
    except Exception as e:
        return None


def run_matches_parallel(match_args: List, num_workers: int = None) -> List[Dict]:
    """Run multiple Bo3 matches in parallel.
    
    Args:
        match_args: List of (d1_id, d2_id, d1_cards, d2_cards, card_pool) tuples
        num_workers: Number of worker processes. Defaults to CPU count.
    
    Returns:
        List of result dicts with winner_id, turns, etc.
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8)
    
    if len(match_args) <= 2:
        # Not worth parallelizing for tiny batches
        return [r for r in [_run_single_match(a) for a in match_args] if r is not None]
    
    try:
        with mp.Pool(processes=num_workers) as pool:
            results = pool.map(_run_single_match, match_args, chunksize=max(1, len(match_args) // (num_workers * 2)))
        return [r for r in results if r is not None]
    except Exception as e:
        # Fallback to sequential
        print(f"  ⚠️ Parallel exec failed, falling back to sequential: {e}")
        return [r for r in [_run_single_match(a) for a in match_args] if r is not None]


def seed_deck_parallel(args) -> Optional[Dict]:
    """Worker function for parallel deck seeding."""
    try:
        colors, seed_idx, all_cards = args
        
        from optimizer.genetic import GeneticOptimizer
        
        opt = GeneticOptimizer(all_cards, population_size=8, generations=3, colors=colors)
        best = opt.evolve()
        
        card_map = {}
        for c in best.maindeck:
            card_map[c.name] = card_map.get(c.name, 0) + 1
        
        return {
            'name': f"Gen0-{colors}-{seed_idx}",
            'cards': card_map,
            'colors': colors,
        }
    except Exception as e:
        return None


def seed_decks_parallel(color_combos_with_counts: List[Tuple[str, int]], all_cards, num_workers: int = None) -> List[Dict]:
    """Seed many decks in parallel across color combinations.
    
    Args:
        color_combos_with_counts: List of (colors, count) tuples
        all_cards: Full card pool list
        num_workers: Worker processes
    
    Returns:
        List of {name, cards, colors} dicts ready for DB insertion
    """
    if num_workers is None:
        num_workers = min(mp.cpu_count(), 8)
    
    args_list = []
    for colors, count in color_combos_with_counts:
        for i in range(count):
            args_list.append((colors, i, all_cards))
    
    print(f"  Seeding {len(args_list)} decks across {num_workers} workers...")
    
    try:
        with mp.Pool(processes=num_workers) as pool:
            results = pool.map(seed_deck_parallel, args_list, chunksize=4)
        return [r for r in results if r is not None]
    except Exception as e:
        print(f"  ⚠️ Parallel seed failed, falling back: {e}")
        return [r for r in [seed_deck_parallel(a) for a in args_list] if r is not None]
