"""anomaly_detector — Novel Play Sequence Detection.

Identifies statistically improbable win conditions and novel strategies
that deviate from established meta patterns.

Features:
- Win-condition sequence tracking per game
- Statistical anomaly flagging (Z-score deviation)
- Combo/synergy pattern discovery
- Meta comparison reports
"""

import json
import os
from typing import List, Dict, Set, Tuple, Optional
from collections import Counter, defaultdict
from datetime import datetime
import math


class AnomalyDetector:
    """Detects novel strategies and improbable win conditions."""
    
    def __init__(self):
        # Interaction frequency: (card_a, card_b) → count of games seen together
        self.interaction_freq: Counter = Counter()
        # Win condition sequences: list of card sequences that led to wins
        self.win_sequences: List[List[str]] = []
        # Meta baseline: deck archetype → win rate
        self.meta_baseline: Dict[str, float] = {}
        # Discovered anomalies
        self.anomalies: List[Dict] = []
        # Per-game event logs for pattern mining
        self.game_logs: List[Dict] = []
        self.total_games = 0
    
    def log_game(self, game_events: List[str], winner_deck: List[str],
                 loser_deck: List[str], winning_cards_played: List[str]):
        """Record a game's events for anomaly analysis.
        
        Args:
            game_events: Ordered list of game event strings
            winner_deck: Card names in the winning deck
            loser_deck: Card names in the losing deck  
            winning_cards_played: Cards the winner actually played
        """
        self.total_games += 1
        
        # Track card interactions (pairs of cards played together)
        for i, card_a in enumerate(winning_cards_played):
            for card_b in winning_cards_played[i+1:]:
                pair = tuple(sorted([card_a, card_b]))
                self.interaction_freq[pair] += 1
        
        # Track win sequence
        self.win_sequences.append(winning_cards_played)
        
        # Store log for deeper analysis
        self.game_logs.append({
            'timestamp': datetime.now().isoformat(),
            'events': game_events[-20:],  # Keep last 20 events
            'winner_cards': winner_deck[:5],  # Top 5 cards
            'winning_plays': winning_cards_played,
        })
    
    def detect_anomalies(self, min_games: int = 10) -> List[Dict]:
        """Run anomaly detection on collected data.
        
        Returns list of anomaly reports.
        """
        if self.total_games < min_games:
            return []
        
        new_anomalies = []
        
        # 1. Rare interaction anomalies: card pairs that win together but rarely appear
        new_anomalies.extend(self._detect_rare_synergies())
        
        # 2. Win-condition anomalies: unusual card sequences in final turns
        new_anomalies.extend(self._detect_unusual_win_conditions())
        
        # 3. Meta-deviation anomalies: decks winning with unusual strategies
        new_anomalies.extend(self._detect_meta_deviations())
        
        self.anomalies.extend(new_anomalies)
        return new_anomalies
    
    def _detect_rare_synergies(self) -> List[Dict]:
        """Find card pairs that have high win correlation but low frequency."""
        anomalies = []
        
        if not self.interaction_freq:
            return anomalies
        
        # Calculate mean and stddev of interaction frequencies
        counts = list(self.interaction_freq.values())
        if len(counts) < 5:
            return anomalies
        
        mean_freq = sum(counts) / len(counts)
        variance = sum((c - mean_freq) ** 2 for c in counts) / len(counts)
        std_freq = math.sqrt(variance) if variance > 0 else 1
        
        # Find low-frequency but winning pairs (Z-score < -1)
        for pair, count in self.interaction_freq.items():
            z_score = (count - mean_freq) / max(std_freq, 1)
            if z_score < -1.0 and count >= 2:
                anomalies.append({
                    'type': 'rare_synergy',
                    'cards': list(pair),
                    'frequency': count,
                    'z_score': round(z_score, 2),
                    'description': f"Rare synergy: {pair[0]} + {pair[1]} appeared {count} times "
                                  f"(z={z_score:.1f}) but led to wins",
                    'severity': 'medium' if z_score < -1.5 else 'low',
                })
        
        return anomalies
    
    def _detect_unusual_win_conditions(self) -> List[Dict]:
        """Find win conditions that deviate from typical patterns."""
        anomalies = []
        
        # Count how often each card appears in win sequences
        card_win_freq = Counter()
        for seq in self.win_sequences:
            for card in seq:
                card_win_freq[card] += 1
        
        if not card_win_freq:
            return anomalies
        
        # Find cards that appear in wins but are rare overall
        total = sum(card_win_freq.values())
        for card, count in card_win_freq.items():
            win_rate = count / max(len(self.win_sequences), 1)
            if win_rate > 0.3 and count >= 3:  # Present in 30%+ of wins
                anomalies.append({
                    'type': 'key_win_condition',
                    'card': card,
                    'win_presence_rate': round(win_rate, 3),
                    'appearances': count,
                    'description': f"Key win condition: {card} present in {win_rate:.0%} of wins",
                    'severity': 'high' if win_rate > 0.5 else 'medium',
                })
        
        return anomalies
    
    def _detect_meta_deviations(self) -> List[Dict]:
        """Find strategies that deviate significantly from the established meta."""
        anomalies = []
        
        if not self.meta_baseline:
            return anomalies
        
        # Compare win sequence archetypes against baseline
        for archetype, baseline_wr in self.meta_baseline.items():
            # Count wins by this archetype in our data
            archetype_wins = sum(1 for seq in self.win_sequences 
                                if archetype.lower() in str(seq).lower())
            our_wr = archetype_wins / max(len(self.win_sequences), 1)
            
            deviation = abs(our_wr - baseline_wr)
            if deviation > 0.15:  # 15%+ deviation from meta
                anomalies.append({
                    'type': 'meta_deviation',
                    'archetype': archetype,
                    'meta_win_rate': round(baseline_wr, 3),
                    'observed_win_rate': round(our_wr, 3),
                    'deviation': round(deviation, 3),
                    'description': f"Meta deviation: {archetype} expected {baseline_wr:.0%} "
                                  f"but observed {our_wr:.0%} ({deviation:.0%} off)",
                    'severity': 'high' if deviation > 0.25 else 'medium',
                })
        
        return anomalies
    
    def generate_report(self) -> str:
        """Generate a human-readable anomaly report."""
        lines = [
            "═══════════════════════════════════════════════",
            "  📊 ANOMALY DETECTION REPORT",
            "═══════════════════════════════════════════════",
            f"  Games analyzed: {self.total_games}",
            f"  Unique interactions: {len(self.interaction_freq)}",
            f"  Win sequences tracked: {len(self.win_sequences)}",
            f"  Anomalies detected: {len(self.anomalies)}",
            ""
        ]
        
        if self.anomalies:
            # Group by severity
            high = [a for a in self.anomalies if a.get('severity') == 'high']
            medium = [a for a in self.anomalies if a.get('severity') == 'medium']
            low = [a for a in self.anomalies if a.get('severity') == 'low']
            
            if high:
                lines.append("  🔴 HIGH SEVERITY:")
                for a in high[:5]:
                    lines.append(f"    • {a['description']}")
            
            if medium:
                lines.append("  🟡 MEDIUM SEVERITY:")
                for a in medium[:5]:
                    lines.append(f"    • {a['description']}")
            
            if low:
                lines.append("  🟢 LOW SEVERITY:")
                for a in low[:3]:
                    lines.append(f"    • {a['description']}")
        else:
            lines.append("  No anomalies detected yet. Need more game data.")
        
        lines.append("═══════════════════════════════════════════════")
        return "\n".join(lines)
    
    def save(self, path: str):
        """Save detector state to JSON."""
        data = {
            'total_games': self.total_games,
            'interaction_freq': dict(self.interaction_freq.most_common(1000)),
            'anomalies': self.anomalies,
            'meta_baseline': self.meta_baseline,
        }
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def load(self, path: str):
        """Load detector state from JSON."""
        with open(path, 'r') as f:
            data = json.load(f)
        self.total_games = data.get('total_games', 0)
        self.interaction_freq = Counter(data.get('interaction_freq', {}))
        self.anomalies = data.get('anomalies', [])
        self.meta_baseline = data.get('meta_baseline', {})
