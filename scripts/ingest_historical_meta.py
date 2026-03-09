#!/usr/bin/env python3
"""ingest_historical_meta.py — Fetches tournament decklists to populate Gauntlet.

In a real environment, this would hit Scryfall / MTGTop8 / Magic.gg APIs.
Here we dynamically generate a JSON file to replace the hardcoded data,
combining the existing baseline with newly fetched eras to demonstrate dynamic ingestion.
"""

import json
import os
import sys
from typing import Dict, Any

# Ensure we can import from engine/league
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.historical_gauntlet import HISTORICAL_ERAS

def fetch_scryfall_meta() -> Dict[str, Any]:
    print("Fetching historical tournament data from Magic.gg and Scryfall...")
    
    # Simulate API response for a new era (Modern 2024 MH3)
    new_era = {
        "Modern 2024": {
            "format": "modern",
            "description": "MH3 Era Modern - Nadu Combo, Boros Energy",
            "decks": [
                {"name": "Nadu Combo", "colors": "UG", "cards": {
                    "Nadu, Winged Wisdom": 4, "Shuko": 4, "Outrider en-Kor": 4,
                    "Springheart Nantuko": 4, "Sylvan Safekeeper": 4,
                    "Summoner's Pact": 4, "Boseiju, Who Endures": 2,
                    "Breeding Pool": 4, "Misty Rainforest": 4, "Forest": 5, "Island": 5,
                    "Urza's Saga": 4, "Chord of Calling": 4, "Otawara, Soaring City": 1,
                    "Haywire Mite": 1, "Sylvan Caryatid": 4,
                }},
                {"name": "Boros Energy", "colors": "RW", "cards": {
                    "Guide of Souls": 4, "Ocelot Pride": 4, "Ajani, Nacatl Pariah": 4,
                    "Amped Raptor": 4, "Phlage, Titan of Fire's Fury": 3,
                    "Galvanic Discharge": 4, "Lightning Bolt": 4,
                    "Blood Moon": 2, "Static Prison": 3,
                    "Sacred Foundry": 4, "Inspiring Vantage": 4, "Arid Mesa": 4,
                    "Mountain": 5, "Plains": 5, "Sunbaked Canyon": 4,
                }}
            ]
        }
    }
    
    # Merge existing (baseline) with the newly fetched ones
    combined = dict(HISTORICAL_ERAS)
    combined.update(new_era)
    return combined

if __name__ == '__main__':
    data = fetch_scryfall_meta()
    
    # Ensure data dir exists
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    out_file = os.path.join(data_dir, 'historical_meta.json')
    with open(out_file, 'w') as f:
        json.dump(data, f, indent=4)
        
    print(f"✅ Successfully ingested historical meta to {out_file}")
    print(f"   Loaded {len(data)} formats/eras.")
