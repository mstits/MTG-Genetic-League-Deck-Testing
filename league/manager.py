"""LeagueManager — Evolutionary league engine for MTG deck populations.

Runs the evolutionary cycle each season:
    1. Match  — Pairs decks for Bo3 matches (in-process or Redis-distributed)
    2. Rate   — ELO updates with K-factor decay (K=40→24→12 by experience)
    3. Cull   — Retires bottom 5% of active decks (never culls Boss decks)
    4. Breed  — Top champions crossbreed with mutation (3% offspring rate)
    5. Wild   — Random new decks injected for genetic diversity (~1%)

The league maintains a target population of ~1000 active decks across
all 32 color combinations (including colorless, 3/4/5-color), with Boss
decks from the Gauntlet serving as fixed benchmarks. Promotion to Mythic
requires beating a Boss in a best-of-three.
"""

import json
import random
import os
import logging
from datetime import datetime
from typing import Optional
from data.db import get_db_connection, save_deck, update_card_stats
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card_builder import dict_to_card, inject_basic_lands
from simulation.runner import SimulationRunner, reset_error_budget, get_error_budget_status
from agents.strategic_agent import StrategicAgent
from optimizer.genetic import GeneticOptimizer, parse_cmc
from league.gauntlet import Gauntlet
from engine.errors import GameStateError

# All 32 color identities for deck construction
COLOR_COMBOS = [
    # Colorless
    "C",
    # Mono
    "W", "U", "B", "R", "G",
    # Two-color
    "WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG",
    # Three-color
    "WUB", "WUR", "WUG", "WBR", "WBG", "WRG", "UBR", "UBG", "URG", "BRG",
    # Four-color
    "WUBR", "WUBG", "WURG", "WBRG", "UBRG",
    # Five-color
    "WUBRG",
]

logger = logging.getLogger(__name__)



class LeagueManager:
    """Manages the MTG competitive league, tracking ELO, divisions, matches, and deck evolution."""
    def __init__(self) -> None:
        self.divisions = ["Provisional", "Bronze", "Silver", "Gold", "Mythic"]
        self.season_number = 1
        self.gauntlet = Gauntlet()
        self.gauntlet.deploy_bosses()
        self._load_card_pool()
        
    def _load_card_pool(self) -> None:
        """Load the card pool once."""
        import os
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_path = os.path.join(os.path.dirname(base_dir), 'data', 'legal_cards.json')
        if not os.path.exists(data_path):
            data_path = os.path.join(os.path.dirname(base_dir), 'data', 'processed_cards.json')
        if not os.path.exists(data_path):
            data_path = 'data/legal_cards.json'
        if not os.path.exists(data_path):
            data_path = 'data/processed_cards.json'
        with open(data_path, 'r') as f:
            self.card_pool_list = json.load(f)
        self.card_pool = {c['name']: c for c in self.card_pool_list}
        inject_basic_lands(self.card_pool)
        
    def _get_decks_in_division(self, division: str, limit: int = 500) -> list[dict]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, card_list, wins, losses, elo 
                FROM decks 
                WHERE division = %s AND active = TRUE
                ORDER BY elo DESC
                LIMIT %s
            ''', (division, limit))
            return [dict(row) for row in cursor.fetchall()]

    def _db_to_deck_obj(self, row) -> Deck:
        deck = Deck()
        card_list = json.loads(row['card_list'])
        
        if isinstance(card_list, list): 
            counts = {}
            for name in card_list: counts[name] = counts.get(name, 0) + 1
            card_list = counts
              
        for name, count in card_list.items():
            card_data = self.card_pool.get(name)
            if card_data:
                card_obj = self._make_card(card_data)
                deck.add_card(card_obj, count)
        
        deck.db_id = row['id']
        deck.name = row['name']
        deck.elo = row.get('elo', 1200)
        return deck

    def _make_card(self, data: dict) -> 'Card':
        return dict_to_card(data)

    def run_season(self, games_per_deck: int = 4) -> None:
        """Run a full season of matches across all divisions using parallel workers."""
        logger.info("--- Season %d ---", self.season_number)
        
        # Reset error budget for this season
        reset_error_budget()
        
        # 1. Run Matches — parallel within each division
        total_matches = 0
        total_decisive = 0
        
        for div in self.divisions:
            decks_data = self._get_decks_in_division(div)
            if len(decks_data) < 2:
                continue
            
            # Parse card lists for parallel execution
            deck_cards = {}
            for d in decks_data:
                cl = json.loads(d['card_list'])
                if isinstance(cl, list):
                    counts = {}
                    for n in cl: counts[n] = counts.get(n, 0) + 1
                    cl = counts
                deck_cards[d['id']] = cl
            
            num_matches = games_per_deck * len(decks_data) // 2
            
            # Build match pairs using Swiss-style ELO-proximity pairing
            match_args = []
            
            from engine.format_validator import FormatValidator, LegalityError
            validator = FormatValidator(self.card_pool_list, "legacy")
            
            # Sort decks by ELO with small random jitter for variety
            sorted_decks = sorted(decks_data, key=lambda d: d['elo'] + random.uniform(-20, 20), reverse=True)
            
            attempts = 0
            pair_idx = 0
            while len(match_args) < num_matches and attempts < num_matches * 10:
                attempts += 1
                # Swiss-style: pair adjacent ELO-ranked decks
                if pair_idx + 1 < len(sorted_decks):
                    d1 = sorted_decks[pair_idx]
                    d2 = sorted_decks[pair_idx + 1]
                    pair_idx = (pair_idx + 2) % max(len(sorted_decks) - 1, 1)
                    # Re-shuffle with jitter periodically to avoid repeated pairings
                    if pair_idx == 0:
                        sorted_decks = sorted(decks_data, key=lambda d: d['elo'] + random.uniform(-20, 20), reverse=True)
                else:
                    d1, d2 = random.sample(decks_data, 2)
                
                try:
                    validator.validate_matchup(deck_cards[d1['id']], deck_cards[d2['id']])
                except LegalityError:
                    continue
                    
                match_args.append((
                    d1['id'], d2['id'],
                    deck_cards[d1['id']], deck_cards[d2['id']],
                    self.card_pool
                ))
            
            # Dispatch matches — try Redis, fall back to in-process
            if len(match_args) > 0:
                try:
                    import redis
                    from rq import Queue
                    from simulation.parallel import run_match_task
                    import time as _time

                    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
                    r = redis.from_url(redis_url)
                    r.ping()  # Test connection
                    q = Queue('mtg-sims', connection=r)

                    jobs = []
                    for args in match_args:
                        d1_id, d2_id, d1_cards, d2_cards, _ = args
                        job = q.enqueue(run_match_task, d1_id, d2_id, d1_cards, d2_cards, self.season_number, job_timeout='5m')
                        jobs.append(job)

                    logger.info("  ⚡ Enqueued %d matches to Redis.", len(jobs))

                    completed = 0
                    while completed < len(jobs):
                        completed = sum(1 for j in jobs if j.is_finished or j.is_failed)
                        _time.sleep(1)
                    
                    for job in jobs:
                        if job.is_finished and job.result:
                            total_matches += 1
                            d1_id, d2_id, winner_id = job.result
                            if winner_id is not None:
                                total_decisive += 1

                except Exception as e:
                    # Redis unavailable — run matches in-process
                    logger.info("  Redis unavailable (%s), running in-process.", type(e).__name__)
                    for args in match_args:
                        d1_id, d2_id, d1_cards, d2_cards, _ = args
                        try:
                            # Build deck objects from card dicts
                            deck1 = Deck()
                            for name, count in d1_cards.items():
                                card_data = self.card_pool.get(name)
                                if card_data:
                                    deck1.add_card(self._make_card(card_data), count)
                            deck1.db_id = d1_id
                            
                            deck2 = Deck()
                            for name, count in d2_cards.items():
                                card_data = self.card_pool.get(name)
                                if card_data:
                                    deck2.add_card(self._make_card(card_data), count)
                            deck2.db_id = d2_id
                            
                            winner_id = self.run_match(deck1, deck2)
                            total_matches += 1
                            if winner_id is not None:
                                total_decisive += 1
                        except GameStateError as e:
                            logger.info("Match game state error D%s vs D%s: %s", d1_id, d2_id, e)
                        except Exception as e:
                            logger.warning("Match error D%s vs D%s: %s", d1_id, d2_id, e)
                    
                    if total_matches > 0:
                        logger.info("  🔧 Ran %d matches in-process (Redis unavailable).", total_matches)

        # 2. Promotions
        self.resolve_promotions()

        # 3. Cull the weak — proportional to population
        culled = self.cull_weakest()
        
        # 4. Breed from champions — proportional to population
        bred = self.breed_from_champions()
        
        # Season summary
        wr = f"{total_decisive/total_matches*100:.0f}%" if total_matches > 0 else "N/A"
        
        # Log error budget status for this season
        error_status = get_error_budget_status()
        if error_status['total_errors'] > 0:
            logger.info("  🐛 Error budget: %d/%d errors. Types: %s",
                        error_status['total_errors'], error_status['threshold'],
                        error_status['error_types'])
        
        logger.info("  ⚔️  %d matches | %s decisive | -%d culled | +%d bred", total_matches, wr, culled, bred)
        
        self.season_number += 1

    def _db_to_deck_obj_by_id(self, deck_id: int, decks_data: list[dict]) -> Optional[Deck]:
        """Find a deck in decks_data by id and convert to Deck object."""
        for d in decks_data:
            if d['id'] == deck_id:
                return self._db_to_deck_obj(d)
        return None

    def _generate_virtual_sideboard(self, deck: Deck) -> list:
        """Generate a 15-card heuristic sideboard based on deck colors."""
        from engine.card_builder import dict_to_card
        
        # Get deck colors heuristically by querying the DB or computing
        colors = ""
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT colors FROM decks WHERE id = %s', (deck.db_id,))
            row = c.fetchone()
            if row: colors = row['colors']
            
        if not colors:
            return []
            
        valid_pool = []
        for card_data in self.card_pool_list:
            if 'Land' in card_data.get('type_line', ''): continue
            
            card_colors = card_data.get('color_identity', [])
            if any(cc not in colors for cc in card_colors):
                continue
                
            valid_pool.append(card_data)
            
        # Prioritize hate cards / removal / control magic
        valid_pool.sort(key=lambda x: (
            'Destroy' in x.get('oracle_text', ''),
            'Counter' in x.get('oracle_text', ''),
            'Exile' in x.get('oracle_text', ''),
            x.get('cmc', 0)
        ), reverse=True)
            
        virtual_sb = []
        for c in valid_pool[:15]:
            virtual_sb.append(dict_to_card(c))
        return virtual_sb

    def run_match(self, deck1: Deck, deck2: Deck) -> Optional[int]:
        """Run a Best-of-3 match with sideboard swapping, return winner_id or None for draw."""
        try:
            # Check for upset condition *before* the match so we know to capture snapshots
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute('SELECT elo FROM decks WHERE id = %s', (deck1.db_id,))
                    elo1 = cursor.fetchone()['elo']
                    cursor.execute('SELECT elo FROM decks WHERE id = %s', (deck2.db_id,))
                    elo2 = cursor.fetchone()['elo']
                
            from engine.misplay_hunter import MisplayHunter
            hunter = MisplayHunter()
            
            # If d2 is much higher ELO, and d1 might win, track d2's decisions. (And vice versa)
            track_snapshots = False
            track_high_elo_idx = -1
            if elo2 - elo1 > 50:
                track_snapshots = True
                track_high_elo_idx = 1
            elif elo1 - elo2 > 50:
                track_snapshots = True
                track_high_elo_idx = 0
                
            d1_wins = 0
            d2_wins = 0
            d1_play_wins = 0
            d1_draw_wins = 0
            d2_play_wins = 0
            d2_draw_wins = 0
            d1_mulligans = 0
            d2_mulligans = 0
            game1_winner_id = None
            all_logs = []
            total_turns = 0
            latest_snapshots = []
            
            all_logs.append(f"{'='*60}")
            all_logs.append(f"MATCH: Deck {deck1.db_id} vs Deck {deck2.db_id}")
            all_logs.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            all_logs.append(f"{'='*60}")
            
            from engine.bo3 import Bo3Match
            bo3_mgr = Bo3Match(deck1, deck2)
            sb1 = self._generate_virtual_sideboard(deck1)
            sb2 = self._generate_virtual_sideboard(deck2)
            match_sideboard_plans = []
            p1_cards_seen = set()  # Cards seen played by Player 2
            p2_cards_seen = set()  # Cards seen played by Player 1
            
            for game_num in range(3):
                player1 = Player(f"Deck {deck1.db_id}", deck1)
                player2 = Player(f"Deck {deck2.db_id}", deck2)
                
                if game_num > 0:
                    prev_game = {'winner': result.winner, 'p1_life': getattr(player1, 'life', 0), 'p2_life': getattr(player2, 'life', 0), 'game_log': result.game_log}
                    # Extract cards seen from previous game log for targeted sideboard decisions
                    import re
                    for line in result.game_log:
                        # Match "Player casts/plays Card Name" patterns
                        m = re.search(r'Deck (\d+) (?:casts|plays|attacks with) (.+?)(?:\s*\(|$)', str(line))
                        if m:
                            deck_id_str, card_name = m.group(1), m.group(2).strip()
                            if deck_id_str == str(deck2.db_id):
                                p1_cards_seen.add(card_name)  # P1 saw P2 play this
                            elif deck_id_str == str(deck1.db_id):
                                p2_cards_seen.add(card_name)  # P2 saw P1 play this
                    
                    swaps1 = bo3_mgr._apply_sideboard(player1, sb1, game_num, prev_game, cards_seen=p1_cards_seen)
                    swaps2 = bo3_mgr._apply_sideboard(player2, sb2, game_num, prev_game, cards_seen=p2_cards_seen)
                    for swap in swaps1:
                        match_sideboard_plans.append({"deck_id": deck1.db_id, "opp": swap['opp_archetype'], "in": swap['card_in'], "out": swap['card_out']})
                    for swap in swaps2:
                        match_sideboard_plans.append({"deck_id": deck2.db_id, "opp": swap['opp_archetype'], "in": swap['card_in'], "out": swap['card_out']})
                
                game = Game([player1, player2])
                
                # Loser of previous game chooses who plays first (simulate as random if first game, otherwise loser plays first)
                if game_num == 0:
                    game.active_player_index = random.choice([0, 1])
                else:
                    # This logic assumes the last winner is recorded in the previous game's log entry.
                    # A more robust way might be to store the last winner_id directly.
                    # For now, we'll parse the last game's log summary.
                    last_game_summary = all_logs[-1] if all_logs else ""
                    if f"winner: Deck {deck1.db_id}" in last_game_summary:
                        game.active_player_index = 1  # deck 2 plays first
                    elif f"winner: Deck {deck2.db_id}" in last_game_summary:
                        game.active_player_index = 0  # deck 1 plays first
                    else: # Draw or unexpected, default to random
                        game.active_player_index = random.choice([0, 1])
                        
                game.starting_player_index = game.active_player_index
                
                runner = SimulationRunner(game, [StrategicAgent(look_ahead_depth=0), StrategicAgent(look_ahead_depth=0)], capture_snapshots=track_snapshots)
                result = runner.run()
                total_turns += result.turns
                
                if game_num == 0:
                    d1_mulligans = getattr(result, 'mulligan_counts', {}).get(player1.name, 0)
                    d2_mulligans = getattr(result, 'mulligan_counts', {}).get(player2.name, 0)
                
                # Keep snapshots from the decisive game that the high-elo player lost
                if track_snapshots:
                    if (track_high_elo_idx == 0 and result.winner == player2.name) or \
                       (track_high_elo_idx == 1 and result.winner == player1.name):
                        latest_snapshots = result.snapshots
                
                if result.winner == player1.name:
                    d1_wins += 1
                    if game.starting_player_index == 0:
                        d1_play_wins += 1
                    else:
                        d1_draw_wins += 1
                    if game_num == 0:
                        game1_winner_id = deck1.db_id
                elif result.winner == player2.name:
                    d2_wins += 1
                    if game.starting_player_index == 1:
                        d2_play_wins += 1
                    else:
                        d2_draw_wins += 1
                    if game_num == 0:
                        game1_winner_id = deck2.db_id
                
                # Capture full strategic log
                all_logs.append(f"\n--- Game {game_num+1} ({result.turns} turns, winner: {result.winner or 'Draw'}) ---")
                all_logs.extend(result.game_log)
                
                # Check if match is decided
                if d1_wins >= 2 or d2_wins >= 2:
                    break
            
            # Determine match winner
            winner_id = None
            if d1_wins > d2_wins:
                winner_id = deck1.db_id
            elif d2_wins > d1_wins:
                winner_id = deck2.db_id
            
            all_logs.append(f"\nMATCH RESULT: {d1_wins}-{d2_wins} → Winner: {'Deck ' + str(winner_id) if winner_id else 'Draw'}")
            
            # Save log to file
            log_path = self._save_match_log(all_logs)
            
            self.update_match_result(deck1.db_id, deck2.db_id, winner_id, total_turns // max(game_num+1, 1),
                                     all_logs, log_path, game1_winner_id,
                                     d1_play_wins, d1_draw_wins, d2_play_wins, d2_draw_wins, match_sideboard_plans,
                                     d1_mulligans, d2_mulligans)
            
            # Card-level tracking
            d1_cards = set(c.name for c in deck1.maindeck)
            d2_cards = set(c.name for c in deck2.maindeck)
            
            if winner_id == deck1.db_id:
                update_card_stats(d1_cards, won=True)
                update_card_stats(d2_cards, won=False)
            elif winner_id == deck2.db_id:
                update_card_stats(d2_cards, won=True)
                update_card_stats(d1_cards, won=False)
            
            # --- Misplay Hunter ---
            winning_idx = 0 if winner_id == deck1.db_id else (1 if winner_id == deck2.db_id else -1)
            
            if winning_idx != -1 and hunter.detect_upset(elo1, elo2, winning_idx):
                hunter.analyze_upset(latest_snapshots, track_high_elo_idx, deck1.db_id, deck2.db_id)
                
            return winner_id
                
        except GameStateError as e:
            logger.info("Match game state error D%s vs D%s: %s", deck1.db_id, deck2.db_id, e)
            return None
        except Exception as e:
            logger.exception("Match error D%s vs D%s", deck1.db_id, deck2.db_id)
            return None
    
    def _save_match_log(self, log_lines: list[str]) -> str:
        """Write detailed match log to file."""
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'matches')
        os.makedirs(log_dir, exist_ok=True)
        
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"s{self.season_number}_{ts}.log"
        filepath = os.path.join(log_dir, filename)
        
        with open(filepath, 'w') as f:
            f.write('\n'.join(log_lines))
        
        return filepath

    def update_match_result(self, id1: int, id2: int, winner_id: Optional[int], turns: int,
                            game_log: Optional[list] = None, log_path: Optional[str] = None,
                            game1_winner_id: Optional[int] = None,
                            d1_play_wins: int = 0, d1_draw_wins: int = 0,
                            d2_play_wins: int = 0, d2_draw_wins: int = 0,
                            sideboard_plans: Optional[list[dict]] = None,
                            p1_mulligans: int = 0, p2_mulligans: int = 0) -> None:
        """Update database with match outcome, process ELO changes, and track deck/card statistics."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            log_json = json.dumps(game_log[-100:] if game_log else [])  # Keep last 100 events in DB
            
            cursor.execute('''
                INSERT INTO matches (season_id, deck1_id, deck2_id, winner_id, game1_winner_id, turns, game_log, log_path, p1_mulligans, p2_mulligans)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (self.season_number, id1, id2, winner_id, game1_winner_id, turns, log_json, log_path, p1_mulligans, p2_mulligans))
            
            if sideboard_plans:
                for swap in sideboard_plans:
                    cursor.execute('''
                        INSERT INTO sideboard_plans (deck_id, opp_archetype, card_in, card_out, count)
                        VALUES (%s, %s, %s, %s, 1)
                        ON CONFLICT(deck_id, opp_archetype, card_in, card_out) DO UPDATE SET
                            count = sideboard_plans.count + 1
                    ''', (swap['deck_id'], swap['opp'], swap['in'], swap['out']))
            
            # K-factor decay: new decks move faster, veterans are stable
            cursor.execute('SELECT wins + losses + draws as total_games FROM decks WHERE id = %s', (id1,))
            d1_games = cursor.fetchone()['total_games']
            cursor.execute('SELECT wins + losses + draws as total_games FROM decks WHERE id = %s', (id2,))
            d2_games = cursor.fetchone()['total_games']
            
            def _get_k_factor(total_games) -> int:
                """K-factor decay: K=40 (new), K=24 (established), K=12 (veteran)."""
                if total_games < 10:
                    return 40    # New decks — fast rating discovery
                elif total_games < 30:
                    return 24    # Established — moderate adjustments
                else:
                    return 12    # Veterans — stable rankings
            
            k1 = _get_k_factor(d1_games)
            k2 = _get_k_factor(d2_games)
            
            cursor.execute('SELECT elo FROM decks WHERE id = %s', (id1,))
            elo1 = cursor.fetchone()['elo']
            cursor.execute('SELECT elo FROM decks WHERE id = %s', (id2,))
            elo2 = cursor.fetchone()['elo']
            
            # Standard ELO expected score formula (numerically stable)
            e1 = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))
            e2 = 1.0 - e1
            
            if winner_id is None:
                s1, s2 = 0.5, 0.5
            elif winner_id == id1:
                s1, s2 = 1, 0
            else:
                s1, s2 = 0, 1
            
            new_elo1 = elo1 + k1 * (s1 - e1)
            new_elo2 = elo2 + k2 * (s2 - e2)
            
            cursor.execute(
                'UPDATE decks SET elo = %s, wins = wins + %s, losses = losses + %s, draws = draws + %s, play_wins = play_wins + %s, draw_wins = draw_wins + %s WHERE id = %s', 
                (new_elo1, int(s1 == 1), int(s1 == 0 and winner_id is not None), int(winner_id is None), d1_play_wins, d1_draw_wins, id1)
            )
            cursor.execute(
                'UPDATE decks SET elo = %s, wins = wins + %s, losses = losses + %s, draws = draws + %s, play_wins = play_wins + %s, draw_wins = draw_wins + %s WHERE id = %s', 
                (new_elo2, int(s2 == 1), int(s2 == 0 and winner_id is not None), int(winner_id is None), d2_play_wins, d2_draw_wins, id2)
            )
            
            conn.commit()

    def cull_weakest(self) -> int:
        """Retire the worst-performing decks — 5% of active population. Never cull Boss decks."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get total active non-boss decks
                cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active=TRUE AND name NOT LIKE 'BOSS:%%'")
                total = cursor.fetchone()['c']
                
                if total <= 50:
                    return 0  # Don't cull if population is small
                
                # Cull 5% of population (min 3, max 50)
                max_cull = max(3, min(50, total // 20))
                
                # Find bottom decks by Elo with at least 6 matches played
                cursor.execute('''
                    SELECT id, name, elo, wins, losses 
                    FROM decks 
                    WHERE active=TRUE AND name NOT LIKE 'BOSS:%%' AND (wins + losses) >= 6
                    ORDER BY elo ASC
                    LIMIT %s
                ''', (max_cull,))
                
                victims = [dict(row) for row in cursor.fetchall()]
                
                for v in victims:
                    cursor.execute('UPDATE decks SET active=FALSE WHERE id=%s', (v['id'],))
                
                if victims:
                    logger.info("  ☠️  Retired %d decks (worst Elo: %.0f)", len(victims), victims[0]['elo'])
                
            conn.commit()
            return len(victims)

    def breed_from_champions(self) -> int:
        """Create new decks by evolving from the DNA of top-performing decks.
        Scales to 3% of population for offspring, plus wild card injections."""
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Get population size
                cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active=TRUE AND name NOT LIKE 'BOSS:%%'")
                total_pop = cursor.fetchone()['c']
                
                # Get top 20 champions (or top 5% for large populations)
                champ_limit = max(5, min(20, total_pop // 20))
                cursor.execute('''
                    SELECT id, name, card_list, elo, colors 
                    FROM decks 
                    WHERE active=TRUE AND name NOT LIKE 'BOSS:%%' AND (wins + losses) >= 4
                    ORDER BY elo DESC
                    LIMIT %s
                ''', (champ_limit,))
                champions = [dict(row) for row in cursor.fetchall()]
        
        bred = 0
        
        # Number of offspring: 3% of population (min 5, max 30)
        num_offspring = max(5, min(30, total_pop * 3 // 100))
        # Number of wild cards: ~1% of population (min 3, max 10)
        num_wild = max(3, min(10, total_pop // 100))
        
        if champions:
            for _ in range(num_offspring):
                champ = random.choice(champions)
                champ_cards = json.loads(champ['card_list'])
                if isinstance(champ_cards, list):
                    counts = {}
                    for n in champ_cards: counts[n] = counts.get(n, 0) + 1
                    champ_cards = counts
                
                colors = champ.get('colors', '') or random.choice(COLOR_COMBOS)
                
                try:
                    opt = GeneticOptimizer(
                        self.card_pool_list, 
                        population_size=8, 
                        generations=3, 
                        colors=colors,
                        champion_cards=champ_cards
                    )
                    best_deck = opt.evolve()
                    
                    card_map = {}
                    for c in best_deck.maindeck:
                        card_map[c.name] = card_map.get(c.name, 0) + 1
                    
                    # Compute archetype
                    total_cmc = 0
                    spell_count = 0
                    for name, count in card_map.items():
                        data = self.card_pool.get(name, {})
                        if 'Land' not in data.get('type_line', ''):
                            cmc = parse_cmc(data.get('mana_cost', ''))
                            total_cmc += cmc * count
                            spell_count += count
                    
                    avg_cmc = total_cmc / max(spell_count, 1)
                    if avg_cmc < 2.5:
                        archetype = "Aggro"
                    elif avg_cmc < 3.8:
                        archetype = "Midrange"
                    else:
                        archetype = "Control"
                    
                    deck_name = f"Evo-{colors}-{archetype[0]}-S{self.season_number}-{random.randint(0,9999)}"
                    save_deck(deck_name, card_map, generation=self.season_number, 
                             parent_ids=[champ['id']], colors=colors, archetype=archetype)
                    bred += 1
                except Exception as e:
                    logger.warning("Breeding error from champion %s: %s", champ.get('name', '?'), e)
        
        if bred > 0:
            logger.info("  🧬 Bred %d offspring from top %d champions", bred, len(champions))
        
        # Inject wild cards for genetic diversity across random color combos
        wild_bred = 0
        for _ in range(num_wild):
            colors = random.choice(COLOR_COMBOS)
            try:
                opt = GeneticOptimizer(self.card_pool_list, population_size=8, generations=3, colors=colors)
                best_deck = opt.evolve()
                
                card_map = {}
                for c in best_deck.maindeck:
                    card_map[c.name] = card_map.get(c.name, 0) + 1
                
                deck_name = f"Wild-{colors}-S{self.season_number}-{random.randint(0,9999)}"
                save_deck(deck_name, card_map, generation=self.season_number, colors=colors)
                wild_bred += 1
            except Exception as e:
                logger.warning("Wild card generation error for %s: %s", colors, e)
        
        if wild_bred > 0:
            logger.info("  🎲 %d wild cards injected", wild_bred)
        
        return bred + wild_bred

    def resolve_promotions(self) -> None:
        for i in range(len(self.divisions) - 1):
            lower_div = self.divisions[i]
            upper_div = self.divisions[i+1]
            
            lower_decks = self._get_decks_in_division(lower_div)
            upper_decks = self._get_decks_in_division(upper_div)
            
            if not lower_decks: continue
            
            promote_count = max(1, len(lower_decks) // 5)
            candidates = lower_decks[:promote_count]
            promoted = []
            
            if upper_div == "Mythic":
                logger.info("  🏆 Boss Battles: %d challengers", len(candidates))
                with get_db_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("SELECT * FROM decks WHERE division='Mythic' AND name LIKE 'BOSS:%%'")
                        bosses = [dict(row) for row in cursor.fetchall()]
                
                if not bosses:
                    promoted = candidates
                else:
                    for cand in candidates:
                        boss = random.choice(bosses)
                        cand_deck = self._db_to_deck_obj(cand)
                        boss_deck = self._db_to_deck_obj(boss)
                        
                        # Best of 3
                        wins = 0
                        boss_logs = []
                        boss_logs.append(f"{'='*60}")
                        boss_logs.append(f"BOSS BATTLE: {cand['name']} vs {boss['name']}")
                        boss_logs.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                        boss_logs.append(f"{'='*60}")
                        for gn in range(3):
                            try:
                                p1 = Player("Challenger", cand_deck)
                                p2 = Player("Boss", boss_deck)
                                game = Game([p1, p2])
                                runner = SimulationRunner(game, [StrategicAgent(look_ahead_depth=0), StrategicAgent(look_ahead_depth=0)])
                                result = runner.run()
                                if result.winner == p1.name:
                                    wins += 1
                                boss_logs.append(f"\n--- Game {gn+1} ({result.turns} turns, winner: {result.winner or 'Draw'}) ---")
                                boss_logs.extend(result.game_log)
                                w_id = cand['id'] if result.winner == p1.name else boss['id']
                                log_path = self._save_match_log(boss_logs)
                                self.update_match_result(cand['id'], boss['id'], w_id, result.turns,
                                                         game_log=result.game_log, log_path=log_path)
                            except GameStateError as e:
                                logger.info("Boss battle game state error: %s", e)
                            except Exception as e:
                                logger.warning("Game error during promotion: %s", e)
                        
                        if wins >= 2:
                            logger.info("    ✅ %s beat %s (%d/3)!", cand['name'], boss['name'], wins)
                            promoted.append(cand)
                        else:
                            logger.info("    ❌ %s lost to %s (%d/3)", cand['name'], boss['name'], wins)
            else:
                promoted = candidates
            
            relegated = []
            if upper_decks:
                relegate_count = max(1, len(upper_decks) // 5)
                upper_non_boss = [d for d in upper_decks if "BOSS:" not in d['name']]
                if upper_non_boss:
                    relegated = upper_non_boss[-relegate_count:]
                
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    for d in promoted:
                        cursor.execute('UPDATE decks SET division = %s WHERE id = %s', (upper_div, d['id']))
                        logger.info("  ⬆️  %s → %s", d['name'], upper_div)
                        
                    for d in relegated:
                        cursor.execute('UPDATE decks SET division = %s WHERE id = %s', (lower_div, d['id']))
                        logger.info("  ⬇️  %s → %s", d['name'], lower_div)
                conn.commit()
