#!/usr/bin/env python3
"""
Unified Scryfall data sync — fetches, processes, and filters cards.

Combines fetch_cards.py + filter_legal.py + monthly_refresh.py into
a single CLI. Run this to get a fully up-to-date card pool.

Usage:
    python scripts/sync_scryfall.py                     # Full refresh (modern)
    python scripts/sync_scryfall.py --format pioneer     # Pioneer-specific
    python scripts/sync_scryfall.py --skip-download      # Re-process only
    python scripts/sync_scryfall.py --all-formats        # Keep all legal cards
"""
import json
import os
import sys
import argparse
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# Add project root to path
sys.path.insert(0, BASE_DIR)


def sync(format_name='modern', skip_download=False, all_formats=False):
    """Full sync: download → process → filter → metadata."""
    from scripts.fetch_cards import fetch_oracle_cards, process_cards, ORACLE_CARDS_FILE

    print(f"🔄 [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Scryfall sync starting...")
    print(f"   Format: {format_name if not all_formats else 'all'}")

    os.makedirs(DATA_DIR, exist_ok=True)

    # Step 1: Download
    scryfall_updated = None
    if not skip_download:
        print("\n─── Step 1: Download Oracle Cards ───")
        scryfall_updated = fetch_oracle_cards()
    else:
        print("\n─── Step 1: Skipping download (--skip-download) ───")
        if not os.path.exists(ORACLE_CARDS_FILE):
            print(f"  ❌ {ORACLE_CARDS_FILE} not found. Run without --skip-download.")
            return False

    # Step 2: Process into processed_cards.json
    print("\n─── Step 2: Process cards ───")
    target = None if all_formats else format_name
    total = process_cards(target_format=target)

    # Step 3: Generate legal_cards.json (format-filtered, engine-ready)
    print("\n─── Step 3: Generate legal card pool ───")
    processed_path = os.path.join(DATA_DIR, 'processed_cards.json')
    legal_path = os.path.join(DATA_DIR, 'legal_cards.json')

    with open(processed_path, 'r') as f:
        all_cards = json.load(f)

    legal_cards = []
    for card in all_cards:
        legalities = card.get('legalities', {})
        if all_formats:
            legal_cards.append(card)
        elif legalities.get(format_name) in ('legal', 'restricted'):
            legal_cards.append(card)

    with open(legal_path, 'w') as f:
        json.dump(legal_cards, f, indent=1)

    print(f"  Legal cards ({format_name}): {len(legal_cards)} → {legal_path}")

    # Step 4: Update metadata
    print("\n─── Step 4: Save metadata ───")
    meta = {
        'last_updated': datetime.now().isoformat(),
        'scryfall_updated': scryfall_updated or 'cached',
        'format': format_name if not all_formats else 'all',
        'total_cards': total,
        'legal_cards': len(legal_cards),
    }
    meta_path = os.path.join(DATA_DIR, 'pool_metadata.json')
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"\n✅ Sync complete!")
    print(f"   Processed: {total} cards")
    print(f"   Legal ({format_name}): {len(legal_cards)} cards")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Unified Scryfall data sync — fetch, process, filter.",
        epilog="""Examples:
  python scripts/sync_scryfall.py                     # Modern refresh
  python scripts/sync_scryfall.py --format pioneer     # Pioneer
  python scripts/sync_scryfall.py --skip-download      # Re-process cached data
  python scripts/sync_scryfall.py --all-formats        # Keep everything"""
    )
    parser.add_argument("--format", default="modern",
                        help="MTG format (modern, pioneer, standard, legacy, etc.)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Use cached bulk data instead of re-downloading")
    parser.add_argument("--all-formats", action="store_true",
                        help="Keep all format-legal cards (don't filter to one)")
    args = parser.parse_args()

    success = sync(
        format_name=args.format,
        skip_download=args.skip_download,
        all_formats=args.all_formats,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
