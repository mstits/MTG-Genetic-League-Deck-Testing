"""SaltScore — Commander Bracket classification (2026 rules).

The 2026 Commander Brackets system categorizes cards and decks by power level:

    Bracket 1: Casual — basic effects, no tutors, no combos
    Bracket 2: Focused — strong synergies, popular staples
    Bracket 3: Optimized — efficient tutors, strong mana rocks, stax pieces
    Bracket 4: Competitive (cEDH) — free counters, fast mana, combo kills

Usage:
    from engine.salt_score import calculate_salt_score, get_bracket_warning

    result = calculate_salt_score(decklist)
    # {'bracket': 4, 'salt_score': 87.5, 'flagged_cards': [...]}

    warning = get_bracket_warning(decklist)
    # "⚠️ Bracket 4 (cEDH) — 5 competitive staples detected"
"""


# ─── Bracket Classification Data ──────────────────────────────────────────────

# Bracket 5: Unrestricted / Rule 0 / Banned in Commander
BRACKET_5_CARDS = frozenset([
    "Black Lotus", "Ancestral Recall", "Mox Sapphire", "Mox Ruby", 
    "Mox Pearl", "Mox Emerald", "Mox Jet", "Time Walk", "Time Vault",
    "Tinker", "Gifts Ungiven", "Karakas", "Tolarian Academy", "Channel", 
    "Fastbond", "Flash", "Emrakul, the Aeons Torn", "Griselbrand", 
    "Primeval Titan", "Sylvan Primordial", "Prophet of Kruphix", 
    "Leovold, Emissary of Trest", "Lutri, the Spellchaser", "Upheaval", 
    "Biorhythm", "Coalition Victory", "Sway of the Stars", "Panoptic Mirror", 
    "Paradox Engine", "Iona, Shield of Emeria", "Hullbreacher", 
    "Golos, Tireless Pilgrim", "Nadu, Winged Wisdom", "Dockside Extortionist", 
    "Mana Crypt", "Jeweled Lotus"
])

# Bracket 4: cEDH staples — free counters, fast mana, instant-win combos
BRACKET_4_CARDS = frozenset([
    # Fast mana
    "Mana Crypt", "Mana Vault", "Chrome Mox", "Mox Diamond",
    "Mox Opal", "Lotus Petal", "Jeweled Lotus", "Lion's Eye Diamond",
    "Grim Monolith", "Mana Drain",
    # Free counters
    "Force of Will", "Fierce Guardianship", "Deflecting Swat",
    "Force of Negation", "Pact of Negation", "Mental Misstep",
    # Combo pieces
    "Thassa's Oracle", "Demonic Consultation", "Tainted Pact",
    "Ad Nauseam", "Doomsday", "Underworld Breach",
    "Isochron Scepter", "Dramatic Reversal",
    # Tutors (unconditional)
    "Vampiric Tutor", "Imperial Seal", "Mystical Tutor",
    "Enlightened Tutor", "Worldly Tutor", "Gamble",
    # Stax
    "Winter Orb", "Static Orb", "Stasis",
    "Trinisphere", "Sphere of Resistance",
    "Drannith Magistrate", "Opposition Agent",
    # Value engines
    "Dockside Extortionist", "Rhystic Study", "Smothering Tithe",
    "Necropotence", "Sylvan Library",
    # Lands
    "Gaea's Cradle", "Serra's Sanctum", "Mishra's Workshop",
    "The Tabernacle at Pendrell Vale",
])

# Bracket 3: Optimized — strong but not cEDH
BRACKET_3_CARDS = frozenset([
    # Good tutors
    "Demonic Tutor", "Diabolic Intent", "Finale of Devastation",
    "Green Sun's Zenith", "Chord of Calling", "Natural Order",
    "Birthing Pod", "Yisan, the Wanderer Bard",
    # Strong mana
    "Sol Ring", "Arcane Signet", "Fellwar Stone",
    "Ancient Tomb", "Urborg, Tomb of Yawgmoth", "Cabal Coffers",
    # Removal/protection
    "Cyclonic Rift", "Toxic Deluge", "Farewell",
    "Teferi's Protection", "Heroic Intervention",
    # Engines
    "Sensei's Divining Top", "Scroll Rack",
    "Doubling Season", "Parallel Lives",
    "Aura Shards", "Grave Pact",
    # Recursion
    "Eternal Witness", "Sun Titan", "Reanimate",
    "Animate Dead", "Dance of the Dead",
    # Finishers
    "Craterhoof Behemoth", "Avenger of Zendikar",
    "Expropriate", "Omniscience",
])

# Bracket 2: Focused — popular staples
BRACKET_2_CARDS = frozenset([
    "Swords to Plowshares", "Path to Exile", "Beast Within",
    "Chaos Warp", "Generous Gift", "Counterspell",
    "Lightning Greaves", "Swiftfoot Boots",
    "Skullclamp", "Solemn Simulacrum",
    "Cultivate", "Kodama's Reach", "Rampant Growth",
    "Command Tower", "Exotic Orchard",
    "Propaganda", "Ghostly Prison",
    "Phyrexian Arena", "Guardian Project",
    "Return of the Wildspeaker", "Rishkar's Expertise",
])

# Game Changers (Rule 0 / Community Watchlist — restricts deck to Bracket 3+)
GAME_CHANGERS = frozenset([
    "Farewell", "Biorhythm", "Expropriate", "Cyclonic Rift",
    "Craterhoof Behemoth", "Omniscience", "Insurrection", "Tooth and Nail"
])

# Weights for salt scoring
BRACKET_WEIGHTS = {5: 100, 4: 20, 3: 8, 2: 3, 1: 0}


def calculate_salt_score(decklist: dict[str, int]) -> dict:
    """Calculate the salt score and bracket classification for a Commander deck.

    Args:
        decklist: Dict of card_name → count.

    Returns:
        Dict with:
            bracket: 1-4 (highest matching bracket)
            salt_score: 0-100+ (total salt value)
            flagged_cards: List of {'card': name, 'bracket': N, 'count': N}
            breakdown: Dict of bracket → count of cards in that bracket
    """
    flagged = []
    breakdown = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    salt = 0.0

    for card_name, count in decklist.items():
        if card_name in BRACKET_5_CARDS:
            bracket = 5
        elif card_name in BRACKET_4_CARDS:
            bracket = 4
        elif card_name in BRACKET_3_CARDS or card_name in GAME_CHANGERS:
            bracket = 3
        elif card_name in BRACKET_2_CARDS:
            bracket = 2
        else:
            bracket = 1
            breakdown[1] += count
            continue

        breakdown[bracket] += count
        salt += BRACKET_WEIGHTS[bracket] * count
        flagged.append({
            "card": card_name,
            "bracket": bracket,
            "count": count,
            "is_game_changer": card_name in GAME_CHANGERS
        })

    # Determine overall deck bracket (highest card bracket present)
    if breakdown[5] > 0:
        overall = 5
    elif breakdown[4] > 0:
        overall = 4
    elif breakdown[3] > 0:
        overall = 3
    elif breakdown[2] > 0:
        overall = 2
    else:
        overall = 1

    # Sort flagged by bracket descending, then name
    flagged.sort(key=lambda x: (-x["bracket"], x["card"]))

    return {
        "bracket": overall,
        "salt_score": round(salt, 1),
        "flagged_cards": flagged,
        "breakdown": breakdown,
    }


def get_bracket_warning(decklist: dict[str, int]) -> str | None:
    """Return a warning string if the deck is Bracket 3 or 4.

    Returns None for Bracket 1-2 decks.
    """
    result = calculate_salt_score(decklist)
    bracket = result["bracket"]

    if bracket >= 4:
        b4_count = result["breakdown"][4]
        return (
            f"⚠️ Bracket 4 (cEDH) — {b4_count} competitive staple(s) detected. "
            f"Salt score: {result['salt_score']}"
        )
    elif bracket == 3:
        b3_count = result["breakdown"][3]
        return (
            f"⚡ Bracket 3 (Optimized) — {b3_count} optimized card(s) detected. "
            f"Salt score: {result['salt_score']}"
        )

    return None
