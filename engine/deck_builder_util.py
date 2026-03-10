"""Deck builder — shared utility for constructing Deck objects from card dicts.

Used by simulation routes, parallel workers, historical gauntlet, and any
code that needs to turn a {card_name: count} dict into a playable Deck.

This eliminates 4 duplicate build_deck/make_deck functions that existed
across the codebase.
"""

import logging

from engine.deck import Deck
from engine.card_builder import dict_to_card, inject_basic_lands

logger = logging.getLogger(__name__)


def build_deck(
    card_dict: dict[str, int] | list[str],
    card_pool: dict[str, dict],
    *,
    skip_missing: bool = True,
) -> Deck:
    """Build a Deck object from a card-name->count dictionary.

    Args:
        card_dict: Mapping of card name -> copy count (e.g. {"Lightning Bolt": 4}).
                   Also accepts list format (["Bolt", "Bolt"] -> {"Bolt": 2}).
        card_pool: Mapping of card name -> Scryfall-style card data dict.
        skip_missing: If True, silently skip unrecognized cards.
                      If False, raise KeyError on missing cards.

    Returns:
        A Deck populated with Card objects ready for game simulation.
    """
    inject_basic_lands(card_pool)

    # Normalize list format to dict
    if isinstance(card_dict, list):
        counts: dict[str, int] = {}
        for n in card_dict:
            counts[n] = counts.get(n, 0) + 1
        card_dict = counts

    deck = Deck()
    for name, count in card_dict.items():
        try:
            data = card_pool.get(name)
            if data:
                card_obj = dict_to_card(data)
                deck.add_card(card_obj, count)
            elif not skip_missing:
                raise KeyError(f"Card '{name}' not found in card pool")
        except KeyError:
            raise
        except Exception as e:
            logger.warning("Skipping corrupt card '%s': %s", name, e, exc_info=True)

    return deck
