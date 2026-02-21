import requests
import json
import os
import gzip
import shutil

SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
DATA_DIR = "../data"
ORACLE_CARDS_FILE = os.path.join(DATA_DIR, "oracle_cards.json")

def fetch_oracle_cards():
    print("Fetching Bulk Data metadata...")
    response = requests.get(SCRYFALL_BULK_DATA_URL)
    response.raise_for_status()
    data = response.json()
    
    oracle_cards_meta = next(item for item in data['data'] if item['type'] == 'oracle_cards')
    download_uri = oracle_cards_meta['download_uri']
    
    print(f"Downloading Oracle Cards from {download_uri}...")
    
    # Stream download
    with requests.get(download_uri, stream=True) as r:
        r.raise_for_status()
        with open(ORACLE_CARDS_FILE, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192): 
                f.write(chunk)
                
    print(f"Downloaded to {ORACLE_CARDS_FILE}")

def process_cards():
    print("Processing cards...")
    with open(ORACLE_CARDS_FILE, 'r') as f:
        cards = json.load(f)

        
    processed_cards = []
    
    print(f"Total cards in bulk data: {len(cards)}")
    
    for card in cards:
        # Filter for relevant formats or just legal in general?
        # Let's keep legal in at least one major format to save space/time
        legalities = card.get('legalities', {})
        relevant_formats = ['standard', 'pioneer', 'modern', 'legacy', 'vintage', 'commander', 'pauper']
        if not any(legalities.get(fmt) == 'legal' for fmt in relevant_formats):
            continue
            
        # Handle DFCs/Battles: extract stats from front face if missing at top level
        mana_cost = card.get('mana_cost')
        power = card.get('power')
        toughness = card.get('toughness')
        colors = card.get('colors')
        
        if 'card_faces' in card and (mana_cost is None or mana_cost == ""):
            front = card['card_faces'][0]
            if mana_cost is None or mana_cost == "":
                mana_cost = front.get('mana_cost', '')
            if power is None:
                power = front.get('power')
            if toughness is None:
                toughness = front.get('toughness')
            if colors is None:
                colors = front.get('colors')

        # Simplified object
        simple_card = {
            'name': card.get('name'),
            'mana_cost': mana_cost,
            'type_line': card.get('type_line', ''),
            'oracle_text': card.get('oracle_text', ''),
            'power': power,
            'toughness': toughness,
            'cmc': card.get('cmc'),
            'colors': colors,
            'color_identity': card.get('color_identity'),
            'keywords': card.get('keywords', []),
            'legalities': legalities,  # CRITICAL for filter script
            'defense': card.get('defense') # For Battles
        }
        processed_cards.append(simple_card)
        
    # Save to data directory
    # Determine correct path relative to this script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Go up one level from 'scripts' to project root, then into 'data'
    project_root = os.path.dirname(script_dir)
    output_path = os.path.join(project_root, 'data', 'processed_cards.json')
    
    with open(output_path, 'w') as f:
        json.dump(processed_cards, f, indent=2)
    
    print(f"Processed {len(processed_cards)} relevant cards. Saved to {output_path}")

if __name__ == "__main__":
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        
    fetch_oracle_cards()
    process_cards()
