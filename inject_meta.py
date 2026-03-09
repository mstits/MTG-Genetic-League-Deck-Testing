import json
import os

data_path = 'data/legal_cards.json'
with open(data_path, 'r') as f:
    cards = json.load(f)

# Find if they exist
names = set(c['name'] for c in cards)

new_cards = [
    {
        "name": "Blood Moon",
        "mana_cost": "{2}{R}",
        "cmc": 3,
        "type_line": "Enchantment",
        "oracle_text": "Nonbasic lands are Mountains.",
        "colors": ["R"],
        "color_identity": ["R"],
        "legalities": {"modern": "legal"}
    },
    {
        "name": "Rest in Peace",
        "mana_cost": "{1}{W}",
        "cmc": 2,
        "type_line": "Enchantment",
        "oracle_text": "When Rest in Peace enters the battlefield, exile all graveyards.\nIf a card or token would be put into a graveyard from anywhere, exile it instead.",
        "colors": ["W"],
        "color_identity": ["W"],
        "legalities": {"modern": "legal"}
    },
    {
        "name": "Bloodstained Mire",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Land",
        "oracle_text": "{T}, Pay 1 life, Sacrifice Bloodstained Mire: Search your library for a Swamp or Mountain card, put it onto the battlefield, then shuffle.",
        "colors": [],
        "color_identity": [],
        "produced_mana": [],
        "legalities": {"modern": "legal"}
    },
    {
        "name": "Blood Crypt",
        "mana_cost": "",
        "cmc": 0,
        "type_line": "Land — Swamp Mountain",
        "oracle_text": "({T}: Add {B} or {R}.)\nAs Blood Crypt enters the battlefield, you may pay 2 life. If you don't, it enters the battlefield tapped.",
        "colors": [],
        "color_identity": ["B", "R"],
        "produced_mana": ["B", "R"],
        "legalities": {"modern": "legal"}
    }
]

added = []
for nc in new_cards:
    if nc['name'] not in names:
        cards.append(nc)
        added.append(nc['name'])

with open(data_path, 'w') as f:
    json.dump(cards, f, indent=2)

import shutil
shutil.copy('data/legal_cards.json', 'data/processed_cards.json')

print(f"Added {len(added)} cards: {added}")
