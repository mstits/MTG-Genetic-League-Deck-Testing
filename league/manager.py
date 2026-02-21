"""LeagueManager — Genetic evolution engine for MTG deck populations.

Runs the evolutionary cycle:
    1. Match  — Pairs decks for Bo3 matches (parallel via multiprocessing)
    2. Rate   — Updates ELO ratings based on match outcomes
    3. Select — Retires bottom 20% of decks each season
    4. Breed  — Top decks crossbreed: combine card pools of two winners
    5. Mutate — Random card additions/removals/swaps (5% mutation rate)

The league maintains a target population of ~1000 active decks across
all 25 color combinations, with boss decks from the Gauntlet serving
as fixed benchmarks.
"""

import json
import random
import time
import re
import os
from datetime import datetime
from typing import List, Tuple
from data.db import get_db_connection, save_deck, update_card_stats
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card import Card
from engine.card_builder import dict_to_card, inject_basic_lands
from simulation.runner import SimulationRunner
from simulation.parallel import run_matches_parallel
from agents.heuristic_agent import HeuristicAgent
from optimizer.genetic import GeneticOptimizer, parse_cmc
from league.gauntlet import Gauntlet

# Available color combos for deck construction
COLOR_COMBOS = ["R", "B", "G", "W", "U", "RB", "RG", "BG", "WU", "WB", "RW", "UB", "UG", "UR", "WG"]

def classify_archetype(card_list: dict) -> str:
    """Classify a deck as Aggro, Midrange, or Control based on average CMC."""
    total_cmc = 0
    total_spells = 0
    for name, count in card_list.items():
        if name in ('Plains', 'Island', 'Swamp', 'Mountain', 'Forest'):
            continue
        # We don't have cost here, so estimate from name patterns
        total_spells += count
    
    # Fallback classification based on creature ratio
    return "Midrange"  # Will be updated with real CMC data


class LeagueManager:
    def __init__(self):
        self.divisions = ["Provisional", "Bronze", "Silver", "Gold", "Mythic"]
        self.season_number = 1
        self.gauntlet = Gauntlet()
        self.gauntlet.deploy_bosses()
        self._load_card_pool()
        
    def _load_card_pool(self):
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
        
    def _get_decks_in_division(self, division, limit=500):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, card_list, wins, losses, elo 
                FROM decks 
                WHERE division = ? AND active = 1
                ORDER BY elo DESC
                LIMIT ?
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
        deck.elo = row.get('elo', 1200)
        return deck

    def _make_card(self, data):
        return dict_to_card(data)

    def run_season(self, games_per_deck=4):
        print(f"--- Season {self.season_number} ---")
        
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
            
            # Build match pairs
            match_args = []
            for _ in range(num_matches):
                d1, d2 = random.sample(decks_data, 2)
                match_args.append((
                    d1['id'], d2['id'],
                    deck_cards[d1['id']], deck_cards[d2['id']],
                    self.card_pool
                ))
            
            # Run in parallel
            if len(match_args) > 4:
                results = run_matches_parallel(match_args)
            else:
                # Small division — run sequentially via existing method
                for args in match_args:
                    d1 = self._db_to_deck_obj_by_id(args[0], decks_data)
                    d2 = self._db_to_deck_obj_by_id(args[1], decks_data)
                    if d1 and d2:
                        winner_id = self.run_match(d1, d2)
                        total_matches += 1
                        if winner_id:
                            total_decisive += 1
                continue
            
            # Batch write results
            for r in results:
                self.update_match_result(r['deck1_id'], r['deck2_id'], r['winner_id'], r['turns'],
                                         log_path=r.get('log_path'))
                total_matches += 1
                if r['winner_id']:
                    total_decisive += 1
                    # Card stats
                    if r['winner_id'] == r['deck1_id']:
                        update_card_stats(r['d1_cards'], won=True)
                        update_card_stats(r['d2_cards'], won=False)
                    else:
                        update_card_stats(r['d2_cards'], won=True)
                        update_card_stats(r['d1_cards'], won=False)

        # 2. Promotions
        self.resolve_promotions()

        # 3. Cull the weak — proportional to population
        culled = self.cull_weakest()
        
        # 4. Breed from champions — proportional to population
        bred = self.breed_from_champions()
        
        # Season summary
        wr = f"{total_decisive/total_matches*100:.0f}%" if total_matches > 0 else "N/A"
        print(f"  ⚔️  {total_matches} matches | {wr} decisive | -{culled} culled | +{bred} bred")
        
        self.season_number += 1

    def _db_to_deck_obj_by_id(self, deck_id, decks_data):
        """Find a deck in decks_data by id and convert to Deck object."""
        for d in decks_data:
            if d['id'] == deck_id:
                return self._db_to_deck_obj(d)
        return None

    def run_match(self, deck1, deck2) -> int:
        """Run a Best-of-3 match with sideboard swapping, return winner_id or None for draw."""
        try:
            d1_wins = 0
            d2_wins = 0
            all_logs = []
            total_turns = 0
            
            all_logs.append(f"{'='*60}")
            all_logs.append(f"MATCH: Deck {deck1.db_id} vs Deck {deck2.db_id}")
            all_logs.append(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            all_logs.append(f"{'='*60}")
            
            for game_num in range(3):
                player1 = Player(f"Deck {deck1.db_id}", deck1)
                player2 = Player(f"Deck {deck2.db_id}", deck2)
                game = Game([player1, player2])
                
                runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                result = runner.run()
                total_turns += result.turns
                
                if result.winner == player1.name:
                    d1_wins += 1
                elif result.winner == player2.name:
                    d2_wins += 1
                
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
            
            self.update_match_result(deck1.db_id, deck2.db_id, winner_id, total_turns // max(game_num+1, 1), all_logs, log_path)
            
            # Card-level tracking
            d1_cards = set(c.name for c in deck1.maindeck)
            d2_cards = set(c.name for c in deck2.maindeck)
            
            if winner_id == deck1.db_id:
                update_card_stats(d1_cards, won=True)
                update_card_stats(d2_cards, won=False)
            elif winner_id == deck2.db_id:
                update_card_stats(d2_cards, won=True)
                update_card_stats(d1_cards, won=False)
            
            return winner_id
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            return None
    
    def _save_match_log(self, log_lines):
        """Write detailed match log to file."""
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'matches')
        os.makedirs(log_dir, exist_ok=True)
        
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"s{self.season_number}_{ts}.log"
        filepath = os.path.join(log_dir, filename)
        
        with open(filepath, 'w') as f:
            f.write('\n'.join(log_lines))
        
        return filepath

    def update_match_result(self, id1, id2, winner_id, turns, game_log=None, log_path=None):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            log_json = json.dumps(game_log[-100:] if game_log else [])  # Keep last 100 events in DB
            
            cursor.execute('''
                INSERT INTO matches (season_id, deck1_id, deck2_id, winner_id, turns, game_log, log_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (self.season_number, id1, id2, winner_id, turns, log_json, log_path))
            
            k = 32
            
            cursor.execute('SELECT elo FROM decks WHERE id = ?', (id1,))
            elo1 = cursor.fetchone()['elo']
            cursor.execute('SELECT elo FROM decks WHERE id = ?', (id2,))
            elo2 = cursor.fetchone()['elo']
            
            r1 = 10 ** (elo1 / 400)
            r2 = 10 ** (elo2 / 400)
            e1 = r1 / (r1 + r2)
            e2 = r2 / (r1 + r2)
            
            if winner_id is None:
                s1, s2 = 0.5, 0.5
            elif winner_id == id1:
                s1, s2 = 1, 0
            else:
                s1, s2 = 0, 1
            
            new_elo1 = elo1 + k * (s1 - e1)
            new_elo2 = elo2 + k * (s2 - e2)
            
            cursor.execute('UPDATE decks SET elo = ?, wins = wins + ?, losses = losses + ?, draws = draws + ? WHERE id = ?', 
                           (new_elo1, int(s1 == 1), int(s1 == 0 and winner_id is not None), int(winner_id is None), id1))
            cursor.execute('UPDATE decks SET elo = ?, wins = wins + ?, losses = losses + ?, draws = draws + ? WHERE id = ?', 
                           (new_elo2, int(s2 == 1), int(s2 == 0 and winner_id is not None), int(winner_id is None), id2))
            
            conn.commit()

    def cull_weakest(self) -> int:
        """Retire the worst-performing decks — 5% of active population. Never cull Boss decks."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Get total active non-boss decks
            cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active=1 AND name NOT LIKE 'BOSS:%'")
            total = cursor.fetchone()['c']
            
            if total <= 50:
                return 0  # Don't cull if population is small
            
            # Cull 5% of population (min 3, max 50)
            max_cull = max(3, min(50, total // 20))
            
            # Find bottom decks by Elo with at least 6 matches played
            cursor.execute('''
                SELECT id, name, elo, wins, losses 
                FROM decks 
                WHERE active=1 AND name NOT LIKE 'BOSS:%' AND (wins + losses) >= 6
                ORDER BY elo ASC
                LIMIT ?
            ''', (max_cull,))
            
            victims = [dict(row) for row in cursor.fetchall()]
            
            for v in victims:
                cursor.execute('UPDATE decks SET active=0 WHERE id=?', (v['id'],))
            
            if victims:
                print(f"  ☠️  Retired {len(victims)} decks (worst Elo: {victims[0]['elo']:.0f})")
            
            conn.commit()
            return len(victims)

    def breed_from_champions(self) -> int:
        """Create new decks by evolving from the DNA of top-performing decks.
        Scales to 3% of population for offspring, plus wild card injections."""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get population size
            cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active=1 AND name NOT LIKE 'BOSS:%'")
            total_pop = cursor.fetchone()['c']
            
            # Get top 20 champions (or top 5% for large populations)
            champ_limit = max(5, min(20, total_pop // 20))
            cursor.execute('''
                SELECT id, name, card_list, elo, colors 
                FROM decks 
                WHERE active=1 AND name NOT LIKE 'BOSS:%' AND (wins + losses) >= 4
                ORDER BY elo DESC
                LIMIT ?
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
                             parent_ids=[champ['id']], colors=colors)
                    bred += 1
                except Exception:
                    pass
        
        if bred > 0:
            print(f"  🧬 Bred {bred} offspring from top {len(champions)} champions")
        
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
            except Exception:
                pass
        
        if wild_bred > 0:
            print(f"  🎲 {wild_bred} wild cards injected")
        
        return bred + wild_bred

    def resolve_promotions(self):
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
                print(f"  🏆 Boss Battles: {len(candidates)} challengers")
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT * FROM decks WHERE division='Mythic' AND name LIKE 'BOSS:%'")
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
                                runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                                result = runner.run()
                                if result.winner == p1.name:
                                    wins += 1
                                boss_logs.append(f"\n--- Game {gn+1} ({result.turns} turns, winner: {result.winner or 'Draw'}) ---")
                                boss_logs.extend(result.game_log)
                                w_id = cand['id'] if result.winner == p1.name else boss['id']
                                log_path = self._save_match_log(boss_logs)
                                self.update_match_result(cand['id'], boss['id'], w_id, result.turns,
                                                         game_log=result.game_log, log_path=log_path)
                            except:
                                pass
                        
                        if wins >= 2:
                            print(f"    ✅ {cand['name']} beat {boss['name']} ({wins}/3)!")
                            promoted.append(cand)
                        else:
                            print(f"    ❌ {cand['name']} lost to {boss['name']} ({wins}/3)")
            else:
                promoted = candidates
            
            relegated = []
            if upper_decks:
                relegate_count = max(1, len(upper_decks) // 5)
                upper_non_boss = [d for d in upper_decks if "BOSS:" not in d['name']]
                if upper_non_boss:
                    relegated = upper_non_boss[-relegate_count:]
                
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for d in promoted:
                    cursor.execute('UPDATE decks SET division = ? WHERE id = ?', (upper_div, d['id']))
                    print(f"  ⬆️  {d['name']} → {upper_div}")
                    
                for d in relegated:
                    cursor.execute('UPDATE decks SET division = ? WHERE id = ?', (lower_div, d['id']))
                    print(f"  ⬇️  {d['name']} → {lower_div}")
                conn.commit()
