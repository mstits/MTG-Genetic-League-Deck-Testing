"""Deck — Blueprint container for a Magic: The Gathering deck.

Stores card prototypes with quantities (e.g. "4x Lightning Bolt").
When a game starts, `get_game_deck()` creates fresh, independent
copies of every card so mutations during gameplay don't pollute
the original prototypes.
"""

from typing import List, Tuple
from .card import Card
import copy


class Deck:
    """A collection of card prototypes and their quantities.

    The deck is a *blueprint* — it stores one Card instance per unique card
    plus a quantity count.  Call `get_game_deck()` to stamp out fresh copies
    for an actual game.

    Attributes:
        _blueprints: List of (Card prototype, quantity) tuples for the maindeck.
        sideboard:   List of Card instances in the sideboard (15-card max by rules).
    """

    def __init__(self):
        self._blueprints: List[Tuple[Card, int]] = []
        self.sideboard: List[Card] = []

    def add_card(self, card: Card, quantity: int = 1, sideboard: bool = False):
        """Register a card prototype with the given quantity.

        Args:
            card:      The Card prototype to add.
            quantity:  Number of copies (e.g. 4 for a playset).
            sideboard: If True, add to sideboard instead of maindeck.
        """
        if sideboard:
            for _ in range(quantity):
                self.sideboard.append(copy.deepcopy(card))
        else:
            self._blueprints.append((card, quantity))

    @property
    def maindeck(self) -> List[Card]:
        """Flat list of card prototypes (for inspection, NOT for gameplay).

        Returns shared references — do not mutate these during a game.
        Use `get_game_deck()` for gameplay copies.
        """
        result = []
        for card, qty in self._blueprints:
            for _ in range(qty):
                result.append(card)
        return result

    @property
    def total_maindeck(self) -> int:
        """Total number of cards in the maindeck."""
        return sum(qty for _, qty in self._blueprints)

    @property
    def canadian_highlander_points(self) -> int:
        """Total Canadian Highlander points in the maindeck."""
        return sum(card.canadian_highlander_points * qty for card, qty in self._blueprints if hasattr(card, 'canadian_highlander_points'))

    def is_valid_canadian_highlander(self) -> bool:
        """A Canadian Highlander deck cannot exceed 10 points."""
        return self.canadian_highlander_points <= 10

    def get_game_deck(self) -> List[Card]:
        """Create fresh, independent card instances for a new game.

        Each copy is a `deepcopy` with its own unique ID and clean state,
        safe to mutate freely during gameplay without affecting the blueprint.
        """
        deck = []
        for card, qty in self._blueprints:
            for _ in range(qty):
                fresh = copy.deepcopy(card)
                deck.append(fresh)
        return deck
