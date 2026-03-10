"""engine — MTG game engine implementing Magic: The Gathering rules.

Core modules:
    card    — Card model with keyword parsing, effect generation, and Oracle text processing
    game    — Game loop, phases, stack, priority system, combat, and state-based actions
    player  — Player state: life, hand, library, mana pool, and cost payment
    deck    — Deck blueprints and fresh game-deck generation
    zone    — Generic card container (library, hand, graveyard, battlefield, stack)
    card_builder     — Converts raw Scryfall JSON into Card instances
    deck_builder_util — Shared deck construction from card dicts (DRY utility)
"""

__all__ = [
    "Card",
    "Deck",
    "Game",
    "Player",
    "Zone",
    "build_deck",
]

# Lazy imports — only resolve when accessed via `from engine import X`
def __getattr__(name: str):
    if name == "Card":
        from engine.card import Card
        return Card
    elif name == "Deck":
        from engine.deck import Deck
        return Deck
    elif name == "Game":
        from engine.game import Game
        return Game
    elif name == "Player":
        from engine.player import Player
        return Player
    elif name == "Zone":
        from engine.zone import Zone
        return Zone
    elif name == "build_deck":
        from engine.deck_builder_util import build_deck
        return build_deck
    raise AttributeError(f"module 'engine' has no attribute {name!r}")
