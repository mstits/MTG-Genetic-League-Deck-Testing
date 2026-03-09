"""Deck Archetype Classifier — heuristic-based archetype detection.

Analyzes a deck's mana curve, card composition, and keyword distribution
to classify it as one of the four major MTG archetypes:

    Aggro      — Low curve, lots of creatures, burn/haste
    Midrange   — Balanced curve, value creatures, some removal
    Control    — High curve, lots of removal/counters/draw
    Combo      — Specific engine pieces (tutor, untap, recursion)

Accessible from the API and shown on leaderboard cards.
"""

import re
from typing import Optional

# Archetype weights for keyword/card-type signals
_AGGRO_SIGNALS = frozenset([
    'haste', 'first strike', 'double strike', 'menace',
    'prowess', 'can\'t block',
])

_CONTROL_SIGNALS = frozenset([
    'counter target', 'destroy target', 'exile target',
    'draw a card', 'draw two', 'draw three', 'scry',
    'board wipe', 'return target',
])

_COMBO_SIGNALS = frozenset([
    'untap', 'search your library', 'return.*from.*graveyard',
    'whenever.*you cast', 'whenever.*enters',
    'infinite', 'copy', 'storm',
])


def classify_deck(card_list: dict, card_pool: dict = None) -> dict:
    """Classify a deck's archetype based on its card composition.
    
    Args:
        card_list: Dict of {card_name: count}
        card_pool: Optional card data pool for Oracle text analysis
        
    Returns:
        Dict with 'archetype', 'confidence', and 'signals' breakdown.
    """
    scores = {'Aggro': 0.0, 'Midrange': 0.0, 'Control': 0.0, 'Combo': 0.0}
    signals = {'Aggro': [], 'Midrange': [], 'Control': [], 'Combo': []}
    
    total_cards = sum(card_list.values())
    if total_cards == 0:
        return {'archetype': 'Unknown', 'confidence': 0.0, 'signals': signals}
    
    # --- Mana curve analysis ---
    cmc_values = []
    creature_count = 0
    noncreature_spell_count = 0
    land_count = 0
    burn_count = 0
    removal_count = 0
    draw_count = 0
    counter_count = 0
    
    for name, count in card_list.items():
        card_data = card_pool.get(name, {}) if card_pool else {}
        type_line = card_data.get('type_line', '')
        oracle = card_data.get('oracle_text', '').lower()
        cmc = card_data.get('cmc', 0)
        
        # NAME-BASED FALLBACK when card pool data is missing
        if not type_line and not oracle:
            lname = name.lower()
            # Detect lands by name
            if lname in ('plains', 'island', 'swamp', 'mountain', 'forest') or 'land' in lname:
                land_count += count
                continue
            # Estimate CMC from well-known card names
            _known_aggro = ('lightning bolt', 'shock', 'lava spike', 'goblin guide',
                           'monastery swiftspear', 'eidolon', 'grim lavamancer', 'skullcrack')
            _known_control = ('counterspell', 'cryptic command', 'wrath of god', 'damnation',
                            'day of judgment', 'supreme verdict', 'snapcaster mage', 'jace',
                            'teferi', 'opt', 'serum visions', 'doom blade', 'murder')
            _known_combo = ('dark ritual', 'lotus', 'time vault', 'storm')
            if any(k in lname for k in _known_aggro):
                scores['Aggro'] += count * 0.8
                signals['Aggro'].append(f"{name} (known aggro)")
            elif any(k in lname for k in _known_control):
                scores['Control'] += count * 0.8
                signals['Control'].append(f"{name} (known control)")
            elif any(k in lname for k in _known_combo):
                scores['Combo'] += count * 0.8
                signals['Combo'].append(f"{name} (known combo)")
            else:
                # Unknown card without data — count as generic creature
                creature_count += count
                cmc_values.append(2)  # Assume average 2-drop
            continue
        
        if 'Land' in type_line:
            land_count += count
            continue
        
        for _ in range(count):
            cmc_values.append(cmc)
        
        if 'Creature' in type_line:
            creature_count += count
        else:
            noncreature_spell_count += count
        
        # Keyword signals
        for kw in _AGGRO_SIGNALS:
            if kw in oracle:
                scores['Aggro'] += count * 0.5
                signals['Aggro'].append(f"{name}: {kw}")
                break
        
        for kw in _CONTROL_SIGNALS:
            if kw in oracle:
                scores['Control'] += count * 0.5
                signals['Control'].append(f"{name}: {kw}")
                break
        
        for pattern in _COMBO_SIGNALS:
            if re.search(pattern, oracle):
                scores['Combo'] += count * 0.7
                signals['Combo'].append(f"{name}: {pattern}")
                break
        
        # Burn detection
        if re.search(r'deals? \d+ damage', oracle):
            burn_count += count
        
        # Removal detection
        if 'destroy target' in oracle or 'exile target' in oracle:
            removal_count += count
        
        # Draw detection
        if 'draw' in oracle and 'Creature' not in type_line:
            draw_count += count
        
        # Counter detection
        if 'counter target spell' in oracle:
            counter_count += count
    
    # --- Curve-based scoring ---
    if cmc_values:
        avg_cmc = sum(cmc_values) / len(cmc_values)
        low_cmc_pct = sum(1 for c in cmc_values if c <= 2) / len(cmc_values)
        high_cmc_pct = sum(1 for c in cmc_values if c >= 4) / len(cmc_values)
        
        # Low curve → Aggro
        if avg_cmc <= 2.0:
            scores['Aggro'] += 3.0
            signals['Aggro'].append(f"avg CMC {avg_cmc:.1f}")
        elif avg_cmc <= 2.5:
            scores['Aggro'] += 1.5
        
        # High curve → Control
        if avg_cmc >= 3.5:
            scores['Control'] += 3.0
            signals['Control'].append(f"avg CMC {avg_cmc:.1f}")
        elif avg_cmc >= 3.0:
            scores['Control'] += 1.5
        
        # Balanced → Midrange
        if 2.5 <= avg_cmc <= 3.5:
            scores['Midrange'] += 2.0
            signals['Midrange'].append(f"balanced curve ({avg_cmc:.1f})")
        
        # Low curve density
        if low_cmc_pct >= 0.7:
            scores['Aggro'] += 2.0
            signals['Aggro'].append(f"{low_cmc_pct*100:.0f}% 1-2 drops")
    
    # --- Composition-based scoring ---
    nonland = total_cards - land_count
    if nonland > 0:
        creature_pct = creature_count / nonland
        
        # Creature-heavy → Aggro or Midrange
        if creature_pct >= 0.6:
            scores['Aggro'] += 2.0
            signals['Aggro'].append(f"{creature_pct*100:.0f}% creatures")
        elif creature_pct >= 0.4:
            scores['Midrange'] += 2.0
            signals['Midrange'].append(f"{creature_pct*100:.0f}% creatures")
        
        # Spell-heavy → Control
        if creature_pct <= 0.25:
            scores['Control'] += 2.5
            signals['Control'].append(f"only {creature_pct*100:.0f}% creatures")
        
        # Burn-heavy → Aggro (burn/red deck wins)
        if burn_count >= 8:
            scores['Aggro'] += 3.0
            signals['Aggro'].append(f"{burn_count} burn spells")
        
        # Removal-heavy → Control
        if removal_count >= 6:
            scores['Control'] += 2.0
            signals['Control'].append(f"{removal_count} removal spells")
        
        # Counter-heavy → Control
        if counter_count >= 4:
            scores['Control'] += 3.0
            signals['Control'].append(f"{counter_count} counterspells")
        
        # Card draw → Control
        if draw_count >= 4:
            scores['Control'] += 1.5
            signals['Control'].append(f"{draw_count} draw spells")
    
    # --- Determine winner ---
    best_archetype = max(scores, key=scores.get)
    best_score = scores[best_archetype]
    total_score = sum(scores.values())
    confidence = best_score / total_score if total_score > 0 else 0.0
    
    # If no strong signal, default to Midrange
    if best_score < 2.0:
        best_archetype = 'Midrange'
        confidence = 0.3
    
    return {
        'archetype': best_archetype,
        'confidence': round(confidence, 2),
        'scores': {k: round(v, 1) for k, v in scores.items()},
        'signals': {k: v[:3] for k, v in signals.items()},  # Top 3 signals per archetype
    }
