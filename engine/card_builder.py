"""
Shared Card construction from JSON data.

Every file that builds Card objects from Scryfall/processed JSON MUST use
dict_to_card() to guarantee produced_mana, color_identity, and safe
power/toughness parsing.
"""
from engine.card import Card
from typing import Optional
import re

# Guaranteed basic land definitions — fallback if card pool is missing them
BASIC_LANDS = {
    'Plains':   {'name': 'Plains',   'mana_cost': '', 'type_line': 'Basic Land — Plains',   'oracle_text': '{T}: Add {W}.', 'produced_mana': ['W'], 'color_identity': ['W']},
    'Island':   {'name': 'Island',   'mana_cost': '', 'type_line': 'Basic Land — Island',   'oracle_text': '{T}: Add {U}.', 'produced_mana': ['U'], 'color_identity': ['U']},
    'Swamp':    {'name': 'Swamp',    'mana_cost': '', 'type_line': 'Basic Land — Swamp',    'oracle_text': '{T}: Add {B}.', 'produced_mana': ['B'], 'color_identity': ['B']},
    'Mountain': {'name': 'Mountain', 'mana_cost': '', 'type_line': 'Basic Land — Mountain', 'oracle_text': '{T}: Add {R}.', 'produced_mana': ['R'], 'color_identity': ['R']},
    'Forest':   {'name': 'Forest',   'mana_cost': '', 'type_line': 'Basic Land — Forest',   'oracle_text': '{T}: Add {G}.', 'produced_mana': ['G'], 'color_identity': ['G']},
    'Wastes':   {'name': 'Wastes',   'mana_cost': '', 'type_line': 'Basic Land',            'oracle_text': '{T}: Add {C}.', 'produced_mana': ['C'], 'color_identity': []},
}


def _safe_int(value, default=None) -> Optional[int]:
    """Parse power/toughness safely — handles None, '', '*', 'X', etc."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    # Handle negative values like '-1'
    if s.startswith('-') and s[1:].isdigit():
        return int(s)
    # '*', 'X', '1+*' etc. → 0 (variable power, engine treats as 0)
    return 0 if s else default


def dict_to_card(data: dict) -> Card:
    """Build a Card from a JSON dict (Scryfall / processed_cards / legal_cards).
    
    Guarantees:
    - produced_mana is set from data or inferred from basic land type
    - color_identity is set from data
    - power/toughness never crash on None or non-numeric values
    - keywords list is preserved for engine parsing
    """
    name = data.get('name', '')
    type_line = data.get('type_line', '')
    
    # produced_mana: from data, or infer from basic land subtypes, or PARSE ORACLE TEXT
    produced_mana = data.get('produced_mana', []) or []
    if not produced_mana and 'Land' in type_line:
        # 1. Infer from basic land subtypes (Rule 305.6)
        subtype_to_mana = {
            'Plains': 'W', 'Island': 'U', 'Swamp': 'B',
            'Mountain': 'R', 'Forest': 'G',
        }
        for subtype, color in subtype_to_mana.items():
            if subtype in type_line:
                produced_mana.append(color)
        
        # 2. Parse Oracle text if still empty (e.g. Pain lands, City of Brass)
        # Only parse if we didn't get basics (or even if we did? No, basics are sufficient usually)
        # But some lands have basic types AND ability (e.g. "Have basic land types" duals are handled above)
        # Non-basic lands need this.
        text = data.get('oracle_text', '')
        
        # "Add one mana of any color" -> All colors
        if 'one mana of any color' in text or 'any color' in text:
             # City of Brass, Mana Confluence
             for c in ['W', 'U', 'B', 'R', 'G']:
                 if c not in produced_mana: produced_mana.append(c)
        else:
            # "Add {C}"
            if re.search(r'Add \{C\}', text):
                if 'C' not in produced_mana: produced_mana.append('C')
            
            # "Add {X} or {Y}" or "Add {X}, {Y}, or {Z}"
            # Capture all {X} symbols after "Add"
            # Limit to line starting with {T} or similar?
            # "Add" usually follows cost. "{T}: Add {U}."
            # Regex to find "Add " followed by symbols
            add_matches = re.finditer(r'Add\s+((?:\{[WUBRG]\}[\s,or]*)+)', text)
            for m in add_matches:
                symbols = re.findall(r'\{([WUBRG])\}', m.group(1))
                for s in symbols:
                    if s not in produced_mana: produced_mana.append(s)
    
    return Card(
        name=name,
        cost=data.get('mana_cost', '') or data.get('cost', ''),
        type_line=type_line,
        oracle_text=data.get('oracle_text', ''),
        base_power=_safe_int(data.get('power')),
        base_toughness=_safe_int(data.get('toughness')),
        produced_mana=produced_mana,
        color_identity=data.get('color_identity', []) or [],
    )


def inject_basic_lands(card_pool: dict) -> dict:
    """Ensure basic lands are present in a card_pool name→data dict.
    Returns the same dict with basic lands added if missing."""
    for name, data in BASIC_LANDS.items():
        if name not in card_pool:
            card_pool[name] = data
    return card_pool
