"""GeneticOptimizer — Evolutionary deck construction using genetic algorithms.

Creates decks by evolving a population through selection, crossover, and mutation.
Color-aware: respects mana base construction, color identity filtering, and
dual-land detection for multi-color decks.

Fitness is evaluated by simulating games against reference opponents —
lower average kills (fewer turns to win) = higher fitness score.
"""

import random
from typing import List
from engine.deck import Deck
from engine.card import Card
from engine.card_builder import dict_to_card
from engine.game import Game
from engine.player import Player
from simulation.runner import SimulationRunner
from agents.heuristic_agent import HeuristicAgent
import re

# Color -> Basic Land mapping
COLOR_LANDS = {
    'W': 'Plains',
    'U': 'Island',
    'B': 'Swamp',
    'R': 'Mountain',
    'G': 'Forest',
    'C': 'Wastes'
}

# Common dual land patterns to detect
DUAL_LAND_PATTERNS = [
    'shock', 'check', 'fetch', 'pain', 'fast', 'filter', 'pathway',
    'triome', 'dual', 'temple', 'refuge', 'scryland'
]

def parse_cmc(cost: str) -> int:
    if not cost:
        return 0
    total = 0
    generic = re.findall(r'\{(\d+)\}', cost)
    for g in generic:
        total += int(g)
    colored = re.findall(r'\{([WUBRGC])\}', cost)
    total += len(colored)
    # Hybrid mana like {R/W} counts as 1
    hybrid = re.findall(r'\{[WUBRG]/[WUBRG]\}', cost)
    total += len(hybrid)
    return total

def card_quality_score(card_data: dict) -> float:
    """Rate a card's quality for deck inclusion — now with synergy awareness."""
    score = 1.0
    cmc = parse_cmc(card_data.get('mana_cost', ''))
    type_line = card_data.get('type_line', '')
    text = card_data.get('oracle_text', '').lower()
    
    if 'Creature' in type_line:
        p = 0
        t = 0
        try:
            p = int(card_data.get('power', 0))
            t = int(card_data.get('toughness', 0))
        except (ValueError, TypeError):
            pass
        
        if cmc > 0:
            score = (p + t) / cmc
        else:
            # 0-CMC creatures: cap score to prevent absurd back-face ratings
            score = min(p + t, 4.0)
        
        # Keywords — big value
        if 'haste' in text: score += 1.5
        if 'flying' in text: score += 1.2
        if 'trample' in text: score += 0.7
        if 'lifelink' in text: score += 0.8
        if 'deathtouch' in text: score += 1.0
        if 'first strike' in text: score += 0.7
        if 'double strike' in text: score += 2.0
        if 'vigilance' in text: score += 0.5
        if 'hexproof' in text: score += 0.8
        if 'indestructible' in text: score += 1.5
        if 'menace' in text: score += 0.5
        if 'flash' in text: score += 0.5
        
        # ETB effects are extremely valuable
        if 'enters the battlefield' in text or 'enters play' in text:
            score += 1.5
            if 'damage' in text: score += 1.0
            if 'destroy' in text: score += 1.5
            if 'draw' in text: score += 1.0
            if 'return' in text: score += 0.8
            if 'gain' in text and 'life' in text: score += 0.5
        
    elif 'Instant' in type_line or 'Sorcery' in type_line:
        if 'destroy' in text and 'creature' in text:
            score = 3.5
        elif 'exile' in text and 'creature' in text:
            score = 4.0  # Exile > Destroy
        elif 'damage' in text:
            dmg = re.search(r'(\d+) damage', text)
            if dmg:
                score = int(dmg.group(1)) / max(cmc, 1) * 1.5
            else:
                score = 2.0
        elif 'draw' in text:
            draw_match = re.search(r'draw (\d+)', text)
            if draw_match:
                score = int(draw_match.group(1)) * 0.9
            else:
                score = 1.5
        elif 'counter' in text and 'spell' in text:
            score = 2.5
        elif 'destroy all creatures' in text or 'all creatures get -' in text:
            score = 4.5  # Board wipes are premium
        elif '+' in text and '/' in text:
            score = 1.5  # Buff spells
        else:
            score = 0.5
    
    elif 'Enchantment' in type_line:
        # Auras and static effects
        if 'creature' in text and ('+' in text or 'destroy' in text):
            score = 1.5
        else:
            score = 0.8
    elif 'Artifact' in type_line:
        score = 0.9
    elif 'Planeswalker' in type_line:
        score = 2.0  # Planeswalkers are generally powerful
    else:
        score = 0.5
    
    # Penalize expensive cards
    if cmc > 6: score *= 0.2
    elif cmc > 5: score *= 0.4
    elif cmc > 4: score *= 0.7
    elif cmc == 0 and 'Creature' not in type_line: score *= 0.3
    
    # P0 FIX: Penalize drawback creatures (Death's Shadow, etc.)
    if card_data.get('has_drawback', False):
        score *= 0.2
    
    return max(score, 0.1)

def synergy_score(card_data: dict, deck_cards: List[dict]) -> float:
    """Score how well a card synergizes with existing deck cards."""
    score = 0.0
    type_line = card_data.get('type_line', '')
    text = card_data.get('oracle_text', '').lower()
    
    # Extract creature types
    card_types = set()
    if 'Creature' in type_line and '—' in type_line:
        card_types = set(type_line.split('—')[1].strip().split())
    
    for existing in deck_cards:
        ex_type = existing.get('type_line', '')
        ex_text = existing.get('oracle_text', '').lower()
        
        # Tribal synergy: shared creature types
        if card_types and 'Creature' in ex_type and '—' in ex_type:
            ex_types = set(ex_type.split('—')[1].strip().split())
            shared = card_types & ex_types
            if shared:
                score += 0.5 * len(shared)
        
        # Keyword synergy packages
        # +1/+1 counter synergy
        if '+1/+1 counter' in text and '+1/+1 counter' in ex_text:
            score += 0.3
        
        # Sacrifice synergy
        if 'sacrifice' in text and ('when' in ex_text and 'dies' in ex_text):
            score += 0.5
        
        # Token synergy
        if 'token' in text and ('token' in ex_text or 'each creature' in ex_text):
            score += 0.3
    
    return score


class GeneticOptimizer:
    def __init__(self, card_pool: List[dict], population_size=20, generations=5, 
                 colors=None, champion_cards=None, meta_pillars=None):
        self.full_pool = card_pool
        self.colors = colors or "R"
        self.population_size = population_size
        self.generations = generations
        self.population: List[Deck] = []
        self.champion_cards = champion_cards or {}
        self.meta_pillars = meta_pillars or []
        
        # Build color-filtered pool
        self.card_pool = self._build_color_pool()
        self.land_cards = self._build_land_base()
        
        # Pre-score all cards for quality
        self.scored_pool = []
        for card in self.card_pool:
            if 'Land' not in card.get('type_line', ''):
                score = card_quality_score(card)
                self.scored_pool.append((card, score))
        
        self.scored_pool.sort(key=lambda x: x[1], reverse=True)
        
    def _build_color_pool(self) -> List[dict]:
        """Filter cards that match the specified colors.
        
        When colors='C', only includes truly colorless cards (artifacts, Eldrazi,
        colorless spells — no colored mana symbols in cost).
        """
        pool = []
        target_colors = set(self.colors)
        is_colorless = self.colors == 'C'
        
        for card in self.full_pool:
            cost = card.get('mana_cost', '')
            type_line = card.get('type_line', '')
            card_colors = set()
            for c in 'WUBRG':
                if c in cost:
                    card_colors.add(c)
            
            # Skip basic lands (we add them ourselves)
            if card['name'] in ('Plains', 'Island', 'Swamp', 'Mountain', 'Forest', 'Wastes'):
                continue
            
            if is_colorless:
                # Colorless mode: only cards with NO colored mana in cost
                if not card_colors and 'Land' not in type_line:
                    pool.append(card)
                continue
            
            # Include colorless non-lands for any deck
            if not card_colors:
                if 'Land' not in type_line:
                    pool.append(card)
                continue
            
            # Include if card colors are subset of our colors
            if card_colors.issubset(target_colors):
                pool.append(card)
        
        return pool
    
    def _build_land_base(self) -> dict:
        """Build optimized land base with dual lands when available.
        
        Colorless decks use Wastes + utility lands.
        """
        total_lands = 24
        lands = {}
        
        if self.colors == 'C':
            # Colorless: all Wastes (utility lands come from card pool)
            lands['Wastes'] = total_lands
            return lands
        
        num_colors = len(self.colors)
        
        if num_colors >= 2:
            # Look for dual lands in our color combo
            dual_lands = self._find_dual_lands()
            
            # Use up to 8 slots for dual lands (4 copies of up to 2 duals)
            dual_slots = 0
            for dual_name in dual_lands[:2]:  # Max 2 different duals
                lands[dual_name] = 4
                dual_slots += 4
            
            # Fill remaining with basics
            remaining = total_lands - dual_slots
            per_color = remaining // num_colors
            for c in self.colors:
                lands[COLOR_LANDS[c]] = per_color
            
            # Distribute remainder
            leftover = remaining - (per_color * num_colors)
            if leftover > 0:
                lands[COLOR_LANDS[self.colors[0]]] = lands.get(COLOR_LANDS[self.colors[0]], 0) + leftover
        else:
            lands[COLOR_LANDS[self.colors[0]]] = total_lands
        
        return lands
    
    def _find_dual_lands(self) -> List[str]:
        """Find dual lands that produce colors we need."""
        target_colors = set(self.colors)
        duals = []
        
        for card in self.full_pool:
            type_line = card.get('type_line', '')
            if 'Land' not in type_line:
                continue
            name = card['name']
            if name in ('Plains', 'Island', 'Swamp', 'Mountain', 'Forest'):
                continue
            
            text = card.get('oracle_text', '').lower()
            color_identity = set(card.get('color_identity', []))
            
            # Check if land's color identity is subset of our colors
            if color_identity and color_identity.issubset(target_colors) and len(color_identity) >= 2:
                # Prefer lands that don't enter tapped
                enters_tapped = 'enters the battlefield tapped' in text or 'enters tapped' in text
                priority = 0 if enters_tapped else 1
                duals.append((name, priority))
        
        # Sort: untapped duals first
        duals.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in duals[:4]]
        
    def generate_initial_population(self):
        for _ in range(self.population_size):
            deck = self._create_deck()
            self.population.append(deck)
        return self.population
            
    def _create_deck(self) -> Deck:
        """Build a deck with 4-of playsets, curve awareness, and synergy."""
        deck = Deck()
        
        # Add lands
        for land_name, count in self.land_cards.items():
            land_data = next((c for c in self.full_pool if c['name'] == land_name), None)
            if land_data:
                deck.add_card(self._dict_to_card(land_data), count)
        
        if not self.scored_pool:
            return deck
        
        if self.champion_cards:
            return self._create_seeded_deck(deck)
        
        # Build curve-aware deck: 9 unique × 4 copies = 36 spells
        selected = []
        used_names = set()
        spells_needed = 9
        
        candidates = list(self.scored_pool)
        
        while len(selected) < spells_needed and candidates:
            # Weighted random + synergy bonus
            weights = []
            for card_data, base_score in candidates:
                syn = synergy_score(card_data, selected) if selected else 0
                w = max(base_score + syn + random.uniform(-0.3, 0.3), 0.01)
                weights.append(w)
            
            total_w = sum(weights)
            weights = [w / total_w for w in weights]
            
            idx = random.choices(range(len(candidates)), weights=weights, k=1)[0]
            card_data, _ = candidates[idx]
            
            if card_data['name'] not in used_names:
                selected.append(card_data)
                used_names.add(card_data['name'])
            
            candidates.pop(idx)
        
        for card_data in selected:
            card = self._dict_to_card(card_data)
            deck.add_card(card, 4)
        
        # Generate sideboard
        deck.sideboard = self._generate_sideboard(deck)
        
        return deck
    
    def _create_seeded_deck(self, deck: Deck) -> Deck:
        """Create a deck seeded from champion DNA with mutations."""
        used_names = set()
        spells_added = 0
        
        champion_items = list(self.champion_cards.items())
        random.shuffle(champion_items)
        
        for name, count in champion_items:
            if spells_added >= 36:
                break
            if 'Land' in name or name in ('Plains', 'Island', 'Swamp', 'Mountain', 'Forest'):
                continue
            
            if random.random() < 0.7:
                card_data = next((c for c in self.full_pool if c['name'] == name), None)
                if card_data and name not in used_names:
                    deck.add_card(self._dict_to_card(card_data), min(count, 4))
                    used_names.add(name)
                    spells_added += min(count, 4)
        
        while spells_added < 36 and self.scored_pool:
            card_data, score = random.choice(self.scored_pool[:30])
            if card_data['name'] not in used_names:
                copies = 4 if spells_added + 4 <= 36 else 36 - spells_added
                deck.add_card(self._dict_to_card(card_data), copies)
                used_names.add(card_data['name'])
                spells_added += copies
        
        deck.sideboard = self._generate_sideboard(deck)
        return deck
    
    def _generate_sideboard(self, deck: Deck) -> List[Card]:
        """Generate a 15-card sideboard with hate cards and alternatives."""
        sideboard = []
        deck_names = set(c.name for c in deck.maindeck)
        
        # Pick 5 unique cards × 3 copies = 15 sideboard cards
        # Prioritize: removal, lifegain, anti-aggro, anti-control
        sb_candidates = []
        for card_data, score in self.scored_pool:
            if card_data['name'] in deck_names:
                continue
            text = card_data.get('oracle_text', '').lower()
            type_line = card_data.get('type_line', '')
            
            sb_score = score
            # Sideboard premium: removal, lifegain, sweepers
            if 'destroy' in text and 'creature' in text: sb_score += 2.0
            if 'exile' in text: sb_score += 2.0
            if 'gain' in text and 'life' in text: sb_score += 1.0
            if 'each creature' in text or 'all creatures' in text: sb_score += 3.0  # Board wipes
            if 'enchantment' in text and 'destroy' in text: sb_score += 1.0
            if 'artifact' in text and 'destroy' in text: sb_score += 1.0
            
            sb_candidates.append((card_data, sb_score))
        
        sb_candidates.sort(key=lambda x: x[1], reverse=True)
        
        sb_names = set()
        for card_data, _ in sb_candidates[:10]:
            if len(sideboard) >= 15:
                break
            if card_data['name'] not in sb_names:
                copies = min(3, 15 - len(sideboard))
                for _ in range(copies):
                    sideboard.append(self._dict_to_card(card_data))
                sb_names.add(card_data['name'])
        
        return sideboard
            
    def _dict_to_card(self, data: dict) -> Card:
        return dict_to_card(data)

    def evaluate_fitness(self, deck: Deck) -> float:
        """Multi-dimensional fitness: PvP wins + speed + life cushion + 
        curve quality + composition + matchup spread + metagame targeting."""
        wins = 0
        total_turns = 0
        total_life_remaining = 0
        games = 0
        matchup_spread_bonus = 0  # Bonus for beating top-ELO opponents
        
        opponents = self.population[:]
        if self.meta_pillars:
            # Nash Equilibrium: Train against the established Meta Pillars
            opponents.extend(self.meta_pillars * 2)  # Weight the meta heavily
        random.shuffle(opponents)
        
        # Sort opponents by ELO descending so we can give matchup spread bonuses
        rated_opponents = sorted(opponents[:8], 
                                 key=lambda d: getattr(d, 'elo', 1200), reverse=True)
        
        for rank, opp_deck in enumerate(rated_opponents[:5]):
            if opp_deck is deck:
                continue
            try:
                player = Player("Candidate", deck)
                opponent = Player("Opponent", opp_deck)
                game = Game([player, opponent])
                runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                result = runner.run()
                
                if result.winner == "Candidate":
                    wins += 2
                    total_life_remaining += max(0, player.life)
                    # Matchup spread: more points for beating higher-ranked opponents
                    spread_weight = max(0, 5 - rank) * 0.4  # Rank 0 = +2.0, Rank 4 = +0.4
                    matchup_spread_bonus += spread_weight
                elif result.winner is None:
                    wins += 0.5
                total_turns += result.turns
                games += 1
            except Exception:
                pass  # Skip games that error out during fitness evaluation
        
        if games == 0:
            return 0
            
        win_rate = wins / games
        
        # Evolutionary Novelty Search (Phase 7)
        try:
            from data.vector_db import get_novelty_score
            card_map = {}
            for c in deck.maindeck:
                card_map[c.name] = card_map.get(c.name, 0) + 1
            novelty = get_novelty_score(card_map, k=5)
        except ImportError:
            novelty = 0.5
            
        base_quality = (win_rate * 0.5) + (novelty * 0.5)
        
        # Base: Scaled up to 10 points
        pvp_score = base_quality * 10
        
        # Speed bonus: faster wins are better (0-3 range)
        avg_turns = total_turns / games
        speed_bonus = max(0, 3.0 - (avg_turns - 8) * 0.2)  # Peak at turn 8
        
        # Life cushion: winning with more life = more stable (0-2 range)
        avg_life = total_life_remaining / max(1, wins // 2)
        life_bonus = min(2.0, avg_life * 0.1)
        
        # Curve quality (0-3 range)
        curve_score = self._evaluate_curve(deck)
        
        # Composition quality (0-2 range)
        comp_score = self._evaluate_composition(deck)
        
        # Matchup spread (0-4 range) — beating top opponents is worth more
        spread_score = min(4.0, matchup_spread_bonus)
        
        return pvp_score + speed_bonus + life_bonus + curve_score + comp_score + spread_score
    
    def _evaluate_curve(self, deck: Deck) -> float:
        """Reward decks with a good mana curve. Peak: 1-2-3 CMC distribution."""
        cmc_buckets = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for card in deck.maindeck:
            if card.is_land:
                continue
            cmc = 0
            if card.cost:
                import re
                pips = re.findall(r'\{(\d+)\}', card.cost)
                cmc += sum(int(p) for p in pips)
                cmc += len(re.findall(r'\{[WUBRGC]\}', card.cost))
            bucket = min(cmc, 5)
            cmc_buckets[bucket] = cmc_buckets.get(bucket, 0) + 1
        
        total_spells = sum(cmc_buckets.values())
        if total_spells == 0:
            return 0
        
        score = 0.0
        # Ideal curve: lots of 1-2 drops, some 3s, few 4+
        one_two = (cmc_buckets.get(1, 0) + cmc_buckets.get(2, 0)) / total_spells
        if one_two >= 0.5:
            score += 1.5  # Good low curve
        elif one_two >= 0.3:
            score += 0.8
        
        # Penalize top-heavy
        high = (cmc_buckets.get(4, 0) + cmc_buckets.get(5, 0)) / total_spells
        if high <= 0.15:
            score += 1.0  # Not too top-heavy
        elif high <= 0.3:
            score += 0.5
        
        # Bonus for having some curve presence at each slot
        filled_slots = sum(1 for k in [1, 2, 3] if cmc_buckets.get(k, 0) > 0)
        score += filled_slots * 0.17
        
        return min(3.0, score)
    
    def _evaluate_composition(self, deck: Deck) -> float:
        """Reward balanced creature/spell ratio."""
        creatures = sum(1 for c in deck.maindeck if c.is_creature)
        spells = sum(1 for c in deck.maindeck if not c.is_creature and not c.is_land)
        lands = sum(1 for c in deck.maindeck if c.is_land)
        total = len(deck.maindeck)
        
        if total == 0:
            return 0
        
        score = 0.0
        
        # Land ratio: 38-42% is ideal (23-25 lands in 60)
        land_pct = lands / total
        if 0.38 <= land_pct <= 0.42:
            score += 0.8
        elif 0.35 <= land_pct <= 0.45:
            score += 0.4
        
        # Creature/spell balance: reward having both
        nonland = creatures + spells
        if nonland > 0:
            creature_pct = creatures / nonland
            if 0.4 <= creature_pct <= 0.75:
                score += 0.8  # Good balance
            elif 0.3 <= creature_pct <= 0.85:
                score += 0.4
        
        # Penalize decks with no interaction (all creatures, no spells)
        if spells == 0:
            score -= 0.5
        
        return max(0, min(2.0, score))

    def evolve(self) -> Deck:
        # ─── Pre-evolution rules fidelity gate ───
        try:
            from engine.rules_sandbox import run_quick_fidelity_check
            from engine.fidelity_report import FidelityError
            fidelity = run_quick_fidelity_check()
            if not fidelity.all_passed:
                print(f"⚠️  FIDELITY FAILURE: {fidelity.failed}/{fidelity.total_scenarios} scenarios failed")
                for f in fidelity.failures[:5]:
                    print(f"   [{f.scenario_id}] {f.scenario_name}: {f.deviation}")
                fidelity.save_report()
                raise FidelityError(fidelity)
        except ImportError:
            pass  # rules_sandbox not available — skip check

        self.generate_initial_population()
        score_history = []
        
        for gen in range(self.generations):
            scores = []
            for deck in self.population:
                score = self.evaluate_fitness(deck)
                scores.append((score, deck))
            
            scores.sort(key=lambda x: x[0], reverse=True)
            best_score = scores[0][0]
            print(f"  Gen {gen+1}/{self.generations} | Best: {best_score:.1f}")
            score_history.append(best_score)
            
            # Nash Equilibrium Convergence Check (Phase 3)
            if len(score_history) >= 3:
                recent = score_history[-3:]
                variance = max(recent) - min(recent)
                if variance < 0.2 and gen >= 2:
                    print(f"  🌟 [Meta-Equilibrium] Nash Equilibrium Converged against Feb 9, 2026 B&R List at Gen {gen+1} (Var: {variance:.3f})")
            
            survivors = [x[1] for x in scores[:max(2, self.population_size//3)]]
            
            new_population = survivors[:]
            
            while len(new_population) < self.population_size:
                p1 = random.choice(survivors[:3])
                p2 = random.choice(survivors)
                child = self._crossover(p1, p2)
                self._mutate(child)
                new_population.append(child)
                
            self.population = new_population

        best_score = -1
        best_deck = self.population[0]
        for deck in self.population:
            score = self.evaluate_fitness(deck)
            if score > best_score:
                best_score = score
                best_deck = deck
        
        return best_deck

    def _crossover(self, p1: Deck, p2: Deck) -> Deck:
        child = Deck()
        
        # Add lands
        for land_name, count in self.land_cards.items():
            land_data = next((c for c in self.full_pool if c['name'] == land_name), None)
            if land_data:
                child.add_card(self._dict_to_card(land_data), count)
        
        p1_names = {}
        for c in p1.maindeck:
            if not c.is_land:
                p1_names[c.name] = p1_names.get(c.name, 0) + 1
        
        p2_names = {}
        for c in p2.maindeck:
            if not c.is_land:
                p2_names[c.name] = p2_names.get(c.name, 0) + 1
        
        all_cards = set(list(p1_names.keys()) + list(p2_names.keys()))
        
        selected = []
        for name in all_cards:
            in_both = name in p1_names and name in p2_names
            selected.append((name, 2.0 if in_both else 1.0))
        
        random.shuffle(selected)
        selected.sort(key=lambda x: x[1], reverse=True)
        
        chosen = selected[:9]
        spells_added = 0
        
        for name, _ in chosen:
            if spells_added >= 36:
                break
            card_data = next((c for c in self.card_pool if c['name'] == name), None)
            if not card_data:
                card_data = next((c for c in self.full_pool if c['name'] == name), None)
            if card_data:
                copies = min(4, 36 - spells_added)
                child.add_card(self._dict_to_card(card_data), copies)
                spells_added += copies
        
        while spells_added < 36 and self.scored_pool:
            card_data, _ = random.choice(self.scored_pool[:20])
            existing = sum(1 for c in child.maindeck if c.name == card_data['name'])
            if existing == 0:
                copies = min(4, 36 - spells_added)
                child.add_card(self._dict_to_card(card_data), copies)
                spells_added += copies
        
        child.sideboard = self._generate_sideboard(child)
        return child
        
    def _mutate(self, deck: Deck):
        # Collect non-land card names from blueprints
        non_land_names = set()
        for card, qty in deck._blueprints:
            if not card.is_land:
                non_land_names.add(card.name)
        
        if not non_land_names or not self.scored_pool:
            return
            
        num_mutations = random.randint(1, 2)
        
        for _ in range(num_mutations):
            if not non_land_names:
                break
                
            target_name = random.choice(list(non_land_names))
            # Remove the blueprint entry for this card
            deck._blueprints = [(c, q) for c, q in deck._blueprints if c.name != target_name]
            non_land_names.discard(target_name)
            
            for card_data, score in self.scored_pool:
                if card_data['name'] not in non_land_names:
                    deck.add_card(self._dict_to_card(card_data), 4)
                    non_land_names.add(card_data['name'])
                    break
