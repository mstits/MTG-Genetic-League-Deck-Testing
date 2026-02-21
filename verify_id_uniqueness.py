"""Verify ID uniqueness — Ensures deepcopy gives each card a unique UUID.

Confirms that `Deck.get_game_deck()` produces cards with distinct IDs
and independent state (tapping one card doesn't tap its copies).
"""

from engine.card import Card
from engine.deck import Deck
from engine.game import Game
from engine.player import Player

def run_test():
    print("Testing Card ID Uniqueness...")

    # 1. Create a card
    u_card = Card(name="Island", cost="", type_line="Basic Land — Island")
    print(f"Prototype ID: {u_card.id}")

    # 2. Add to deck
    deck = Deck()
    deck.add_card(u_card, 4) # 4 Islands

    # 3. Get game deck
    game_cards = deck.get_game_deck()
    print(f"Game Deck Size: {len(game_cards)}")

    # 4. Check IDs
    ids = [c.id for c in game_cards]
    print(f"IDs: {ids}")

    if len(set(ids)) != len(ids):
        print("FAIL: Duplicate IDs found!")
        return

    # 5. Check State Independence
    c1 = game_cards[0]
    c2 = game_cards[1]
    
    print(f"Modifying c1 (id {c1.id})...")
    c1.tapped = True
    
    if c2.tapped:
        print(f"FAIL: c2 (id {c2.id}) became tapped when c1 was tapped! Shared state detected.")
        return
    else:
        print("PASS: c2 remained untapped.")

    print("Success: IDs are unique and state is independent.")

if __name__ == "__main__":
    run_test()
