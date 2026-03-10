"""
Distributed match simulation using Redis RQ.
Runs Bo3 matches on isolated SimWorkers for thousands-of-decks throughput.
"""
from typing import List, Tuple, Optional, Dict
import os
import sys
import json
from datetime import datetime
from engine.engine_config import config as engine_config
import multiprocessing as mp
import logging

logger = logging.getLogger(__name__)

# Global variable for worker processes to cache the card pool
GLOBAL_CARD_POOL = None

def load_card_pool_global():
    """Load the card pool once per worker process to save memory/time."""
    global GLOBAL_CARD_POOL
    if GLOBAL_CARD_POOL is not None:
        return GLOBAL_CARD_POOL
        
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_path = os.path.join(base_dir, 'data', 'processed_cards.json')
    if not os.path.exists(data_path):
        data_path = os.path.join(base_dir, 'data', 'legal_cards.json')
    with open(data_path, 'r') as f:
        pool_list = json.load(f)
    
    GLOBAL_CARD_POOL = {c['name']: c for c in pool_list}
    
    from engine.card_builder import inject_basic_lands
    inject_basic_lands(GLOBAL_CARD_POOL)
    return GLOBAL_CARD_POOL

def _apply_memory_limit():
    """Apply per-worker memory limit from EngineConfig (Unix only)."""
    limit_mb = engine_config.memory_limit_mb
    if limit_mb <= 0:
        return
    try:
        import resource
        limit_bytes = limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    except (ImportError, ValueError, OSError):
        pass  # Graceful fallback on non-Unix or if limit unsupported

def run_match_task(d1_id, d2_id, d1_cards, d2_cards, season_number):
    """Worker task for Redis queue match execution.
    Pushes results directly to PostgreSQL database.
    """
    try:
        card_pool = load_card_pool_global()
        
        from engine.game import Game
        from engine.player import Player
        from engine.deck import Deck
        from engine.card_builder import dict_to_card, inject_basic_lands
        from simulation.runner import SimulationRunner
        from agents.heuristic_agent import HeuristicAgent
        from data.db import update_card_stats, get_db_connection
        from agents.sideboard_agent import SideboardAgent
        import copy

        def build_deck(card_list, cp):
            """Construct a Deck object from a card dictionary using the provided card pool."""
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
        
        current_deck1 = deck1
        current_deck2 = deck2
        
        swaps1 = []
        swaps2 = []
        for game_num in range(3):
            if game_num == 1:
                current_deck1 = copy.deepcopy(deck1)
                current_deck2 = copy.deepcopy(deck2)
                swaps1 = SideboardAgent(current_deck1).sideboard_against(deck2)
                swaps2 = SideboardAgent(current_deck2).sideboard_against(deck1)
                
            p1 = Player(f"D{d1_id}", current_deck1)
            p2 = Player(f"D{d2_id}", current_deck2)
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
        filename = f"s{season_number}_{ts}.log"
        filepath = os.path.join(log_dir, filename)
        
        with open(filepath, 'w') as f:
            f.write('\n'.join(all_logs))
            
        # Push Result Directly to DB
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get specific deck names for sideboard tracking
                cursor.execute('SELECT name FROM decks WHERE id = %s', (d1_id,))
                d1_name = cursor.fetchone()['name']
                cursor.execute('SELECT name FROM decks WHERE id = %s', (d2_id,))
                d2_name = cursor.fetchone()['name']
                
                # Save sideboard plans tracking specific opponent names
                for swap in (swaps1 or []):
                    cursor.execute('''
                        INSERT INTO sideboard_plans (deck_id, opp_archetype, card_in, card_out, count)
                        VALUES (%s, %s, %s, %s, 1)
                        ON CONFLICT(deck_id, opp_archetype, card_in, card_out) DO UPDATE SET
                            count = sideboard_plans.count + 1
                    ''', (d1_id, d2_name, swap['card_in'], swap['card_out']))
                    
                for swap in (swaps2 or []):
                    cursor.execute('''
                        INSERT INTO sideboard_plans (deck_id, opp_archetype, card_in, card_out, count)
                        VALUES (%s, %s, %s, %s, 1)
                        ON CONFLICT(deck_id, opp_archetype, card_in, card_out) DO UPDATE SET
                            count = sideboard_plans.count + 1
                    ''', (d2_id, d1_name, swap['card_in'], swap['card_out']))

                log_json = json.dumps(all_logs[-100:])
                cursor.execute('''
                    INSERT INTO matches (season_id, deck1_id, deck2_id, winner_id, turns, game_log, log_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                ''', (season_number, d1_id, d2_id, winner_id, total_turns // max(games_played, 1), log_json, filepath))
                
                k = 32
                cursor.execute('SELECT elo FROM decks WHERE id = %s', (d1_id,))
                elo1 = cursor.fetchone()['elo']
                cursor.execute('SELECT elo FROM decks WHERE id = %s', (d2_id,))
                elo2 = cursor.fetchone()['elo']
                
                r1 = 10 ** (elo1 / 400)
                r2 = 10 ** (elo2 / 400)
                e1 = r1 / (r1 + r2)
                e2 = r2 / (r1 + r2)
                
                if winner_id is None:
                    s1, s2 = 0.5, 0.5
                elif winner_id == d1_id:
                    s1, s2 = 1, 0
                else:
                    s1, s2 = 0, 1
                    
                new_elo1 = elo1 + k * (s1 - e1)
                new_elo2 = elo2 + k * (s2 - e2)
                
                cursor.execute('UPDATE decks SET elo = %s, wins = wins + %s, losses = losses + %s, draws = draws + %s WHERE id = %s', 
                               (new_elo1, int(s1 == 1), int(s1 == 0 and winner_id is not None), int(winner_id is None), d1_id))
                cursor.execute('UPDATE decks SET elo = %s, wins = wins + %s, losses = losses + %s, draws = draws + %s WHERE id = %s', 
                               (new_elo2, int(s2 == 1), int(s2 == 0 and winner_id is not None), int(winner_id is None), d2_id))
            conn.commit()
            
        # Update Card Stats
        if winner_id == d1_id:
            update_card_stats(d1_cards.keys(), won=True)
            update_card_stats(d2_cards.keys(), won=False)
        elif winner_id == d2_id:
            update_card_stats(d2_cards.keys(), won=True)
            update_card_stats(d1_cards.keys(), won=False)
            
        return d1_id, d2_id, winner_id
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def run_matches_parallel(match_args: List, num_workers: int = None) -> List[Dict]:
    """Run multiple Bo3 matches in parallel.
    
    Args:
        match_args: List of (d1_id, d2_id, d1_cards, d2_cards, card_pool) tuples
        num_workers: Number of worker processes. Defaults to EngineConfig.max_workers.
    
    Returns:
        List of result dicts with winner_id, turns, etc.
    """
    if num_workers is None:
        num_workers = engine_config.max_workers
        
    # Ensure FastAPI retains at least 1 dedicated core
    max_cores = max(1, (os.cpu_count() or 2) - 1)
    if num_workers > max_cores:
        num_workers = max_cores
    
    if len(match_args) <= 2:
        # Not worth parallelizing for tiny batches
        return [r for r in [_run_single_match(a) for a in match_args] if r is not None]
    
    try:
        with mp.Pool(processes=num_workers, initializer=_apply_memory_limit) as pool:
            results = pool.map(_run_single_match, match_args, chunksize=max(1, len(match_args) // (num_workers * 2)))
        return [r for r in results if r is not None]
    except Exception as e:
        # Fallback to sequential
        logger.warning("Parallel exec failed, falling back to sequential: %s", e)
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
        num_workers: Worker processes (defaults to EngineConfig.max_workers)
    
    Returns:
        List of {name, cards, colors} dicts ready for DB insertion
    """
    if num_workers is None:
        num_workers = engine_config.max_workers
        
    # Ensure FastAPI retains at least 1 dedicated core
    max_cores = max(1, (os.cpu_count() or 2) - 1)
    if num_workers > max_cores:
        num_workers = max_cores
    
    args_list = []
    for colors, count in color_combos_with_counts:
        for i in range(count):
            args_list.append((colors, i, all_cards))
    
    logger.info("Seeding %d decks across %d workers...", len(args_list), num_workers)
    
    try:
        with mp.Pool(processes=num_workers, initializer=_apply_memory_limit) as pool:
            results = pool.map(seed_deck_parallel, args_list, chunksize=4)
        return [r for r in results if r is not None]
    except Exception as e:
        logger.warning("Parallel seed failed, falling back: %s", e)
        return [r for r in [seed_deck_parallel(a) for a in args_list] if r is not None]
