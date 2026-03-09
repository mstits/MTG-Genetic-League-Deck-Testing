"""audit_engine.py — Generates games and audits logs for rule violations.

Run:  python audit_engine.py --games 200 --review 40
"""

import sys, os, json, re, random, copy, time, argparse, textwrap
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.card import Card
from engine.card_pool import load_card_pool
from engine.player import Player
from engine.deck import Deck
from engine.game import Game
from agents.heuristic_agent import HeuristicAgent


# ────────────────────────────────────────────────────────────
# 1. BUILD DIVERSE DECKS from the real card pool
# ────────────────────────────────────────────────────────────
def build_deck(pool, color, seed=None):
    """Build a 40-card deck of a specific color from the pool."""
    rng = random.Random(seed)
    
    # Gather on-color cards (with mana cost — already filtered by card_pool.py)
    candidates = [c for c in pool
                  if color in c.color_identity
                  and c.cost  # must have mana cost
                  and not c.is_land]
    
    # Sort by CMC buckets
    from engine.player import Player as _P
    low = [c for c in candidates if _P._parse_cmc(c.cost) <= 2]
    mid = [c for c in candidates if 2 < _P._parse_cmc(c.cost) <= 4]
    high = [c for c in candidates if _P._parse_cmc(c.cost) > 4]
    
    spells = []
    # 8-10 low, 6-8 mid, 2-4 high
    if low:  spells += rng.sample(low,  min(rng.randint(8,10), len(low)))
    if mid:  spells += rng.sample(mid,  min(rng.randint(6,8),  len(mid)))
    if high: spells += rng.sample(high, min(rng.randint(2,4),  len(high)))
    
    # Pad to 22 spells if needed
    remaining = [c for c in candidates if c not in spells]
    while len(spells) < 22 and remaining:
        spells.append(rng.choice(remaining))
    
    # Add lands (18)
    land_map = {'W': 'Plains', 'U': 'Island', 'B': 'Swamp', 'R': 'Mountain', 'G': 'Forest'}
    basic_land = [c for c in pool if c.name == land_map.get(color, 'Island')]
    if not basic_land:
        basic_land = [c for c in pool if c.name == 'Island']
    
    d = Deck()
    for c in spells:
        d.add_card(copy.deepcopy(c), 1)
    for _ in range(18):
        d.add_card(copy.deepcopy(basic_land[0]), 1)
    
    return d


# ────────────────────────────────────────────────────────────
# 2. PLAY A GAME and capture detailed log
# ────────────────────────────────────────────────────────────
def play_game(deck1, deck2, name1="P1", name2="P2", max_turns=50, max_steps=5000):
    """Play a single game and return (log_lines, metadata)."""
    p1 = Player(name1, deck1)
    p2 = Player(name2, deck2)
    game = Game([p1, p2])
    game.start_game()
    
    agent = HeuristicAgent()
    steps = 0
    
    while not game.game_over and game.turn_count <= max_turns and steps < max_steps:
        actions = game.get_legal_actions()
        if not actions:
            game.advance_phase()
            continue
        player = game.priority_player
        action = agent.get_action(game, player)
        game.apply_action(action)
        steps += 1
    
    meta = {
        'turns': game.turn_count,
        'winner': game.winner.name if game.winner else 'Draw/Timeout',
        'p1_life': p1.life,
        'p2_life': p2.life,
        'steps': steps,
        'game_over': game.game_over,
    }
    
    return game.log if hasattr(game, 'log') else [], meta


# ────────────────────────────────────────────────────────────
# 3. RULE VIOLATION CHECKER
# ────────────────────────────────────────────────────────────
ISSUES = []

def check_log(log_lines, meta, game_id):
    """Analyze a game log for Magic rule violations."""
    issues = []
    
    turn_actions = defaultdict(list)
    current_turn = 0
    lands_by_turn = defaultdict(lambda: defaultdict(int))
    casts_by_turn = defaultdict(lambda: defaultdict(list))
    
    for line in log_lines:
        # Track turn number
        turn_m = re.match(r'(?:---\s*)?T(\d+)', line)
        if turn_m:
            current_turn = int(turn_m.group(1))
        
        turn_actions[current_turn].append(line)
        
        # ── CHECK: Multiple land drops per turn ──
        land_m = re.search(r'T(\d+): (\w+) plays (\w+)', line)
        if land_m:
            t, player, land = int(land_m.group(1)), land_m.group(2), land_m.group(3)
            lands_by_turn[t][player] += 1
            if lands_by_turn[t][player] > 1:
                issues.append(f"MULTI_LAND: T{t} {player} played {lands_by_turn[t][player]} lands (max 1)")
        
        # ── CHECK: Cast with empty cost ──
        cast_m = re.search(r'T(\d+): (\w+) casts (.+?) \(\)', line)
        if cast_m:
            t, player, card = int(cast_m.group(1)), cast_m.group(2), cast_m.group(3)
            issues.append(f"FREE_CAST: T{t} {player} casts {card} with no mana cost")
        
        # ── CHECK: Creature casts (track mana vs cost) ──
        cast_cost_m = re.search(r'T(\d+): (\w+) casts (.+?) \((\{.+?\})\)', line)
        if cast_cost_m:
            t = int(cast_cost_m.group(1))
            player = cast_cost_m.group(2)
            card = cast_cost_m.group(3)
            cost = cast_cost_m.group(4)
            casts_by_turn[t][player].append((card, cost))
        
        # ── CHECK: Massive overkill on early turns (symptom of broken P/T) ──
        dmg_m = re.search(r'T(\d+): (.+?) deals (\d+) to (\w+)', line)
        if dmg_m:
            t, card, dmg, target = int(dmg_m.group(1)), dmg_m.group(2), int(dmg_m.group(3)), dmg_m.group(4)
            if t <= 2 and dmg >= 10:
                issues.append(f"EARLY_NUKE: T{t} {card} deals {dmg} damage (suspiciously high for turn {t})")
        
        # ── CHECK: Death's Shadow should not deal 13 damage ──
        if "Death's Shadow" in line and 'deals 13' in line:
            issues.append(f"CDA_FAIL: Death's Shadow dealing 13 damage (CDA not applied): {line.strip()}")
        
        # ── CHECK: Negative life continuation ──
        life_m = re.search(r'\((-?\d+) life\)', line)
        if life_m:
            life = int(life_m.group(1))
            # After life goes negative, game should end soon
        
        # ── CHECK: Creature attacks same turn without haste ──
        # This requires tracking ETB and attack timing which is harder from logs
        
    # ── CHECK: Game length ──
    if meta['turns'] <= 1 and not meta['game_over']:
        issues.append(f"STUCK: Game stuck at turn {meta['turns']} after {meta['steps']} steps")
    if meta['turns'] <= 2 and meta['game_over'] and meta['winner'] != 'Draw/Timeout':
        issues.append(f"TOO_FAST: Game ended turn {meta['turns']} — winner: {meta['winner']}")
    
    # ── CHECK: Multiple casts beyond mana capacity ──
    for t, players in casts_by_turn.items():
        for player, casts in players.items():
            if len(casts) > t + 1:  # Can't cast more spells than lands you could have
                total_cmc = 0
                for card, cost in casts:
                    from engine.player import Player as _P
                    total_cmc += _P._parse_cmc(cost)
                if total_cmc > t + 2:  # Generous: t lands + 2 for mana dorks/rituals
                    cards_str = ', '.join(f"{c}({cost})" for c, cost in casts)
                    issues.append(f"MANA_CHEAT: T{t} {player} cast {len(casts)} spells (total CMC={total_cmc}, max plausible mana≈{t}): {cards_str}")
    
    return issues


# ────────────────────────────────────────────────────────────
# 4. MAIN
# ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--games', type=int, default=200)
    parser.add_argument('--review', type=int, default=40)
    parser.add_argument('--log-dir', default='/tmp/audit_logs')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    
    os.makedirs(args.log_dir, exist_ok=True)
    
    print(f"Loading card pool...")
    pool = load_card_pool('modern')
    print(f"  {len(pool)} cards loaded")
    
    colors = ['W', 'U', 'B', 'R', 'G']
    rng = random.Random(args.seed)
    
    # Generate matchups
    matchups = []
    for i in range(args.games):
        c1, c2 = rng.sample(colors, 2)
        matchups.append((c1, c2, i))
    
    print(f"\n{'='*60}")
    print(f"Playing {args.games} games...")
    print(f"{'='*60}")
    
    results = []
    turn_counts = []
    t0 = time.time()
    
    for idx, (c1, c2, seed) in enumerate(matchups):
        d1 = build_deck(pool, c1, seed=seed*100)
        d2 = build_deck(pool, c2, seed=seed*100+1)
        
        log_lines, meta = play_game(d1, d2, f"{c1}-deck", f"{c2}-deck")
        meta['game_id'] = idx
        meta['colors'] = f"{c1} vs {c2}"
        results.append((log_lines, meta))
        turn_counts.append(meta['turns'])
        
        if (idx+1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"  {idx+1}/{args.games} games ({elapsed:.1f}s, {(idx+1)/elapsed:.0f} games/sec)")
    
    elapsed = time.time() - t0
    print(f"\nDone: {args.games} games in {elapsed:.1f}s ({args.games/elapsed:.0f} games/sec)")
    
    # Turn distribution
    print(f"\n{'='*60}")
    print(f"Turn Distribution:")
    for bucket in [1,2,3,4,5,6,7,8,9,10,15,20,30,50]:
        cnt = sum(1 for t in turn_counts if t <= bucket)
        pct = cnt / len(turn_counts) * 100
        bar = '█' * int(pct / 2)
        print(f"  ≤{bucket:2d} turns: {cnt:4d}/{len(turn_counts)} ({pct:5.1f}%) {bar}")
    
    from statistics import mean, median
    print(f"  Avg: {mean(turn_counts):.1f}, Median: {median(turn_counts):.1f}, Min: {min(turn_counts)}, Max: {max(turn_counts)}")
    
    # Review random sample
    print(f"\n{'='*60}")
    print(f"Auditing {args.review} random games...")
    print(f"{'='*60}")
    
    sample = rng.sample(results, min(args.review, len(results)))
    all_issues = defaultdict(list)
    games_with_issues = 0
    
    for log_lines, meta in sample:
        issues = check_log(log_lines, meta, meta['game_id'])
        if issues:
            games_with_issues += 1
            for iss in issues:
                category = iss.split(':')[0]
                all_issues[category].append(iss)
    
    # Write individual game logs for review
    for i, (log_lines, meta) in enumerate(sample[:10]):
        log_path = os.path.join(args.log_dir, f"game_{meta['game_id']:04d}.log")
        with open(log_path, 'w') as f:
            f.write(f"Game {meta['game_id']}: {meta['colors']} | {meta['turns']} turns | Winner: {meta['winner']}\n")
            f.write(f"P1 life: {meta['p1_life']}, P2 life: {meta['p2_life']}, Steps: {meta['steps']}\n")
            f.write('='*60 + '\n')
            for line in log_lines:
                f.write(line + '\n')
    
    # Summary
    print(f"\nGames reviewed: {args.review}")
    print(f"Games with issues: {games_with_issues} ({games_with_issues/args.review*100:.0f}%)")
    
    if all_issues:
        print(f"\nIssues by category:")
        for cat, items in sorted(all_issues.items(), key=lambda x: -len(x[1])):
            print(f"\n  {cat} ({len(items)} occurrences):")
            # Show unique examples (max 5)
            unique = list(set(items))[:5]
            for item in unique:
                print(f"    • {item}")
    else:
        print("\n✅ NO ISSUES FOUND! Engine passes audit.")
    
    # Save full results
    results_path = os.path.join(args.log_dir, 'audit_results.json')
    with open(results_path, 'w') as f:
        json.dump({
            'games': args.games,
            'reviewed': args.review,
            'games_with_issues': games_with_issues,
            'issues': dict(all_issues),
            'turn_stats': {
                'avg': mean(turn_counts),
                'median': median(turn_counts),
                'min': min(turn_counts),
                'max': max(turn_counts),
            }
        }, f, indent=2)
    
    print(f"\nFull logs: {args.log_dir}/")
    print(f"Results:   {results_path}")
    
    return games_with_issues == 0


if __name__ == '__main__':
    clean = main()
    sys.exit(0 if clean else 1)
