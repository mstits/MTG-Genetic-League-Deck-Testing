"""Flex Tester — A/B testing framework for MTG deck optimization.

Generates combinatorics of a fixed "core" deck and a "flex pool" 
to discover the mathematically optimal 60-card configuration against the Gauntlet.
"""

import itertools
from collections import Counter
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from simulation.runner import SimulationRunner
from agents.heuristic_agent import HeuristicAgent
from league.gauntlet import Gauntlet
from engine.card_builder import dict_to_card, inject_basic_lands
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed


class FlexTester:
    def __init__(self, core_deck: dict, flex_pool: list, target_size: int = 60, target_matches_per_config: int = 15):
        """
        core_deck: dict of {card_name: count} (e.g., {"Lightning Bolt": 4, "Mountain": 16})
        flex_pool: list of card names to consider extending the core deck with
        target_size: the required deck size (usually 60)
        """
        self.core_deck = core_deck
        self.flex_pool = flex_pool
        self.target_size = target_size
        self.target_matches = target_matches_per_config
        
        self.core_count = sum(core_deck.values())
        self.slots_to_fill = target_size - self.core_count
        
        if self.slots_to_fill < 0:
            raise ValueError("Core deck already exceeds target size.")
            
        self._load_cards()
        self.gauntlet = Gauntlet()
        self.bosses = self.gauntlet.bosses
        
    def _load_cards(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_path = os.path.join(base_dir, 'data', 'legal_cards.json')
        if not os.path.exists(data_path):
            data_path = os.path.join(base_dir, 'data', 'processed_cards.json')
            
        with open(data_path, 'r') as f:
            cards = json.load(f)
            self.card_data = {c['name']: c for c in cards}
            inject_basic_lands(self.card_data)

    def generate_configurations(self):
        """Yield valid decklist dicts that satisfy rules (max 4 of each card, except basics)."""
        if self.slots_to_fill == 0:
            yield self.core_deck
            return
            
        # Combinations with replacement to fill slots
        for flex_combo in itertools.combinations_with_replacement(self.flex_pool, self.slots_to_fill):
            flex_counts = Counter(flex_combo)
            
            # Check 4-of rule (considering core deck too)
            valid = True
            for c_name, flex_q in flex_counts.items():
                if not 'Land' in self.card_data.get(c_name, {}).get('type_line', ''):
                    total_q = self.core_deck.get(c_name, 0) + flex_q
                    if total_q > 4:
                        valid = False
                        break
                        
            if valid:
                merged = dict(self.core_deck)
                for k, v in flex_counts.items():
                    merged[k] = merged.get(k, 0) + v
                yield merged

    def _make_deck(self, card_dict):
        deck = Deck()
        for name, count in card_dict.items():
            if name in self.card_data:
                deck.add_card(dict_to_card(self.card_data[name]), count)
        return deck

    def run_tests(self):
        """Run Gauntlet matches for all generated configurations and return ranked results.
        
        T12: Now includes sample_size and confidence_interval per configuration.
        """
        configs = list(self.generate_configurations())
        
        # Limit to avoid overloading
        if len(configs) > 100:
            configs = configs[:100]
            
        results = []
        
        for config_idx, config in enumerate(configs):
            candidate_deck = self._make_deck(config)
            
            w, l, d = 0, 0, 0
            for boss in self.bosses:
                boss_deck = self._make_deck(boss['cards'])
                games_per_boss = max(1, self.target_matches // len(self.bosses))
                
                for _ in range(games_per_boss):
                    p1 = Player(f"Candidate C{config_idx}", candidate_deck)
                    p2 = Player(f"{boss['name']}", boss_deck)
                    game = Game([p1, p2])
                    runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                    try:
                        res = runner.run()
                        if res.winner == p1.name: w += 1
                        elif res.winner == p2.name: l += 1
                        else: d += 1
                    except Exception as e:
                        d += 1
                        
            total = w + l + d
            win_rate = (w / total) * 100 if total > 0 else 0
            
            # T12: Calculate Wilson score confidence interval (95%)
            import math
            n = total
            if n > 0:
                p_hat = w / n
                z = 1.96  # 95% confidence
                denom = 1 + z**2 / n
                center = (p_hat + z**2 / (2 * n)) / denom
                spread = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
                ci_low = max(0, center - spread) * 100
                ci_high = min(1, center + spread) * 100
            else:
                ci_low, ci_high = 0, 0
            
            flex_diff = {k: v - self.core_deck.get(k, 0) for k, v in config.items() if v > self.core_deck.get(k, 0)}
            
            results.append({
                "config_id": config_idx,
                "flex_cards": flex_diff,
                "win_rate": round(win_rate, 1),
                "record": f"{w}W-{l}L-{d}D",
                "sample_size": total,
                "confidence_interval": f"{ci_low:.1f}%-{ci_high:.1f}%"
            })
            
        results.sort(key=lambda x: x['win_rate'], reverse=True)
        return results
