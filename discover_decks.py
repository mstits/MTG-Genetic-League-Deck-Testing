#!/usr/bin/env python3
"""
MTG Deck Discovery Pipeline — World-Class Edition

Evolves decks via genetic optimization, tests them against 22 real tournament-winning
boss decks (Burn, Murktide, Boros Energy, Rakdos Scam, etc.), then runs a full
cross-archetype tournament. Reports rankings, novel strategies, and metagame insights.

Usage:
    python3 discover_decks.py                              # All 5 mono colors
    python3 discover_decks.py --colors R                   # Red only (fast test)
    python3 discover_decks.py --colors R G --gen 5          # Red + Green, 5 gens
    python3 discover_decks.py --all --gen 10 --pop 25       # Full 15-color, 10 gens
    python3 discover_decks.py --boss-only                   # Boss-vs-boss tournament only
"""

import json
import sys
import os
import time
import argparse
import random
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.card import Card
from engine.deck import Deck
from engine.game import Game
from engine.player import Player
from simulation.runner import SimulationRunner
from agents.heuristic_agent import HeuristicAgent
from optimizer.genetic import GeneticOptimizer
from scripts.import_tournament import BOSS_ARCHETYPES


# ─── Color names ──────────────────────────────────────────────

MONO_COLORS = ['W', 'U', 'B', 'R', 'G']
TWO_COLOR_PAIRS = ['WU', 'WB', 'WR', 'WG', 'UB', 'UR', 'UG', 'BR', 'BG', 'RG']
COLOR_NAMES = {
    'W': 'White', 'U': 'Blue', 'B': 'Black', 'R': 'Red', 'G': 'Green',
    'WU': 'Azorius', 'WB': 'Orzhov', 'WR': 'Boros', 'WG': 'Selesnya',
    'UB': 'Dimir', 'UR': 'Izzet', 'UG': 'Simic', 'BR': 'Rakdos',
    'BG': 'Golgari', 'RG': 'Gruul'
}


def load_card_pool():
    """Load card pool from legal_cards.json."""
    pool_path = os.path.join(os.path.dirname(__file__), 'data', 'legal_cards.json')
    with open(pool_path, 'r') as f:
        cards = json.load(f)
    print(f"📚 Loaded {len(cards):,} cards from pool")
    return cards


def build_boss_deck(arch_name, arch_data, card_pool_dict):
    """Build a Deck object from boss archetype data."""
    deck = Deck()
    cards = arch_data['cards']
    colors = arch_data['colors']
    
    for name, count in cards.items():
        card_data = card_pool_dict.get(name)
        if card_data:
            card = Card(
                name=card_data['name'],
                cost=card_data.get('mana_cost', ''),
                type_line=card_data.get('type_line', ''),
                oracle_text=card_data.get('oracle_text', ''),
                base_power=int(card_data['power']) if card_data.get('power', '').isdigit() else None,
                base_toughness=int(card_data['toughness']) if card_data.get('toughness', '').isdigit() else None
            )
            deck.add_card(card, count)
    
    # Pad with basic lands if needed
    land_map = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}
    total = len(deck.maindeck)
    while total < 60:
        for c in colors:
            if total >= 60:
                break
            land_name = land_map.get(c, 'Mountain')
            land_data = card_pool_dict.get(land_name)
            if land_data:
                card = Card(
                    name=land_data['name'],
                    cost='',
                    type_line=land_data.get('type_line', 'Basic Land'),
                    oracle_text=land_data.get('oracle_text', ''),
                )
                deck.add_card(card, 1)
            total += 1
    return deck


def classify_archetype(deck):
    """Classify a deck's archetype based on card composition."""
    cards = deck.maindeck
    creatures = [c for c in cards if c.is_creature]
    spells = [c for c in cards if not c.is_creature and not c.is_land]
    
    avg_cmc = 0
    cmc_count = 0
    for c in cards:
        if not c.is_land and c.cost:
            cmc = sum(1 for ch in c.cost if ch == '{')
            avg_cmc += cmc
            cmc_count += 1
    avg_cmc = avg_cmc / max(cmc_count, 1)
    
    has_burn = any(getattr(c, 'is_burn', False) for c in spells)
    has_draw = any(getattr(c, 'is_draw', False) for c in spells)
    has_removal = any(getattr(c, 'is_removal', False) for c in spells)
    has_counter = any(getattr(c, 'is_counter', False) for c in spells)
    has_mill = any(getattr(c, 'is_mill', False) for c in spells)
    has_vehicles = any(getattr(c, 'is_vehicle', False) for c in cards)
    has_prowess = any(getattr(c, 'has_prowess', False) for c in creatures)
    has_wipe = any(getattr(c, 'is_board_wipe', False) for c in spells)
    
    if has_mill and sum(1 for c in spells if getattr(c, 'is_mill', False)) >= 3:
        return "Mill Control"
    if has_prowess and has_burn:
        return "Prowess Burn"
    if has_vehicles and len(creatures) >= 15:
        return "Vehicle Aggro"
    if len(creatures) >= 25 and avg_cmc <= 2.5:
        return "Aggro"
    if len(creatures) >= 20 and avg_cmc <= 3.0:
        return "Aggro-Midrange"
    if has_wipe and has_counter and len(creatures) <= 10:
        return "Control"
    if has_counter and has_draw and len(creatures) <= 15:
        return "Tempo-Control"
    if avg_cmc >= 3.5 and len(creatures) >= 10:
        return "Ramp/Midrange"
    if len(creatures) >= 15 and has_removal:
        return "Midrange"
    if has_counter and len(spells) >= 20:
        return "Control"
    return "Midrange"


def run_match(deck1, name1, deck2, name2):
    """Run a single PvP match."""
    p1 = Player(name1, deck1)
    p2 = Player(name2, deck2)
    game = Game([p1, p2])
    agents = [HeuristicAgent(), HeuristicAgent()]
    runner = SimulationRunner(game, agents)
    return runner.run()


def run_bo3(deck1, name1, deck2, name2):
    """Run a Best-of-3 match. Returns (winner_name, games_won_1, games_won_2, avg_turns)."""
    w1, w2 = 0, 0
    turns = []
    for _ in range(3):
        result = run_match(deck1, name1, deck2, name2)
        turns.append(result.turns)
        if result.winner == name1:
            w1 += 1
        elif result.winner == name2:
            w2 += 1
        if w1 >= 2 or w2 >= 2:
            break
    winner = name1 if w1 > w2 else name2 if w2 > w1 else None
    return winner, w1, w2, sum(turns) / max(len(turns), 1)


def run_tournament(contestants, num_games=3):
    """Round-robin tournament. Returns results dict."""
    results = {}
    entries = list(contestants.items())
    
    for i, (name1, deck1) in enumerate(entries):
        if name1 not in results:
            results[name1] = {'wins': 0, 'losses': 0, 'draws': 0, 'turns': [],
                             'opponents_beaten': [], 'boss_wins': 0, 'boss_losses': 0}
        for j, (name2, deck2) in enumerate(entries):
            if i >= j:
                continue
            if name2 not in results:
                results[name2] = {'wins': 0, 'losses': 0, 'draws': 0, 'turns': [],
                                 'opponents_beaten': [], 'boss_wins': 0, 'boss_losses': 0}
            
            for _ in range(num_games):
                result = run_match(deck1, name1, deck2, name2)
                is_boss_1 = name1.startswith("BOSS:")
                is_boss_2 = name2.startswith("BOSS:")
                
                if result.winner == name1:
                    results[name1]['wins'] += 1
                    results[name2]['losses'] += 1
                    results[name1]['opponents_beaten'].append(name2)
                    if is_boss_2:
                        results[name1]['boss_wins'] += 1
                    if is_boss_1:
                        results[name2]['boss_losses'] += 1
                elif result.winner == name2:
                    results[name2]['wins'] += 1
                    results[name1]['losses'] += 1
                    results[name2]['opponents_beaten'].append(name1)
                    if is_boss_1:
                        results[name2]['boss_wins'] += 1
                    if is_boss_2:
                        results[name1]['boss_losses'] += 1
                else:
                    results[name1]['draws'] += 1
                    results[name2]['draws'] += 1
                results[name1]['turns'].append(result.turns)
                results[name2]['turns'].append(result.turns)
    
    return results


def print_deck(deck, name):
    """Print a deck list."""
    print(f"\n{'=' * 55}")
    print(f"  {name}")
    print(f"{'=' * 55}")
    
    creatures, spells, lands = {}, {}, {}
    for card in deck.maindeck:
        if card.is_land:
            lands[card.name] = lands.get(card.name, 0) + 1
        elif card.is_creature:
            creatures[card.name] = creatures.get(card.name, 0) + 1
        else:
            spells[card.name] = spells.get(card.name, 0) + 1
    
    if creatures:
        print(f"\n  Creatures ({sum(creatures.values())}):")
        for n, c in sorted(creatures.items(), key=lambda x: -x[1]):
            print(f"    {c}x {n}")
    if spells:
        print(f"\n  Spells ({sum(spells.values())}):")
        for n, c in sorted(spells.items(), key=lambda x: -x[1]):
            print(f"    {c}x {n}")
    if lands:
        print(f"\n  Lands ({sum(lands.values())}):")
        for n, c in sorted(lands.items(), key=lambda x: -x[1]):
            print(f"    {c}x {n}")


def discover(colors_to_run, population_size=15, generations=5, boss_only=False,
             num_bosses=6, games_per_match=3):
    """Main discovery pipeline."""
    print("\n" + "=" * 65)
    print("  🧬 MTG DECK DISCOVERY — WORLD-CLASS EDITION")
    print("=" * 65)
    
    card_pool = load_card_pool()
    card_pool_dict = {c['name']: c for c in card_pool}
    
    champions = {}
    
    # ── Phase 0: Build Boss Deck Library ──
    print(f"\n🏆 Loading {len(BOSS_ARCHETYPES)} tournament boss decks...")
    boss_decks = {}
    for arch_name, arch_data in BOSS_ARCHETYPES.items():
        try:
            deck = build_boss_deck(arch_name, arch_data, card_pool_dict)
            if len(deck.maindeck) >= 40:
                boss_decks[arch_name] = deck
        except Exception as e:
            pass
    print(f"   ✅ {len(boss_decks)} boss decks ready")
    
    # Select representative subset of bosses for gauntlet
    boss_names = list(boss_decks.keys())
    if len(boss_names) > num_bosses:
        # Always include recent meta + classic diversity
        priority = [n for n in boss_names if any(tag in n for tag in 
                    ['Energy', 'Scam', 'Murktide', 'Burn', 'Control', 'Coffers'])]
        remaining = [n for n in boss_names if n not in priority]
        random.shuffle(remaining)
        gauntlet_names = priority[:num_bosses] + remaining[:max(0, num_bosses - len(priority))]
        gauntlet_names = gauntlet_names[:num_bosses]
    else:
        gauntlet_names = boss_names
    
    gauntlet = {n: boss_decks[n] for n in gauntlet_names}
    print(f"   ⚔️  Gauntlet ({len(gauntlet)}): {', '.join(n.replace('BOSS:', '') for n in gauntlet_names)}")
    
    if boss_only:
        # Just run boss-vs-boss tournament
        print(f"\n{'=' * 65}")
        print(f"  ⚔️  Boss-vs-Boss Tournament ({len(boss_decks)} decks)")
        print(f"{'=' * 65}")
        t0 = time.time()
        results = run_tournament(boss_decks, num_games=games_per_match)
        elapsed = time.time() - t0
        _print_results(results, boss_decks, elapsed)
        return
    
    # ── Phase 1: Evolve Decks ──
    print(f"\n📊 Phase 1: Evolving decks for {len(colors_to_run)} color(s)...")
    
    for color in colors_to_run:
        color_name = COLOR_NAMES.get(color, color)
        print(f"\n{'─' * 45}")
        print(f"  🎨 Evolving {color_name} ({color})...")
        print(f"{'─' * 45}")
        
        t0 = time.time()
        try:
            optimizer = GeneticOptimizer(
                card_pool=card_pool,
                population_size=population_size,
                generations=generations,
                colors=color
            )
            best = optimizer.evolve()
            elapsed = time.time() - t0
            
            deck_name = f"{color_name} Champion"
            champions[deck_name] = best
            
            archetype = classify_archetype(best)
            print(f"  ✅ {deck_name}: {archetype} ({elapsed:.1f}s)")
            print(f"     {len(best.maindeck)} cards, {sum(1 for c in best.maindeck if c.is_creature)} creatures")
            
            top = Counter(c.name for c in best.maindeck if not c.is_land).most_common(5)
            print(f"     Key cards: {', '.join(f'{n}x {name}' for name, n in top)}")
            
        except Exception as e:
            print(f"  ❌ {color_name} failed: {e}")
    
    if not champions:
        print("\n⚠️  No champions evolved. Exiting.")
        return
    
    # ── Phase 2: Boss Gauntlet ──
    print(f"\n{'=' * 65}")
    print(f"  ⚔️  Phase 2: Boss Gauntlet — Evolved vs Tournament Decks")
    print(f"{'=' * 65}")
    
    gauntlet_results = {}
    for champ_name, champ_deck in champions.items():
        gauntlet_results[champ_name] = {'wins': 0, 'losses': 0, 'draws': 0, 
                                         'bosses_beaten': [], 'turns': []}
        for boss_name, boss_deck in gauntlet.items():
            winner, w1, w2, avg_t = run_bo3(champ_deck, champ_name, boss_deck, boss_name)
            gauntlet_results[champ_name]['turns'].append(avg_t)
            if winner == champ_name:
                gauntlet_results[champ_name]['wins'] += 1
                gauntlet_results[champ_name]['bosses_beaten'].append(boss_name.replace('BOSS:', ''))
            elif winner == boss_name:
                gauntlet_results[champ_name]['losses'] += 1
            else:
                gauntlet_results[champ_name]['draws'] += 1
    
    # Print gauntlet results
    print(f"\n  {'Evolved Deck':<25}{'Bosses Beat':>12}{'Bosses Lost':>12}{'Win%':>7}  Bosses Beaten")
    print(f"  {'─' * 80}")
    
    for name, stats in sorted(gauntlet_results.items(), 
                               key=lambda x: x[1]['wins'] / max(x[1]['wins'] + x[1]['losses'], 1),
                               reverse=True):
        total = stats['wins'] + stats['losses'] + stats['draws']
        wr = stats['wins'] / max(total, 1) * 100
        beaten = ', '.join(stats['bosses_beaten'][:3]) or "none"
        if len(stats['bosses_beaten']) > 3:
            beaten += f" +{len(stats['bosses_beaten'])-3} more"
        print(f"  {name:<25}{stats['wins']:>12}{stats['losses']:>12}{wr:>6.0f}%  {beaten}")
    
    # ── Phase 3: Full Tournament (Champions + Top Bosses) ──
    print(f"\n{'=' * 65}")
    print(f"  🏟️  Phase 3: Full Tournament (Evolved + Boss Decks)")
    print(f"{'=' * 65}")
    
    all_contestants = {}
    all_contestants.update(champions)
    all_contestants.update(gauntlet)
    
    t0 = time.time()
    results = run_tournament(all_contestants, num_games=games_per_match)
    elapsed = time.time() - t0
    
    _print_results(results, all_contestants, elapsed)
    
    # ── Phase 4: Novel Strategy Detection ──
    print(f"\n{'─' * 65}")
    print("  🔬 NOVEL STRATEGY ANALYSIS")
    print(f"{'─' * 65}")
    
    for name, deck in champions.items():
        archetype = classify_archetype(deck)
        stats = results.get(name, {})
        total = stats.get('wins', 0) + stats.get('losses', 0) + stats.get('draws', 0)
        wr = stats.get('wins', 0) / max(total, 1) * 100
        
        novelties = []
        cards = deck.maindeck
        if any(getattr(c, 'is_mill', False) for c in cards):
            novelties.append("🌊 mill as win condition")
        if any(getattr(c, 'is_vehicle', False) for c in cards):
            novelties.append("🚗 vehicle aggro strategy")
        if any(getattr(c, 'has_prowess', False) for c in cards) and any(getattr(c, 'is_burn', False) for c in cards):
            novelties.append("🔥 prowess + burn synergy")
        if any(getattr(c, 'cycling_cost', None) for c in cards):
            novelties.append("♻️  cycling for consistency")
        if any(getattr(c, 'is_proliferate', False) for c in cards):
            novelties.append("🧬 proliferate counter synergy")
        if any(getattr(c, 'is_fight', False) for c in cards):
            novelties.append("⚔️  fight-based removal")
        if stats.get('boss_wins', 0) > 0:
            novelties.append(f"🏆 beats {stats['boss_wins']} tournament deck(s)!")
        
        beaten_bosses = gauntlet_results.get(name, {}).get('bosses_beaten', [])
        if beaten_bosses:
            novelties.append(f"💀 beat: {', '.join(beaten_bosses[:4])}")
        
        if novelties:
            print(f"\n  {name} ({archetype}, {wr:.0f}% WR):")
            for n in novelties:
                print(f"    {n}")
    
    # Print best evolved deck list
    best_evolved = None
    best_wr = -1
    for name in champions:
        stats = results.get(name, {})
        total = stats.get('wins', 0) + stats.get('losses', 0) + stats.get('draws', 0)
        wr = stats.get('wins', 0) / max(total, 1)
        if wr > best_wr:
            best_wr = wr
            best_evolved = name
    
    if best_evolved:
        print_deck(champions[best_evolved], f"🏆 BEST EVOLVED DECK: {best_evolved}")
    
    print(f"\n{'=' * 65}")
    print(f"  Discovery complete! {len(champions)} evolved vs {len(gauntlet)} tournament decks.")
    print(f"{'=' * 65}\n")


def _print_results(results, contestants, elapsed):
    """Print tournament results table."""
    print(f"\n  🏆 RESULTS (tournament ran in {elapsed:.1f}s)")
    print(f"\n  {'Rank':<6}{'Deck':<30}{'W':>4}{'L':>4}{'D':>4}{'Win%':>7}{'Avg T':>7}  Type")
    print(f"  {'─' * 75}")
    
    ranked = sorted(results.items(),
                   key=lambda x: x[1]['wins'] / max(x[1]['wins'] + x[1]['losses'] + x[1]['draws'], 1),
                   reverse=True)
    
    for rank, (name, stats) in enumerate(ranked, 1):
        total = stats['wins'] + stats['losses'] + stats['draws']
        wr = stats['wins'] / max(total, 1) * 100
        avg_t = sum(stats['turns']) / max(len(stats['turns']), 1)
        
        is_boss = name.startswith("BOSS:")
        display_name = name.replace("BOSS:", "⭐") if is_boss else f"🧬{name}"
        if len(display_name) > 28:
            display_name = display_name[:28]
        
        deck = contestants.get(name)
        archetype = classify_archetype(deck) if deck else "?"
        
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        print(f"  {medal}{rank:<4}{display_name:<30}{stats['wins']:>4}{stats['losses']:>4}{stats['draws']:>4}"
              f"{wr:>6.1f}%{avg_t:>6.1f}  {archetype}")


def main():
    parser = argparse.ArgumentParser(description='MTG Deck Discovery — World-Class Edition')
    parser.add_argument('--colors', nargs='+', default=MONO_COLORS,
                       help='Color(s) to evolve (W U B R G WU BR etc.)')
    parser.add_argument('--all', action='store_true',
                       help='Run all mono + two-color combinations')
    parser.add_argument('--gen', type=int, default=5,
                       help='Generations per color (default: 5)')
    parser.add_argument('--pop', type=int, default=15,
                       help='Population size (default: 15)')
    parser.add_argument('--boss-only', action='store_true',
                       help='Run boss-vs-boss tournament only')
    parser.add_argument('--bosses', type=int, default=6,
                       help='Number of boss decks in gauntlet (default: 6)')
    parser.add_argument('--games', type=int, default=3,
                       help='Games per match in tournament (default: 3)')
    
    args = parser.parse_args()
    
    if args.all:
        colors = MONO_COLORS + TWO_COLOR_PAIRS
    else:
        colors = args.colors
    
    discover(colors, population_size=args.pop, generations=args.gen,
             boss_only=args.boss_only, num_bosses=args.bosses,
             games_per_match=args.games)


if __name__ == '__main__':
    main()
