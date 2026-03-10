"""card_pool — Load card pool from Scryfall data into engine Card objects.

Reads legal_cards.json (output of sync_scryfall.py) and converts each entry
into an engine Card object with full Oracle text parsing.

Usage:
    from engine.card_pool import load_card_pool, get_cards_by_color
    pool = load_card_pool(format='standard')
    red_cards = get_cards_by_color(pool, 'R')
"""

import json
import logging
import os
from typing import List, Optional, Dict
from engine.card import Card

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')


def load_card_pool(format_name: str = 'standard', data_dir: str = None) -> List[Card]:
    """Load the card pool from processed Scryfall data.
    
    Args:
        format_name: MTG format to load ('standard', 'pioneer', 'modern', etc.)
        data_dir: Custom data directory (defaults to project data/)
    
    Returns:
        List of Card objects ready for use in the engine.
    """
    data_path = data_dir or DATA_DIR
    legal_path = os.path.join(data_path, 'legal_cards.json')
    
    if not os.path.exists(legal_path):
        raise FileNotFoundError(
            f"Card pool not found at {legal_path}. "
            f"Run 'python scripts/sync_scryfall.py --format {format_name}' first."
        )
    
    with open(legal_path, 'r') as f:
        raw_cards = json.load(f)
    
    cards = []
    skipped_backface = 0
    for data in raw_cards:
        # Filter by format legality
        legalities = data.get('legalities', {})
        if legalities.get(format_name) not in ('legal', 'restricted'):
            continue
        
        # ── Filter: Back-face / meld-result cards ──
        # Cards with no mana_cost that are creatures or planeswalkers are
        # meld results or transform back-faces (e.g., Ragnarok 7/6,
        # Mishra 9/9, Brisela 9/10). These cannot be cast from hand in
        # real Magic — they only appear through transform/meld mechanics.
        # Suspend spells (sorceries/instants with no cost) ARE kept.
        raw_cost = data.get('mana_cost', '')
        raw_type = data.get('type_line', '')
        if not raw_cost:
            is_creature_or_pw = ('Creature' in raw_type or 'Planeswalker' in raw_type)
            if is_creature_or_pw:
                skipped_backface += 1
                continue
        
        try:
            card = _scryfall_to_card(data)
            if card:
                cards.append(card)
        except Exception as e:
            # Skip cards that fail to parse (some edge cases)
            pass
    
    if skipped_backface:
        logging.getLogger(__name__).info(
            f"Filtered {skipped_backface} back-face/meld creatures from card pool"
        )
    
    return cards


def _scryfall_to_card(data: dict) -> Optional[Card]:
    """Convert a Scryfall JSON entry to an engine Card object."""
    name = data.get('name', '')
    cost = data.get('mana_cost', '')
    type_line = data.get('type_line', '')
    oracle_text = data.get('oracle_text', '')
    
    # Parse power/toughness
    power = None
    toughness = None
    if data.get('power') is not None:
        try:
            power = int(data['power'])
        except (ValueError, TypeError):
            power = 0  # Handle '*' power
    if data.get('toughness') is not None:
        try:
            toughness = int(data['toughness'])
        except (ValueError, TypeError):
            toughness = 0
    
    # Color identity
    color_identity = data.get('color_identity', [])
    
    card = Card(
        name=name,
        cost=cost,
        type_line=type_line,
        oracle_text=oracle_text,
        base_power=power,
        base_toughness=toughness,
        color_identity=color_identity
    )
    
    # Set produced_mana from Scryfall data (for lands)
    if data.get('produced_mana'):
        card.produced_mana = data['produced_mana']
    
    # Set loyalty for planeswalkers
    if card.is_planeswalker and data.get('loyalty'):
        try:
            card.loyalty = int(data['loyalty'])
        except (ValueError, TypeError):
            pass
    
    # Store metadata for display/filtering
    card._scryfall_data = {
        'rarity': data.get('rarity', ''),
        'set': data.get('set', ''),
        'image_uris': data.get('image_uris', {}),
        'prices': data.get('prices', {}),
        'edhrec_rank': data.get('edhrec_rank'),
        'cmc': data.get('cmc', 0),
    }
    
    return card


def get_cards_by_color(pool: List[Card], color: str) -> List[Card]:
    """Filter card pool to cards that include a specific color identity."""
    return [c for c in pool if color in c.color_identity]


def get_cards_by_type(pool: List[Card], card_type: str) -> List[Card]:
    """Filter card pool by type (creature, instant, sorcery, etc.)."""
    return [c for c in pool if card_type.lower() in c.type_line.lower()]


def get_cards_by_cmc(pool: List[Card], max_cmc: int) -> List[Card]:
    """Filter card pool to cards with CMC ≤ max_cmc."""
    from engine.player import Player
    return [c for c in pool if Player._parse_cmc(c.cost) <= max_cmc]


def get_pool_stats(pool: List[Card]) -> dict:
    """Get statistics about the card pool."""
    stats = {
        'total': len(pool),
        'creatures': sum(1 for c in pool if c.is_creature),
        'instants': sum(1 for c in pool if c.is_instant),
        'sorceries': sum(1 for c in pool if c.is_sorcery),
        'lands': sum(1 for c in pool if c.is_land),
        'planeswalkers': sum(1 for c in pool if c.is_planeswalker),
        'colors': {
            'W': sum(1 for c in pool if 'W' in c.color_identity),
            'U': sum(1 for c in pool if 'U' in c.color_identity),
            'B': sum(1 for c in pool if 'B' in c.color_identity),
            'R': sum(1 for c in pool if 'R' in c.color_identity),
            'G': sum(1 for c in pool if 'G' in c.color_identity),
        }
    }
    return stats
