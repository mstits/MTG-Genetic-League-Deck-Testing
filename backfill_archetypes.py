import sqlite3
import json
import sys
import os

# Add project root to sys path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.archetype_classifier import classify_deck

def backfill():
    # Load legal cards pool
    pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'legal_cards.json')
    if not os.path.exists(pool_path):
        pool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'processed_cards.json')
    with open(pool_path, 'r') as f:
        pool_list = json.load(f)
    card_pool = {c['name']: c for c in pool_list}
    
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'league.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Ensure archetype column exists
    try:
        cursor.execute("ALTER TABLE decks ADD COLUMN archetype TEXT DEFAULT 'Unknown'")
        print("Added archetype column.")
    except sqlite3.OperationalError:
        pass # Already exists
        
    cursor.execute("SELECT id, card_list FROM decks WHERE archetype IS NULL OR archetype = 'Unknown'")
    rows = cursor.fetchall()
    print(f"Backfilling {len(rows)} decks...")
    
    batch = []
    for row_id, card_list_json in rows:
        try:
            cl = json.loads(card_list_json)
            if isinstance(cl, list):
                counts = {}
                for name in cl: counts[name] = counts.get(name, 0) + 1
                cl = counts
                
            arch = classify_deck(cl, card_pool)['archetype']
            batch.append((arch, row_id))
        except Exception as e:
            print(f"Failed on {row_id}: {e}")
            
    cursor.executemany("UPDATE decks SET archetype = ? WHERE id = ?", batch)
    conn.commit()
    conn.close()
    print("Done!")

if __name__ == '__main__':
    backfill()
