#!/usr/bin/env python3
"""
Fetch and process card data from the Scryfall Bulk Data API.

Downloads the complete Oracle Cards dataset, extracts essential fields
(including produced_mana, image_uris, pricing, rarity), and saves
a processed JSON file ready for the engine.

Usage:
    python scripts/fetch_cards.py                   # Full download + process
    python scripts/fetch_cards.py --skip-download    # Re-process existing data
    python scripts/fetch_cards.py --format pioneer   # Filter to format
"""
import requests
import json
import os
import sys
import argparse

SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
ORACLE_CARDS_FILE = os.path.join(DATA_DIR, "oracle_cards_raw.json")


def _extract_image(card: dict) -> dict:
    """Extract image URIs, handling DFCs (double-faced cards)."""
    if 'image_uris' in card:
        uris = card['image_uris']
        return {
            'small': uris.get('small', ''),
            'normal': uris.get('normal', ''),
            'art_crop': uris.get('art_crop', ''),
        }
    # DFC: use front face
    if 'card_faces' in card and card['card_faces']:
        front = card['card_faces'][0]
        uris = front.get('image_uris', {})
        return {
            'small': uris.get('small', ''),
            'normal': uris.get('normal', ''),
            'art_crop': uris.get('art_crop', ''),
        }
    return {}


def fetch_oracle_cards():
    """Download the Oracle Cards bulk dataset from Scryfall."""
    print("Fetching Bulk Data metadata...")
    response = requests.get(SCRYFALL_BULK_DATA_URL, timeout=30)
    response.raise_for_status()
    data = response.json()

    oracle_cards_meta = next(
        item for item in data['data'] if item['type'] == 'oracle_cards'
    )
    download_uri = oracle_cards_meta['download_uri']
    updated_at = oracle_cards_meta.get('updated_at', 'unknown')

    print(f"Downloading Oracle Cards from {download_uri}...")
    print(f"  Scryfall last updated: {updated_at}")

    # Stream download
    total_bytes = 0
    with requests.get(download_uri, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(ORACLE_CARDS_FILE, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                total_bytes += len(chunk)

    print(f"Downloaded {total_bytes / (1024*1024):.1f} MB → {ORACLE_CARDS_FILE}")
    return updated_at


def process_cards(target_format=None):
    """Process downloaded cards into engine-ready JSON.

    Args:
        target_format: If set, only include cards legal in this format.
                       If None, include cards legal in any major format.
    """
    print("Processing cards...")
    with open(ORACLE_CARDS_FILE, 'r') as f:
        cards = json.load(f)

    print(f"Total cards in bulk data: {len(cards)}")

    processed_cards = []
    seen_names = set()

    # Skip non-paper layouts
    excluded_layouts = {'token', 'emblem', 'art_series', 'double_faced_token'}

    relevant_formats = [
        'standard', 'pioneer', 'modern', 'legacy', 'vintage',
        'commander', 'pauper'
    ]

    for card in cards:
        name = card.get('name', '')

        # Skip duplicates
        if name in seen_names:
            continue

        # Skip digital-only
        if card.get('digital', False):
            continue

        # Skip tokens, emblems, art series
        if card.get('layout', '') in excluded_layouts:
            continue

        # Skip funny/memorabilia sets
        set_type = card.get('set_type', '')
        if set_type in ('funny', 'memorabilia', 'token', 'minigame'):
            continue

        # Check paper availability
        games = card.get('games', [])
        if 'paper' not in games:
            continue

        # Legality check
        legalities = card.get('legalities', {})
        if target_format:
            if legalities.get(target_format) not in ('legal', 'restricted'):
                continue
        else:
            if not any(legalities.get(fmt) in ('legal', 'restricted')
                       for fmt in relevant_formats):
                continue

        # Handle DFCs: extract stats from front face if missing at top level
        mana_cost = card.get('mana_cost')
        power = card.get('power')
        toughness = card.get('toughness')
        colors = card.get('colors')
        oracle_text = card.get('oracle_text', '')
        produced_mana = card.get('produced_mana', [])

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
            if not oracle_text:
                oracle_text = front.get('oracle_text', '')
            if not produced_mana:
                produced_mana = front.get('produced_mana', [])

        simple_card = {
            'name': name,
            'mana_cost': mana_cost or '',
            'type_line': card.get('type_line', ''),
            'oracle_text': oracle_text,
            'power': power,
            'toughness': toughness,
            'cmc': card.get('cmc'),
            'colors': colors or [],
            'color_identity': card.get('color_identity', []),
            'keywords': card.get('keywords', []),
            'legalities': legalities,
            'defense': card.get('defense'),
            # New fields
            'produced_mana': produced_mana or [],
            'rarity': card.get('rarity', ''),
            'set': card.get('set', ''),
            'set_name': card.get('set_name', ''),
            'image_uris': _extract_image(card),
            'prices': {
                'usd': card.get('prices', {}).get('usd'),
                'usd_foil': card.get('prices', {}).get('usd_foil'),
            },
            'edhrec_rank': card.get('edhrec_rank'),
        }
        processed_cards.append(simple_card)
        seen_names.add(name)

    output_path = os.path.join(DATA_DIR, 'processed_cards.json')
    with open(output_path, 'w') as f:
        json.dump(processed_cards, f, indent=2)

    # Update metadata
    from datetime import datetime
    meta_path = os.path.join(DATA_DIR, 'pool_metadata.json')
    meta = {
        'last_updated': datetime.now().isoformat(),
        'format': target_format or 'all',
        'total_cards': len(processed_cards),
    }
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"Processed {len(processed_cards)} cards → {output_path}")
    return len(processed_cards)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and process card data from Scryfall."
    )
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download, re-process existing data")
    parser.add_argument("--format", default=None,
                        help="Filter to format (modern, pioneer, standard, etc.)")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    if not args.skip_download:
        fetch_oracle_cards()

    if not os.path.exists(ORACLE_CARDS_FILE):
        print(f"Error: {ORACLE_CARDS_FILE} not found. Run without --skip-download first.")
        sys.exit(1)

    process_cards(target_format=args.format)


if __name__ == "__main__":
    main()
