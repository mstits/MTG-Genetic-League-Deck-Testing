"""
Pre-filter the card pool to only include tournament-legal paper cards.
Removes: digital-only (Alchemy), banned, un-sets, tokens, DFC back-faces, etc.
Flags: creatures with drawback mechanics (Death's Shadow, Phyrexian Dreadnought).
"""
import json
import os
import re
import sys

# Default to Legacy — allows testing with almost all cards (including bans in Modern/Standard)
FORMAT = os.environ.get('MTG_FORMAT', 'legacy')

# Patterns that indicate a creature has significant drawbacks
DRAWBACK_PATTERNS = [
    r'you lose .* life',                  # Death's Shadow, etc.
    r'sacrifice .* when',                 # Phyrexian Dreadnought, Ball Lightning
    r'sacrifice .* at',                   # Sacrifice at end of turn
    r'deals? .* damage to you',           # Sleeper Agent, etc.
    r"can't attack",                      # Moat-like drawbacks
    r"can't block",                       # Creatures that can't block
    r'skip your .* step',                 # Eater of Days
    r'your opponent .* gains? control',   # Opponent gains control
    r'you .* discard',                    # Drawback that forces discard
]
DRAWBACK_RE = re.compile('|'.join(DRAWBACK_PATTERNS), re.IGNORECASE)


def _has_drawback(card_data: dict) -> bool:
    """Check if a creature card has significant drawback mechanics."""
    text = card_data.get('oracle_text', '')
    if DRAWBACK_RE.search(text):
        return True
    # Extreme stat-to-cost ratio: likely a drawback card (e.g. 13/13 for {B})
    try:
        p = int(card_data.get('power', 0))
        t = int(card_data.get('toughness', 0))
        cmc = card_data.get('cmc', 0)
        if cmc <= 1 and p + t >= 10:
            return True
    except (ValueError, TypeError):
        pass
    return False


def filter_cards(input_path='data/processed_cards.json', output_path='data/legal_cards.json', fmt=FORMAT):
    with open(input_path, 'r') as f:
        all_cards = json.load(f)
    
    print(f"Total cards in pool: {len(all_cards)}")
    
    legal = []
    rejected = {
        'digital': 0, 'not_legal': 0, 'no_type': 0, 'token': 0,
        'funny': 0, 'back_face': 0, 'uncastable': 0,
    }
    
    seen_names = set()  # Deduplicate by name
    
    for card in all_cards:
        name = card.get('name', '')
        
        # Skip duplicates (keep first printing)
        if name in seen_names:
            continue
        
        # Skip digital-only cards (Alchemy, etc)
        if card.get('digital', False):
            rejected['digital'] += 1
            continue
        
        # Skip tokens
        type_line = card.get('type_line', '')
        if not type_line:
            rejected['no_type'] += 1
            continue
        if 'Token' in type_line:
            rejected['token'] += 1
            continue
        layout = card.get('layout', '')
        if layout in ('token', 'double_faced_token', 'emblem', 'art_series'):
            rejected['token'] += 1
            continue
        
        # === P0 FIX: Filter DFC back-faces (Rule 712.8a + 118.6) ===
        # Back-faces of transform/meld/modal_dfc cards have no mana cost.
        # In real MTG, a card with no mana cost has an unpayable cost and
        # cannot be cast from hand. Filter them entirely.
        mana_cost = card.get('mana_cost', '')
        if not mana_cost and 'Land' not in type_line:
            # Allow 0-cost artifacts/spells that actually cost {0} (e.g. Memnite)
            # Those have mana_cost = "{0}", not empty string ""
            # Also allow cards with explicit cmc=0 that ARE castable (Evoke, Suspend, etc.)
            # But filter back-faces and meld halves which truly have no cost
            if layout in ('transform', 'modal_dfc', 'meld', 'flip', 'reversible_card'):
                rejected['back_face'] += 1
                continue
            # Even outside known DFC layouts, empty mana_cost + non-land = uncastable
            # (catches edge cases from unusual layouts)
            if card.get('cmc', 0) == 0 and 'Creature' in type_line:
                # Exception: suspend/evoke creatures that have alt costs in text
                text = card.get('oracle_text', '').lower()
                if not any(kw in text for kw in ('suspend', 'evoke', 'cascade', 'living end',
                                                   'restore balance', 'convoke', 'affinity')):
                    rejected['uncastable'] += 1
                    continue
        
        # Skip un-sets / funny cards
        set_type = card.get('set_type', '')
        if set_type in ('funny', 'memorabilia', 'token', 'minigame'):
            rejected['funny'] += 1
            continue
        
        # Must be legal or restricted in the chosen format
        legalities = card.get('legalities', {})
        status = legalities.get(fmt, 'not_legal')
        if status not in ('legal', 'restricted'):
            rejected['not_legal'] += 1
            continue
        
        # === P0 FIX: Flag drawback creatures ===
        has_drawback = False
        if 'Creature' in type_line:
            has_drawback = _has_drawback(card)
        
        # Keep only the fields we need (slimmer data)
        slim = {
            'name': name,
            'mana_cost': mana_cost,
            'cmc': card.get('cmc', 0),
            'type_line': type_line,
            'oracle_text': card.get('oracle_text', ''),
            'power': card.get('power', ''),
            'toughness': card.get('toughness', ''),
            'colors': card.get('colors', []),
            'color_identity': card.get('color_identity', []),
            'keywords': card.get('keywords', []),
            'scryfall_uri': card.get('scryfall_uri', ''),
            'rarity': card.get('rarity', 'common'),
            'legalities': legalities,
            'layout': layout,
            'has_drawback': has_drawback,
            'produced_mana': card.get('produced_mana', []),
        }
        
        legal.append(slim)
        seen_names.add(name)
    
    with open(output_path, 'w') as f:
        json.dump(legal, f)
    
    print(f"\n{fmt.upper()}-legal paper cards: {len(legal)}")
    print(f"Rejected:")
    for reason, count in rejected.items():
        print(f"  {reason}: {count}")
    
    # Stats
    creatures = sum(1 for c in legal if 'Creature' in c.get('type_line', ''))
    instants = sum(1 for c in legal if 'Instant' in c.get('type_line', ''))
    sorceries = sum(1 for c in legal if 'Sorcery' in c.get('type_line', ''))
    lands = sum(1 for c in legal if 'Land' in c.get('type_line', ''))
    drawback_count = sum(1 for c in legal if c.get('has_drawback', False))
    
    print(f"\nBreakdown:")
    print(f"  Creatures:  {creatures}")
    print(f"  Instants:   {instants}")
    print(f"  Sorceries:  {sorceries}")
    print(f"  Lands:      {lands}")
    print(f"  Other:      {len(legal) - creatures - instants - sorceries - lands}")
    print(f"  With drawbacks: {drawback_count}")
    
    return legal

if __name__ == '__main__':
    fmt = sys.argv[1] if len(sys.argv) > 1 else FORMAT
    filter_cards(fmt=fmt)
