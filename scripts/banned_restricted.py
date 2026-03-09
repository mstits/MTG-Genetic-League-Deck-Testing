#!/usr/bin/env python3
"""banned_restricted — Auto-fetch Banned & Restricted announcements.

Fetches current B&R status from Scryfall API legalities and:
1. Filters the card pool to remove banned cards
2. Alerts when evolved decks contain newly-banned cards
3. Tracks B&R history for format analysis

Usage:
    python scripts/banned_restricted.py --format standard
    python scripts/banned_restricted.py --format modern --audit
    python scripts/banned_restricted.py --all-formats
"""

import json
import os
import sys
import argparse
from datetime import datetime
from typing import List, Dict, Set

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
sys.path.insert(0, BASE_DIR)


def fetch_banlist(format_name: str) -> Dict[str, str]:
    """Fetch current ban status for all cards in a format.
    
    Returns dict of card_name → legality_status ('banned', 'restricted', 'not_legal')
    """
    processed_path = os.path.join(DATA_DIR, 'processed_cards.json')
    if not os.path.exists(processed_path):
        print(f"❌ Card data not found. Run sync_scryfall.py first.")
        return {}
    
    with open(processed_path, 'r') as f:
        all_cards = json.load(f)
    
    banned = {}
    for card in all_cards:
        legalities = card.get('legalities', {})
        status = legalities.get(format_name, 'not_legal')
        if status == 'banned':
            banned[card['name']] = 'banned'
        elif status == 'restricted':
            banned[card['name']] = 'restricted'
    
    return banned


def filter_card_pool(format_name: str) -> int:
    """Remove banned cards from the legal card pool.
    
    Returns number of cards removed.
    """
    legal_path = os.path.join(DATA_DIR, 'legal_cards.json')
    if not os.path.exists(legal_path):
        print(f"❌ Legal card pool not found. Run sync_scryfall.py first.")
        return 0
    
    with open(legal_path, 'r') as f:
        cards = json.load(f)
    
    original_count = len(cards)
    
    # Filter out banned cards
    filtered = [c for c in cards 
                if c.get('legalities', {}).get(format_name) in ('legal', 'restricted')]
    
    removed_count = original_count - len(filtered)
    
    with open(legal_path, 'w') as f:
        json.dump(filtered, f, indent=1)
    
    return removed_count


def audit_deck(deck_card_names: List[str], format_name: str) -> List[Dict]:
    """Check if a deck contains any banned cards.
    
    Returns list of {card, status, action} for violations.
    """
    banned = fetch_banlist(format_name)
    violations = []
    
    for name in deck_card_names:
        if name in banned:
            violations.append({
                'card': name,
                'status': banned[name],
                'action': 'remove' if banned[name] == 'banned' else 'limit_to_1',
                'format': format_name,
            })
    
    return violations


def save_banlist_snapshot(format_name: str):
    """Save current banlist with timestamp for historical tracking."""
    banned = fetch_banlist(format_name)
    
    history_dir = os.path.join(DATA_DIR, 'br_history')
    os.makedirs(history_dir, exist_ok=True)
    
    snapshot = {
        'format': format_name,
        'timestamp': datetime.now().isoformat(),
        'banned_count': sum(1 for s in banned.values() if s == 'banned'),
        'restricted_count': sum(1 for s in banned.values() if s == 'restricted'),
        'cards': banned,
    }
    
    filename = f"br_{format_name}_{datetime.now().strftime('%Y%m%d')}.json"
    path = os.path.join(history_dir, filename)
    
    with open(path, 'w') as f:
        json.dump(snapshot, f, indent=2)
    
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Banned & Restricted auto-update from Scryfall data"
    )
    parser.add_argument("--format", default="standard",
                        help="MTG format (standard, pioneer, modern, legacy, commander)")
    parser.add_argument("--audit", action="store_true",
                        help="Audit mode: show banned cards only")
    parser.add_argument("--all-formats", action="store_true",
                        help="Check all major formats")
    args = parser.parse_args()
    
    formats = ['standard', 'pioneer', 'modern', 'legacy', 'vintage', 'commander'] \
              if args.all_formats else [args.format]
    
    for fmt in formats:
        print(f"\n{'='*50}")
        print(f"📋 B&R Status: {fmt.upper()}")
        print(f"{'='*50}")
        
        banned = fetch_banlist(fmt)
        banned_cards = {k: v for k, v in banned.items() if v == 'banned'}
        restricted_cards = {k: v for k, v in banned.items() if v == 'restricted'}
        
        print(f"  Banned: {len(banned_cards)} cards")
        if banned_cards:
            for name in sorted(banned_cards.keys())[:20]:
                print(f"    🚫 {name}")
            if len(banned_cards) > 20:
                print(f"    ... and {len(banned_cards)-20} more")
        
        print(f"  Restricted: {len(restricted_cards)} cards")
        if restricted_cards:
            for name in sorted(restricted_cards.keys()):
                print(f"    ⚠️  {name}")
        
        if not args.audit:
            removed = filter_card_pool(fmt)
            print(f"  Filtered: {removed} cards removed from pool")
            
            snapshot_path = save_banlist_snapshot(fmt)
            print(f"  Snapshot saved: {snapshot_path}")
    
    print(f"\n✅ B&R update complete ({datetime.now().strftime('%Y-%m-%d %H:%M')})")


if __name__ == "__main__":
    main()
