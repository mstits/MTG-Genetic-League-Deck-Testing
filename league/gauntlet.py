"""Gauntlet — Hardcoded boss decks that serve as benchmarks for the league.

Boss decks represent proven competitive archetypes (Red Deck Wins, Black
Control, Blue Tempo) at elevated ELO ratings.  Evolved decks that beat
bosses are demonstrating real strategic viability.
"""

from data.db import get_db_connection
import json
import logging

logger = logging.getLogger(__name__)


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
        Covers all 5 mono colors + colorless for benchmark diversity.
        """
        return [
            {
                "name": "BOSS: Red Deck Wins",
                "cards": {
                    "Mountain": 20,
                    "Lightning Bolt": 4, "Shock": 4,
                    "Goblin Guide": 4, "Monastery Swiftspear": 4,
                    "Lava Spike": 4, "Rift Bolt": 4,
                    "Eidolon of the Great Revel": 4, "Grim Lavamancer": 4,
                    "Searing Blaze": 4, "Skullcrack": 4
                },
                "division": "Mythic", "elo": 1500,
                "archetype": "Aggro", "colors": "R"
            },
            {
                "name": "BOSS: Black Control",
                "cards": {
                    "Swamp": 24,
                    "Doom Blade": 4, "Murder": 4,
                    "Ravenous Chupacabra": 4, "Gray Merchant of Asphodel": 4,
                    "Sign in Blood": 4, "Phyrexian Arena": 4,
                    "Vampire Nighthawk": 4, "Gifted Aetherborn": 4,
                    "Grave Titan": 2, "Damnation": 2
                },
                "division": "Mythic", "elo": 1600,
                "archetype": "Control", "colors": "B"
            },
            {
                "name": "BOSS: Blue Tempo",
                "cards": {
                    "Island": 22,
                    "Delver of Secrets": 4, "Unsummon": 4,
                    "Opt": 4, "Serum Visions": 4,
                    "Tempest Djinn": 4, "Merfolk Trickster": 4,
                    "Brazen Borrower": 4, "Cryptic Command": 4,
                    "Snapcaster Mage": 4, "Vendilion Clique": 2
                },
                "division": "Mythic", "elo": 1550,
                "archetype": "Tempo", "colors": "U"
            },
            {
                "name": "BOSS: White Weenie",
                "cards": {
                    "Plains": 20,
                    "Thalia, Guardian of Thraben": 4,
                    "Esper Sentinel": 4, "Luminarch Aspirant": 4,
                    "Adeline, Resplendent Cathar": 3,
                    "Skyclave Apparition": 4, "Leonin Arbiter": 4,
                    "Path to Exile": 4, "Brave the Elements": 2,
                    "Giver of Runes": 3, "Benalish Marshal": 4,
                    "Isamaru, Hound of Konda": 4
                },
                "division": "Mythic", "elo": 1500,
                "archetype": "Aggro", "colors": "W"
            },
            {
                "name": "BOSS: Green Stompy",
                "cards": {
                    "Forest": 20,
                    "Experiment One": 4, "Pelt Collector": 4,
                    "Barkhide Troll": 4, "Steel Leaf Champion": 4,
                    "Rancor": 4, "Vines of Vastwood": 4,
                    "Strangleroot Geist": 4, "Leatherback Baloth": 4,
                    "Avatar of the Resolute": 4, "Aspect of Hydra": 4
                },
                "division": "Mythic", "elo": 1500,
                "archetype": "Aggro", "colors": "G"
            },
            {
                "name": "BOSS: Colorless Eldrazi",
                "cards": {
                    "Wastes": 5,
                    "Urza's Tower": 4, "Urza's Mine": 4,
                    "Urza's Power Plant": 4, "Eldrazi Temple": 4,
                    "Thought-Knot Seer": 4, "Reality Smasher": 4,
                    "Matter Reshaper": 4, "Endbringer": 2,
                    "Walking Ballista": 3, "Chalice of the Void": 4,
                    "Expedition Map": 4, "Mind Stone": 4,
                    "Dismember": 3, "All Is Dust": 2,
                    "Blast Zone": 2, "Cavern of Souls": 3
                },
                "division": "Mythic", "elo": 1550,
                "archetype": "Midrange", "colors": "C"
            },
            {
                "name": "BOSS: Boros Aggro",
                "cards": {
                    "Mountain": 10, "Plains": 6,
                    "Sacred Foundry": 4, "Inspiring Vantage": 4,
                    "Lightning Bolt": 4, "Chain Lightning": 4,
                    "Goblin Guide": 4, "Monastery Swiftspear": 4,
                    "Thalia, Guardian of Thraben": 3,
                    "Lightning Helix": 4, "Path to Exile": 3,
                    "Boros Charm": 4, "Eidolon of the Great Revel": 4,
                    "Skullcrack": 2
                },
                "division": "Mythic", "elo": 1500,
                "archetype": "Aggro", "colors": "WR"
            },
            {
                "name": "BOSS: Simic Tempo",
                "cards": {
                    "Island": 8, "Forest": 6,
                    "Breeding Pool": 4, "Misty Rainforest": 4,
                    "Botanical Sanctum": 2,
                    "Delver of Secrets": 4, "Tarmogoyf": 4,
                    "Brazen Borrower": 3, "Ice-Fang Coatl": 4,
                    "Counterspell": 4, "Opt": 4,
                    "Vines of Vastwood": 4, "Stubborn Denial": 3,
                    "Collected Company": 4, "Noble Hierarch": 2
                },
                "division": "Mythic", "elo": 1500,
                "archetype": "Tempo", "colors": "UG"
            },
        ]

    def deploy_bosses(self):
        """
        Inserts Boss Decks into the DB if they don't exist.
        """
        logger.info("Deploying Gauntlet Bosses...")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            for boss in self.bosses:
                # Check if exists
                cursor.execute('SELECT id FROM decks WHERE name = %s', (boss['name'],))
                existing = cursor.fetchone()
                
                if not existing:
                    logger.info("Deploying %s...", boss['name'])
                    cursor.execute('''
                        INSERT INTO decks (name, card_list, division, elo, generation, active, archetype, colors)
                        VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
                    ''', (boss['name'], json.dumps(boss['cards']), boss['division'], boss['elo'], 0,
                          boss.get('archetype', 'Unknown'), boss.get('colors', '')))
                else:
                    # Reset Boss Elo/Status if needed?
                    # For now, let them stay and gain history.
                    pass
            conn.commit()

if __name__ == "__main__":
    g = Gauntlet()
    g.deploy_bosses()
