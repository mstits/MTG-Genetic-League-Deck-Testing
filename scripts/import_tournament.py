"""Import Tournament — Hardcoded tournament-winning boss archetypes.

Contains 22 competitive Modern decklists (Burn, Murktide, Boros Energy,
Rakdos Scam, Tron, etc.) used as benchmarks.  These are inserted into
the league database as BOSS decks that evolved decks must compete against.
"""

import json
import os
import re
import requests
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
from data.db import save_deck, get_db_connection

# Well-known competitive Modern archetypes with representative card cores
BOSS_ARCHETYPES = {
    "BOSS:Burn-RW": {
        "colors": "RW",
        "cards": {
            "Lightning Bolt": 4, "Goblin Guide": 4, "Monastery Swiftspear": 4,
            "Eidolon of the Great Revel": 4, "Lava Spike": 4, 
            "Rift Bolt": 4, "Searing Blaze": 4,
            "Skullcrack": 2, "Light Up the Stage": 4, "Shard Volley": 2,
            "Mountain": 14, "Inspiring Vantage": 4, "Sacred Foundry": 4,
            "Sunbaked Canyon": 2,
        }
    },
    "BOSS:Murktide-UR": {
        "colors": "UR",
        "cards": {
            "Murktide Regent": 4, "Ragavan, Nimble Pilferer": 4,
            "Dragon's Rage Channeler": 4, "Counterspell": 4,
            "Lightning Bolt": 4, "Unholy Heat": 4,
            "Consider": 4, "Expressive Iteration": 4,
            "Spell Pierce": 2, "Mishra's Bauble": 2,
            "Steam Vents": 4, "Spirebluff Canal": 4,
            "Island": 6, "Mountain": 4, "Fiery Islet": 2,
        }
    },
    "BOSS:Jund-BRG": {
        "colors": "BRG",
        "cards": {
            "Tarmogoyf": 4, "Bloodbraid Elf": 4,
            "Dark Confidant": 4, "Lightning Bolt": 4,
            "Fatal Push": 4, "Inquisition of Kozilek": 4,
            "Thoughtseize": 2, "Liliana of the Veil": 3,
            "Assassin's Trophy": 2, "Kolaghan's Command": 2,
            "Overgrown Tomb": 3, "Blood Crypt": 2, "Stomping Ground": 2,
            "Swamp": 3, "Mountain": 2, "Forest": 3,
            "Blackcleave Cliffs": 4, "Verdant Catacombs": 4,
        }
    },
    "BOSS:Control-WU": {
        "colors": "WU",
        "cards": {
            "Teferi, Hero of Dominaria": 3, "Snapcaster Mage": 4,
            "Supreme Verdict": 3, "Prismatic Ending": 3,
            "Counterspell": 4, "Archmage's Charm": 3,
            "Shark Typhoon": 2, "March of Otherworldly Light": 2,
            "Spreading Seas": 2, "Opt": 4,
            "Hallowed Fountain": 4, "Celestial Colonnade": 4,
            "Glacial Fortress": 4, "Island": 6, "Plains": 4,
            "Mystic Gate": 2, "Irrigated Farmland": 2,
        }
    },
    "BOSS:Hammertime-W": {
        "colors": "W",
        "cards": {
            "Colossus Hammer": 4, "Puresteel Paladin": 4,
            "Stoneforge Mystic": 4, "Sigarda's Aid": 4,
            "Ornithopter": 4, "Memnite": 4,
            "Esper Sentinel": 4, "Giver of Runes": 3,
            "Springleaf Drum": 3, "Kaldra Compleat": 1,
            "Urza's Saga": 4, "Inkmoth Nexus": 4,
            "Plains": 9, "Silent Clearing": 2,
            "Seachrome Coast": 4, "Mana Confluence": 2,
        }
    },
    "BOSS:MonoGreen-Tron-G": {
        "colors": "G",
        "cards": {
            "Karn, the Great Creator": 4, "Wurmcoil Engine": 3,
            "Ulamog, the Ceaseless Hunger": 2, "Walking Ballista": 2,
            "Sylvan Scrying": 4, "Ancient Stirrings": 4,
            "Chromatic Star": 4, "Chromatic Sphere": 4,
            "Expedition Map": 4, "Oblivion Stone": 2,
            "All Is Dust": 2, "Forest": 5,
            "Urza's Tower": 4, "Urza's Mine": 4,
            "Urza's Power Plant": 4, "Sanctum of Ugin": 2,
            "Blast Zone": 2,
        }
    },
    "BOSS:DeathsShadow-UBR": {
        "colors": "UBR",
        "cards": {
            "Death's Shadow": 4, "Ragavan, Nimble Pilferer": 4,
            "Dragon's Rage Channeler": 4, "Thoughtseize": 4,
            "Fatal Push": 4, "Lightning Bolt": 4,
            "Unholy Heat": 2, "Stubborn Denial": 2,
            "Consider": 4, "Mishra's Bauble": 4,
            "Dress Down": 2,
            "Blood Crypt": 2, "Steam Vents": 2, "Watery Grave": 2,
            "Polluted Delta": 4, "Bloodstained Mire": 4,
            "Scalding Tarn": 2, "Swamp": 2, "Island": 2, "Mountain": 2,
        }
    },
    "BOSS:Yawgmoth-BG": {
        "colors": "BG",
        "cards": {
            "Yawgmoth, Thran Physician": 4, "Young Wolf": 4,
            "Strangleroot Geist": 4, "Geralf's Messenger": 4,
            "Blood Artist": 4, "Zulaport Cutthroat": 2,
            "Chord of Calling": 4, "Collected Company": 4,
            "Birds of Paradise": 4, "Elves of Deep Shadow": 2,
            "Overgrown Tomb": 4, "Blooming Marsh": 4,
            "Forest": 6, "Swamp": 4, "Llanowar Wastes": 2,
            "Twilight Mire": 2, "Nurturing Peatland": 2,
        }
    },
    "BOSS:LivingEnd-UBR": {
        "colors": "UBR",
        "cards": {
            "Shardless Agent": 4, "Violent Outburst": 4,
            "Architects of Will": 4, "Street Wraith": 4,
            "Striped Riverwinder": 4, "Horror of the Broken Lands": 4,
            "Living End": 4, "Grief": 4,
            "Force of Negation": 4,
            "Blood Crypt": 2, "Steam Vents": 2, "Watery Grave": 2,
            "Blackcleave Cliffs": 4, "Spirebluff Canal": 4,
            "Island": 2, "Swamp": 2, "Mountain": 2,
            "Gemstone Mine": 4,
        }
    },
    "BOSS:AmuletTitan-G": {
        "colors": "G",
        "cards": {
            "Primeval Titan": 4, "Amulet of Vigor": 4,
            "Dryad of the Ilysian Grove": 4, "Arboreal Grazer": 4,
            "Summoner's Pact": 4, "Explore": 4,
            "Expedition Map": 2,
            "Simic Growth Chamber": 4, "Gruul Turf": 2,
            "Boros Garrison": 2, "Selesnya Sanctuary": 2,
            "Forest": 4, "Tolaria West": 2, "Bojuka Bog": 2,
            "Radiant Fountain": 2, "Cavern of Souls": 2,
            "Vesuva": 2, "Castle Garenbrig": 2,
            "Valakut, the Molten Pinnacle": 4, "Slayers' Stronghold": 2,
        }
    },
    "BOSS:Merfolk-U": {
        "colors": "U",
        "cards": {
            "Lord of Atlantis": 4, "Master of the Pearl Trident": 4,
            "Silvergill Adept": 4, "Merfolk Trickster": 4,
            "Harbinger of the Tides": 4, "Master of Waves": 3,
            "Spreading Seas": 4, "Aether Vial": 4,
            "Force of Negation": 3, "Dismember": 2,
            "Island": 14, "Mutavault": 4, "Cavern of Souls": 4,
            "Minamo, School at Water's Edge": 1,
        }
    },
    "BOSS:Infect-UG": {
        "colors": "UG",
        "cards": {
            "Glistener Elf": 4, "Blighted Agent": 4,
            "Noble Hierarch": 4, "Vines of Vastwood": 4,
            "Blossoming Defense": 4, "Might of Old Krosa": 4,
            "Mutagenic Growth": 4, "Become Immense": 2,
            "Spell Pierce": 2, "Distortion Strike": 3,
            "Pendelhaven": 1, "Inkmoth Nexus": 4,
            "Breeding Pool": 4, "Forest": 6,
            "Windswept Heath": 4, "Misty Rainforest": 4,
            "Island": 1,
        }
    },
    "BOSS:4cOmnath-WURG": {
        "colors": "WURG",
        "cards": {
            "Omnath, Locus of Creation": 4, "Solitude": 4,
            "Fury": 4, "Teferi, Time Raveler": 3,
            "Wrenn and Six": 3, "Prismatic Ending": 3,
            "Lightning Bolt": 4, "Counterspell": 2,
            "Abundant Growth": 2,
            "Raugrin Triome": 2, "Ketria Triome": 2,
            "Steam Vents": 2, "Breeding Pool": 2,
            "Stomping Ground": 1, "Sacred Foundry": 1,
            "Windswept Heath": 4, "Misty Rainforest": 4,
            "Flooded Strand": 2, "Forest": 2, "Island": 1,
            "Mountain": 1, "Plains": 1,
        }
    },
    "BOSS:Dredge-BRG": {
        "colors": "BRG",
        "cards": {
            "Stinkweed Imp": 4, "Golgari Thug": 4,
            "Life from the Loam": 4, "Narcomoeba": 4,
            "Prized Amalgam": 4, "Creeping Chill": 4,
            "Cathartic Reunion": 4, "Thrilling Discovery": 4,
            "Conflagrate": 2, "Ox of Agonas": 1,
            "Bloodghast": 4,
            "Blood Crypt": 2, "Stomping Ground": 2,
            "Copperline Gorge": 4, "Blackcleave Cliffs": 4,
            "Mountain": 4, "Forgotten Cave": 2,
            "Arid Mesa": 2,
        }
    },
    # ─── 2024-2025 Metagame Titans ─────────────────────────────────
    "BOSS:BorosEnergy-RW": {
        "colors": "RW",
        "cards": {
            "Amped Raptor": 4, "Galvanic Discharge": 4,
            "Guide of Souls": 4, "Ocelot Pride": 4,
            "Phlage, Titan of Fire's Fury": 3, "Ajani, Nacatl Pariah": 4,
            "Static Prison": 3, "Unstable Amulet": 2,
            "Luminarch Aspirant": 3, "Monastery Swiftspear": 4,
            "Sacred Foundry": 4, "Inspiring Vantage": 4,
            "Arid Mesa": 4, "Mountain": 6, "Plains": 7,
        }
    },
    "BOSS:GoryosVengeance-UBR": {
        "colors": "UBR",
        "cards": {
            "Goryo's Vengeance": 4, "Griselbrand": 4,
            "Atraxa, Grand Unifier": 3, "Grief": 4,
            "Ephemerate": 4, "Thoughtseize": 4,
            "Faithful Mending": 4, "Persist": 2,
            "Unmarked Grave": 4, "Consider": 3,
            "Blood Crypt": 2, "Watery Grave": 2,
            "Godless Shrine": 2, "Marsh Flats": 4,
            "Swamp": 5, "Island": 3, "Plains": 2,
            "Silent Clearing": 3,
        }
    },
    "BOSS:DomainZoo-WUBRG": {
        "colors": "WUBRG",
        "cards": {
            "Territorial Kavu": 4, "Scion of Draco": 4,
            "Leyline Binding": 4, "General Ferrous Rokiric": 4,
            "Nishoba Brawler": 4, "Tribal Flames": 4,
            "Lightning Bolt": 4, "Prismatic Ending": 3,
            "Stubborn Denial": 2,
            "Triome Indatha": 1, "Triome Ketria": 1,
            "Triome Raugrin": 1, "Triome Savai": 1,
            "Triome Zagoth": 1, "Sacred Foundry": 1,
            "Steam Vents": 1, "Breeding Pool": 1,
            "Stomping Ground": 1, "Blood Crypt": 1,
            "Windswept Heath": 4, "Misty Rainforest": 4,
            "Arid Mesa": 2, "Forest": 2, "Plains": 2,
        }
    },
    "BOSS:MonoBlackCoffers-B": {
        "colors": "B",
        "cards": {
            "Cabal Coffers": 4, "Urborg, Tomb of Yawgmoth": 4,
            "Fatal Push": 4, "Thoughtseize": 4,
            "Invoke Despair": 4, "Sheoldred, the Apocalypse": 4,
            "Dauthi Voidwalker": 4, "Tourach, Dread Cantor": 3,
            "Liliana of the Veil": 3, "March of Wretched Sorrow": 2,
            "Phyrexian Arena": 2, "Swamp": 18,
            "Castle Locthwain": 2, "Mutavault": 2,
        }
    },
    "BOSS:HardenedScales-G": {
        "colors": "G",
        "cards": {
            "Hardened Scales": 4, "Arcbound Ravager": 4,
            "Walking Ballista": 4, "Hangarback Walker": 4,
            "Arcbound Worker": 4, "Steel Overseer": 4,
            "Ozolith, the Shattered Spire": 4, "Zabaz, the Glimmerwasp": 4,
            "Ancient Stirrings": 4, "Welding Jar": 2,
            "Darksteel Citadel": 4, "Inkmoth Nexus": 4,
            "Forest": 6, "Blinkmoth Nexus": 4, "Llanowar Reborn": 2,
        }
    },
    "BOSS:Creativity-URW": {
        "colors": "URW",
        "cards": {
            "Indomitable Creativity": 4, "Archon of Cruelty": 2,
            "Serra's Emissary": 1, "Fable of the Mirror-Breaker": 4,
            "Prismari Command": 4, "Hard Evidence": 4,
            "Transmogrify": 2, "Lightning Bolt": 4,
            "Fire // Ice": 2, "Teferi, Time Raveler": 3,
            "Shark Typhoon": 2, "Prismatic Ending": 2,
            "Dwarven Mine": 4, "Steam Vents": 4,
            "Sacred Foundry": 2, "Raugrin Triome": 2,
            "Scalding Tarn": 4, "Flooded Strand": 2,
            "Island": 3, "Mountain": 3, "Plains": 1,
        }
    },
    "BOSS:JeskaiControl-WUR": {
        "colors": "WUR",
        "cards": {
            "Teferi, Hero of Dominaria": 3, "Teferi, Time Raveler": 3,
            "Narset, Parter of Veils": 1, "Snapcaster Mage": 4,
            "Solitude": 4, "Lightning Bolt": 4,
            "Counterspell": 4, "Supreme Verdict": 3,
            "Prismatic Ending": 3, "Shark Typhoon": 2,
            "Archmage's Charm": 2,
            "Hallowed Fountain": 4, "Steam Vents": 4,
            "Sacred Foundry": 1, "Raugrin Triome": 2,
            "Scalding Tarn": 4, "Flooded Strand": 2,
            "Island": 4, "Plains": 3, "Mountain": 2,
        }
    },
    "BOSS:RakdosScam-BR": {
        "colors": "BR",
        "cards": {
            "Grief": 4, "Fury": 4, "Seasoned Pyromancer": 4,
            "Fable of the Mirror-Breaker": 4, "Ragavan, Nimble Pilferer": 4,
            "Thoughtseize": 4, "Fatal Push": 4,
            "Lightning Bolt": 4, "Undying Malice": 4,
            "Feign Death": 2,
            "Blood Crypt": 4, "Blackcleave Cliffs": 4,
            "Bloodstained Mire": 4, "Swamp": 6,
            "Mountain": 4, "Den of the Bugbear": 2,
        }
    },
}

def import_boss_decks():
    """Import all boss archetypes into the database."""
    # Load legal cards to validate
    base_dir = os.path.dirname(os.path.abspath(__file__))
    legal_path = os.path.join(os.path.dirname(base_dir), 'data', 'legal_cards.json')
    if not os.path.exists(legal_path):
        legal_path = os.path.join(base_dir, '..', 'data', 'legal_cards.json')
    
    legal_names = set()
    if os.path.exists(legal_path):
        with open(legal_path, 'r') as f:
            legal_cards = json.load(f)
            legal_names = {c['name'] for c in legal_cards}
    
    imported = 0
    for deck_name, arch in BOSS_ARCHETYPES.items():
        cards = arch['cards']
        colors = arch['colors']
        
        # Validate cards exist in legal pool (skip missing ones)
        valid_cards = {}
        missing = []
        for name, count in cards.items():
            if legal_names and name in legal_names:
                valid_cards[name] = count
            elif not legal_names:
                valid_cards[name] = count
            else:
                missing.append(name)
        
        total = sum(valid_cards.values())
        
        if total < 40:
            print(f"  ⚠️  {deck_name}: Only {total} valid cards, skipping")
            continue
        
        # Pad with basics if needed
        while total < 60:
            land_colors = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}
            for c in colors:
                if total >= 60:
                    break
                land = land_colors.get(c, 'Mountain')
                valid_cards[land] = valid_cards.get(land, 0) + 1
                total += 1
        
        try:
            deck_id = save_deck(deck_name, valid_cards, generation=0, parent_ids=[], 
                     colors=colors)
            # Set boss decks to Gold division
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE decks SET division = ? WHERE id = ?', ('Gold', deck_id))
                conn.commit()
            print(f"  ✅ Imported {deck_name} ({total} cards, {len(missing)} missing)")
            imported += 1
        except Exception as e:
            print(f"  ❌ Failed {deck_name}: {e}")
    
    return imported

def fetch_scryfall_decklists():
    """Fetch popular card data from Scryfall to identify meta staples."""
    print("📊 Fetching card popularity data from Scryfall...")
    # Use Scryfall search for commonly played Modern cards
    url = "https://api.scryfall.com/cards/search"
    params = {"q": "f:modern game:paper", "order": "edhrec", "dir": "desc"}
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            top_cards = [c['name'] for c in data.get('data', [])[:50]]
            print(f"  Top played Modern cards: {', '.join(top_cards[:10])}...")
            return top_cards
    except Exception as e:
        print(f"  Scryfall fetch failed: {e}")
    return []

if __name__ == "__main__":
    print("🏆 Importing Tournament Boss Decks...")
    imported = import_boss_decks()
    print(f"\n✅ Imported {imported} boss decks")
    
    # Optional: fetch meta data
    top = fetch_scryfall_decklists()
