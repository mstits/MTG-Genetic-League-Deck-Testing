"""Shared caches and template instances for the web layer.

This module exists to break circular imports. Route modules (views, decks,
simulation, etc.) need access to the Jinja2 templates instance and the
card-pool caches, but they can't import from web.app without creating a
circular dependency (app → routes → app).

By placing these shared objects here, both app.py and all route modules
can import from web.cache without cycles.

Usage in route modules:
    from web.cache import templates, get_card_pool, get_card_search_cache
"""

import os
import json
import logging
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

# ─── Templates ────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))         # web/
PROJECT_ROOT = os.path.dirname(BASE_DIR)                      # MTG Deck Testing/
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ─── Card Pool Cache ─────────────────────────────────────────────────────────

_card_pool_cache = None


def get_card_pool() -> dict:
    """Load the full card pool once and cache at module level.

    Returns a dict mapping card name → card data dict.
    Thread-safe via GIL (single-writer pattern).
    """
    global _card_pool_cache
    if _card_pool_cache is not None:
        return _card_pool_cache

    cp_path = os.path.join(PROJECT_ROOT, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(PROJECT_ROOT, 'data', 'processed_cards.json')

    card_pool = {}
    with open(cp_path) as f:
        for c in json.load(f):
            name = c.get('name', '')
            card_pool[name] = c
            if ' // ' in name:
                front_face = name.split(' // ')[0].strip()
                if front_face not in card_pool:
                    card_pool[front_face] = c

    from engine.card_builder import inject_basic_lands
    inject_basic_lands(card_pool)

    _card_pool_cache = card_pool
    logger.info("Card pool cached: %d cards", len(card_pool))
    return card_pool


# ─── Card Search Cache ───────────────────────────────────────────────────────

_card_search_cache = None


def get_card_search_cache() -> list[dict]:
    """Load card names/metadata once for autocomplete.

    Returns a list of dicts with name, name_lower, mana_cost, type_line,
    colors, and cmc for fuzzy search.
    """
    global _card_search_cache
    if _card_search_cache is not None:
        return _card_search_cache

    cp_path = os.path.join(PROJECT_ROOT, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(PROJECT_ROOT, 'data', 'processed_cards.json')

    with open(cp_path) as f:
        raw = json.load(f)

    _card_search_cache = []
    for c in raw:
        name = c.get('name', '')
        _card_search_cache.append({
            'name': name,
            'name_lower': name.lower(),
            'mana_cost': c.get('mana_cost', ''),
            'type_line': c.get('type_line', ''),
            'colors': c.get('colors', []),
            'cmc': c.get('cmc', 0),
        })
    return _card_search_cache
