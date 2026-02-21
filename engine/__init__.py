"""engine — MTG game engine implementing Magic: The Gathering rules.

Core modules:
    card    — Card model with keyword parsing, effect generation, and Oracle text processing
    game    — Game loop, phases, stack, priority system, combat, and state-based actions
    player  — Player state: life, hand, library, mana pool, and cost payment
    deck    — Deck blueprints and fresh game-deck generation
    zone    — Generic card container (library, hand, graveyard, battlefield, stack)
    card_builder — Converts raw Scryfall JSON into Card instances
"""
