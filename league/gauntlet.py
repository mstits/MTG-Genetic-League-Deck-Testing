"""Gauntlet — Hardcoded boss decks that serve as benchmarks for the league.

Boss decks represent proven competitive archetypes (Red Deck Wins, Black
Control, Blue Tempo) at elevated ELO ratings.  Evolved decks that beat
bosses are demonstrating real strategic viability.
"""

from data.db import save_deck, get_db_connection
import json


class Gauntlet:
    """Manager for the league's boss deck collection.

    Bosses are inserted into the database on startup and serve as fixed
    benchmarks that evolved decks must compete against.
    """
    def __init__(self):
        self.bosses = self._define_bosses()

    def _define_bosses(self):
        """
        Hardcoded list of Boss Decks ("The Gauntlet").
        In a real app, this would load from a file or scraper.
        """
        return [
            {
                "name": "BOSS: Red Deck Wins",
                "cards": {
                    "Mountain": 20,
                    "Lightning Bolt": 4,
                    "Shock": 4,
                    "Goblin Guide": 4,
                    "Monastery Swiftspear": 4,
                    "Lava Spike": 4,
                    "Rift Bolt": 4,
                    "Eidolon of the Great Revel": 4,
                    "Grim Lavamancer": 4,
                    "Searing Blaze": 4,
                    "Skullcrack": 4
                },
                "division": "Mythic",
                "elo": 1500  # Start high
            },
            {
                "name": "BOSS: Black Control",
                "cards": {
                    "Swamp": 24,
                    "Doom Blade": 4,
                    "Murder": 4,
                    "Ravenous Chupacabra": 4,
                    "Gray Merchant of Asphodel": 4,
                    "Sign in Blood": 4,
                    "Phyrexian Arena": 4,
                    "Vampire Nighthawk": 4,
                    "Gifted Aetherborn": 4,
                    "Grave Titan": 2,
                    "Damnation": 2
                },
                "division": "Mythic",
                "elo": 1600
            },
            {
                "name": "BOSS: Blue Tempo",
                "cards": {
                    "Island": 22,
                    "Delver of Secrets": 4,
                    "Unsummon": 4,
                    "Opt": 4,
                    "Serum Visions": 4,
                    "Tempest Djinn": 4,
                    "Merfolk Trickster": 4,
                    "Brazen Borrower": 4,
                    "Cryptic Command": 4, # Counterspell logic stub
                    "Snapcaster Mage": 4,
                    "Vendilion Clique": 2
                },
                "division": "Mythic",
                "elo": 1550
            }
        ]

    def deploy_bosses(self):
        """
        Inserts Boss Decks into the DB if they don't exist.
        """
        print("Deploying Gauntlet Bosses...")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            for boss in self.bosses:
                # Check if exists
                cursor.execute('SELECT id FROM decks WHERE name = ?', (boss['name'],))
                existing = cursor.fetchone()
                
                if not existing:
                    print(f"Deploying {boss['name']}...")
                    cursor.execute('''
                        INSERT INTO decks (name, card_list, division, elo, generation, active)
                        VALUES (?, ?, ?, ?, ?, 1)
                    ''', (boss['name'], json.dumps(boss['cards']), boss['division'], boss['elo'], 0))
                else:
                    # Reset Boss Elo/Status if needed?
                    # For now, let them stay and gain history.
                    pass
            conn.commit()

if __name__ == "__main__":
    g = Gauntlet()
    g.deploy_bosses()
