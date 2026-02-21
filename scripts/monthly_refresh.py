#!/usr/bin/env python3
"""
Monthly card pool refresh — pulls latest card data from Scryfall bulk API
and regenerates the legal card pool.

Run monthly via launchctl or cron:
  python scripts/monthly_refresh.py
"""
import json
import os
import sys
import time
import requests
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

def download_bulk_cards():
    """Download the latest Oracle Cards bulk data from Scryfall."""
    print(f"📦 [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Starting monthly card refresh...")
    
    # Step 1: Get bulk data URL
    print("  Fetching bulk data catalog...")
    bulk_url = "https://api.scryfall.com/bulk-data"
    try:
        resp = requests.get(bulk_url, timeout=30)
        resp.raise_for_status()
        bulk_data = resp.json()
    except Exception as e:
        print(f"  ❌ Failed to fetch bulk catalog: {e}")
        return False
    
    # Find "Oracle Cards" dataset
    oracle_entry = None
    for entry in bulk_data.get('data', []):
        if entry['type'] == 'oracle_cards':
            oracle_entry = entry
            break
    
    if not oracle_entry:
        print("  ❌ Could not find oracle_cards bulk data entry")
        return False
    
    download_uri = oracle_entry['download_uri']
    updated_at = oracle_entry.get('updated_at', 'unknown')
    print(f"  Downloading from: {download_uri}")
    print(f"  Last updated: {updated_at}")
    
    # Step 2: Download the JSON
    try:
        resp = requests.get(download_uri, timeout=120, stream=True)
        resp.raise_for_status()
        
        raw_path = os.path.join(DATA_DIR, 'scryfall_bulk.json')
        total = 0
        with open(raw_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024*1024):
                f.write(chunk)
                total += len(chunk)
        
        print(f"  ✅ Downloaded {total / (1024*1024):.1f} MB → {raw_path}")
        
    except Exception as e:
        print(f"  ❌ Download failed: {e}")
        return False
    
    # Step 3: Re-run the legal filter
    print("  🔄 Re-filtering legal card pool...")
    
    try:
        # Load downloaded data
        with open(raw_path, 'r') as f:
            all_cards = json.load(f)
        
        print(f"  Raw cards: {len(all_cards)}")
        
        mtg_format = os.environ.get('MTG_FORMAT', 'modern')
        
        # Filter to legal paper cards
        legal_cards = []
        seen_names = set()
        
        excluded_layouts = {'token', 'emblem', 'art_series', 'double_faced_token'}
        
        for card in all_cards:
            name = card.get('name', '')
            layout = card.get('layout', '')
            
            # Skip duplicates
            if name in seen_names:
                continue
            
            # Skip tokens, emblems, art series
            if layout in excluded_layouts:
                continue
            
            # Skip digital-only
            if card.get('digital', False):
                continue
            
            # Skip "funny" sets
            set_type = card.get('set_type', '')
            if set_type in ('funny', 'memorabilia', 'token', 'minigame'):
                continue
            
            # Check legality
            legalities = card.get('legalities', {})
            if legalities.get(mtg_format) not in ('legal', 'restricted'):
                continue
            
            # Check paper availability
            games = card.get('games', [])
            if 'paper' not in games:
                continue
            
            # Extract essential fields
            filtered = {
                'name': name,
                'mana_cost': card.get('mana_cost', ''),
                'type_line': card.get('type_line', ''),
                'oracle_text': card.get('oracle_text', ''),
                'power': card.get('power', ''),
                'toughness': card.get('toughness', ''),
                'color_identity': card.get('color_identity', []),
                'scryfall_uri': card.get('scryfall_uri', ''),
            }
            
            legal_cards.append(filtered)
            seen_names.add(name)
        
        # Save
        output_path = os.path.join(DATA_DIR, 'legal_cards.json')
        with open(output_path, 'w') as f:
            json.dump(legal_cards, f, indent=1)
        
        # Save metadata
        meta_path = os.path.join(DATA_DIR, 'pool_metadata.json')
        meta = {
            'last_updated': datetime.now().isoformat(),
            'scryfall_updated': updated_at,
            'format': mtg_format,
            'total_cards': len(legal_cards),
            'raw_cards': len(all_cards),
        }
        with open(meta_path, 'w') as f:
            json.dump(meta, f, indent=2)
        
        print(f"  ✅ Legal card pool: {len(legal_cards)} cards → {output_path}")
        print(f"  ✅ Metadata saved → {meta_path}")
        
        # Cleanup bulk file
        os.remove(raw_path)
        print(f"  🗑️ Cleaned up bulk download")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Filter failed: {e}")
        return False

if __name__ == "__main__":
    success = download_bulk_cards()
    if success:
        print("\n✅ Monthly refresh complete!")
    else:
        print("\n❌ Monthly refresh failed!")
        sys.exit(1)
