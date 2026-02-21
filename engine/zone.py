"""Zone — A generic card container representing MTG game zones.

Zones model the physical areas of a Magic game: library (deck), hand,
battlefield, graveyard, stack, and exile.  Each zone is an ordered list
of Card objects with standard add/remove/shuffle/draw operations.

Per Rule 400.1, zones are the areas where cards can exist during a game.
"""

import random
from typing import List, Optional
from .card import Card


class Zone:
    """A named, ordered collection of Card objects.

    Attributes:
        name:  Human-readable label (e.g. "Battlefield", "Hand").
        cards: The ordered list of cards currently in this zone.
    """

    def __init__(self, name: str):
        self.name = name
        self.cards: List[Card] = []

    def add(self, card: Card):
        """Place a card into this zone (top/end of list)."""
        self.cards.append(card)

    def remove(self, card: Card) -> Optional[Card]:
        """Remove a specific card from this zone, if present.

        Returns the removed card, or None if it wasn't found.
        """
        if card in self.cards:
            self.cards.remove(card)
            return card
        return None

    def shuffle(self):
        """Randomize the order of cards in this zone (Rule 701.20)."""
        random.shuffle(self.cards)

    def draw(self) -> Optional[Card]:
        """Remove and return the top card (index 0).

        Returns None if the zone is empty.  Used primarily for
        the library zone (Rule 121.2).
        """
        if not self.cards:
            return None
        return self.cards.pop(0)  # Draw from top

    def __len__(self):
        return len(self.cards)

    def __getitem__(self, index):
        return self.cards[index]

    def __repr__(self):
        return f"Zone({self.name}, {len(self.cards)} cards)"
