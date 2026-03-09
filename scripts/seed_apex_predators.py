"""Apex Predator Seeding script.

Injects historical Pro-Level MTG Goldfish tier decks into the initial gene pool
so the Genetic Algorithm doesn't start from pure randomness but rather stands
on the shoulders of competitive giants.
"""

import os
import json
from data.db import save_deck, init_db
from data.vector_db import init_vector_db, insert_deck_fingerprint

# Mock payload representing historical Top 8 lists
APEX_DECKS = [
    {
        "name": "BOSS: Rakdos Scam (Pro Tour 2024)",
        "colors": "BR",
        "division": "Mythic",
        "cards": {
            "Grief": 4, "Fury": 4, "Not Dead After All": 4,
            "Blood Moon": 2, "Orcish Bowmasters": 4, "Dauthi Voidwalker": 4,
            "Thoughtseize": 4, "Fatal Push": 4, "Lightning Bolt": 4,
            "Blood Crypt": 4, "Swamp": 10, "Mountain": 10, "Terminate": 2
        }
    },
    {
        "name": "BOSS: Dimir Death's Shadow",
        "colors": "UB",
        "division": "Mythic",
        "cards": {
            "Death's Shadow": 4, "Murktide Regent": 2, "Orcish Bowmasters": 4,
            "Thoughtseize": 4, "Fatal Push": 4, "Consider": 4, "Daze": 4,
            "Watery Grave": 4, "Polluted Delta": 4, "Swamp": 12, "Island": 14
        }
    },
    {
        "name": "BOSS: Boros Convoke",
        "colors": "WR",
        "division": "Mythic",
        "cards": {
            "Voldaren Epicure": 4, "Thraben Inspector": 4, "Gleeful Demolition": 4,
            "Knight-Errant of Eos": 4, "Venerated Loxodon": 4, "Imodane's Recruiter": 4,
            "Sacred Foundry": 4, "Inspiring Vantage": 4, "Plains": 14, "Mountain": 14
        }
    }
]

def seed_apex_predators():
    print("Seeding Apex Predators...")
    try:
        init_db()
        init_vector_db()
    except Exception as e:
        print(f"Skipping DB init: {e}")
        
    for deck in APEX_DECKS:
        deck_id = save_deck(
            name=deck["name"],
            card_list=deck["cards"],
            generation=-1, # -1 implies historical anchor
            colors=deck["colors"]
        )
        insert_deck_fingerprint(deck_id, deck["cards"])
        print(f"✅ Injected {deck['name']} -> DB ID: {deck_id}")

if __name__ == "__main__":
    seed_apex_predators()
