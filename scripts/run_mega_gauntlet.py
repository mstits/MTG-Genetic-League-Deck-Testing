#!/usr/bin/env python3
import os
import os
import json
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.historical_gauntlet import run_gauntlet, HISTORICAL_ERAS
from data.db import get_db_connection

def get_top_deck():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, name, card_list, elo FROM decks WHERE active=true ORDER BY elo DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    return dict(row)
    except Exception as e:
        print(f"DB Error: {e}")
    return None

def main():
    top_deck = get_top_deck()
    if not top_deck:
        print("No decks found in database. Using default test deck.")
        decklist = {
            "Nadu, Winged Wisdom": 4, "Shuko": 4, "Outrider en-Kor": 4,
            "Springheart Nantuko": 4, "Sylvan Safekeeper": 4,
            "Summoner's Pact": 4, "Boseiju, Who Endures": 2,
            "Breeding Pool": 4, "Misty Rainforest": 4, "Forest": 5, "Island": 5,
            "Urza's Saga": 4, "Chord of Calling": 4, "Otawara, Soaring City": 1,
            "Haywire Mite": 1, "Sylvan Caryatid": 4,
        }
        deck_name = "Nadu Test Deck"
    else:
        deck_name = f"Deck #{top_deck['id']} ({top_deck['elo']} ELO)"
        dl = json.loads(top_deck['card_list'])
        if isinstance(dl, list):
            counts = {}
            for n in dl: counts[n] = counts.get(n, 0) + 1
            decklist = counts
        else:
            decklist = dl

    print(f"Running Mega-Gauntlet for {deck_name}...\n")
    
    # Run against all eras
    for era in sorted(HISTORICAL_ERAS.keys()):
        print(f"--- Era: {era} ---")
        result = run_gauntlet(decklist, era, matches_per_opponent=1) # 1 match for speed
        print(f"Results: {result['total_wins']}W - {result['total_losses']}L - {result['total_draws']}D")
        print(f"Verdict: {result['verdict']} ({result['win_rate']:.1f}% WR)\n")

if __name__ == "__main__":
    main()
