"""Run League — Main entry point for the continuous evolutionary league.

Seeds initial deck population (~425 decks across 25 color combinations),
deploys boss decks, then runs seasons indefinitely.  Each season pairs
decks for Bo3 matches, updates ELO, retires weak decks, and breeds
new ones from winners.

Usage:
    python run_league.py          # Start the league (Ctrl+C to stop)
    Dashboard: http://localhost:8000 (run web server separately)
"""

from league.manager import LeagueManager
from data.db import save_deck, get_db_connection
from simulation.parallel import seed_decks_parallel
import json
import random
import time
import sys
import os

# All 25 color combinations: 5 mono + 10 two-color + 10 three-color
MONO_COLORS = ["W", "U", "B", "R", "G"]
TWO_COLORS = ["WU", "WB", "WR", "WG", "UB", "UR", "UG", "BR", "BG", "RG"]
THREE_COLORS = ["WUB", "WUR", "WBR", "WBG", "WUG", "WRG", "UBR", "UBG", "URG", "BRG"]

# Decks per color combo — more for popular combos, fewer for 3-color
SEED_COUNTS = {}
for c in MONO_COLORS:
    SEED_COUNTS[c] = 25        # 125 mono decks
for c in TWO_COLORS:
    SEED_COUNTS[c] = 20        # 200 two-color decks
for c in THREE_COLORS:
    SEED_COUNTS[c] = 10        # 100 three-color decks
# Total: ~425 seeded decks + boss decks

TARGET_POPULATION = 1000  # Target active population


def seed_initial_decks():
    """Seed hundreds of diverse decks across all color combos using parallel workers."""
    with get_db_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM decks WHERE active=1").fetchone()[0]
        
    if count >= 50:
        print(f"  Already have {count} active decks, skipping seed")
        return
    
    print(f"🌱 Mass seeding initial deck population...")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(base_dir, 'data', 'legal_cards.json')
    if not os.path.exists(data_path):
        data_path = os.path.join(base_dir, 'data', 'processed_cards.json')
    
    with open(data_path, 'r') as f:
        all_cards_list = json.load(f)
        
    # Ensure basic lands are present
    from engine.card_builder import BASIC_LANDS
    all_cards_map = {c['name']: c for c in all_cards_list}
    for name, data in BASIC_LANDS.items():
        if name not in all_cards_map:
            all_cards_list.append(data)
            
    all_cards = all_cards_list
    
    # Build seed list
    color_combos_with_counts = [(c, n) for c, n in SEED_COUNTS.items()]
    total_seeds = sum(n for _, n in color_combos_with_counts)
    
    print(f"  Target: {total_seeds} decks across {len(color_combos_with_counts)} color combos")
    
    # Parallel seeding
    start = time.time()
    results = seed_decks_parallel(color_combos_with_counts, all_cards)
    elapsed = time.time() - start
    
    print(f"  Generated {len(results)} decks in {elapsed:.1f}s")
    
    # Batch insert to DB
    success = 0
    for deck_data in results:
        try:
            save_deck(
                deck_data['name'], 
                deck_data['cards'], 
                generation=0, 
                colors=deck_data['colors']
            )
            success += 1
        except Exception as e:
            pass
    
    print(f"  ✅ Seeded {success} decks to database")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    
    print("🏟️  MTG GENETIC LEAGUE — SCALED MODE")
    print("=" * 50)
    print(f"Target population: {TARGET_POPULATION}+ active decks")
    print("Initializing...\n")
    
    seed_initial_decks()
    
    lm = LeagueManager()
    season = 1
    
    # Check current population
    with get_db_connection() as conn:
        active = conn.execute("SELECT COUNT(*) FROM decks WHERE active=1").fetchone()[0]
    
    print(f"\n🟢 LEAGUE RUNNING — {active} active decks")
    print(f"   Dashboard at http://localhost:8000")
    print("   Press Ctrl+C to stop\n")
    
    while True:
        try:
            lm.run_season(games_per_deck=4)
            
            # Every 5 seasons, print meta report + population check
            if season % 5 == 0:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT COUNT(*) FROM matches')
                    total = cursor.fetchone()[0]
                    cursor.execute('SELECT COUNT(*) FROM decks WHERE active=1')
                    active = cursor.fetchone()[0]
                    cursor.execute("SELECT COUNT(*) FROM decks WHERE active=0")
                    retired = cursor.fetchone()[0]
                    cursor.execute('''
                        SELECT name, elo, wins, losses, colors FROM decks 
                        WHERE active=1 AND name NOT LIKE 'BOSS:%'
                        ORDER BY elo DESC LIMIT 5
                    ''')
                    top5 = [dict(r) for r in cursor.fetchall()]
                
                print(f"\n{'='*50}")
                print(f"  📊 META REPORT — Season {season}")
                print(f"  Matches: {total} | Active: {active} | Retired: {retired}")
                for i, d in enumerate(top5):
                    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣']
                    wr = f"{d['wins']/(d['wins']+d['losses'])*100:.0f}%" if d['wins']+d['losses'] > 0 else "—"
                    print(f"  {medals[i]} {d['name']} ({d['colors']}) Elo:{d['elo']:.0f} {wr}")
                print(f"{'='*50}\n")
            
            season += 1
            time.sleep(0.2)  # Brief pause between seasons
            
        except KeyboardInterrupt:
            print("\n\nLeague stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"\n⚠️  Season error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(2)
