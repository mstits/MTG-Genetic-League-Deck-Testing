"""Mulligan AI — Evaluates opening hands against Goldfish approximations.

This module contains a sub-network that looks at the opening 7 cards
and predicts the "Goldfish Win Turn" (how many turns it takes to win
with no opponent interaction).

If the predicted turn is worse than the baseline average for 6 cards,
the agent will decide to mulligan.
"""

import math
import random
import re
from typing import List
from engine.card import Card
from engine.deck import Deck

try:
    import numpy as np
except ImportError:
    import math
    import random as _r
    
    class ndarray(list):
        def astype(self, dtype):
            return self
            
        def __setitem__(self, key, value):
            if isinstance(key, tuple) and len(key) == 2:
                self[key[0]][key[1]] = value
            else:
                super().__setitem__(key, value)
            
        def __matmul__(self, other):
            # Matrix multiplication A @ B
            # 1D @ 1D -> scalar
            if not isinstance(self[0], (list, ndarray)) and not isinstance(other[0], (list, ndarray)):
                return sum(a * b for a, b in zip(self, other))
            # 2D @ 2D -> 2D
            if isinstance(self[0], (list, ndarray)) and isinstance(other[0], (list, ndarray)):
                res = []
                for i in range(len(self)):
                    row = []
                    for j in range(len(other[0])):
                        val = sum(self[i][k] * other[k][j] for k in range(len(self[0])))
                        row.append(val)
                    res.append(ndarray(row))
                return ndarray(res)
            # 1D @ 2D -> 1D
            if not isinstance(self[0], (list, ndarray)) and isinstance(other[0], (list, ndarray)):
                res = []
                for j in range(len(other[0])):
                    val = sum(self[k] * other[k][j] for k in range(len(self)))
                    res.append(val)
                return ndarray(res)
            # 2D @ 1D -> 1D
            if isinstance(self[0], (list, ndarray)) and not isinstance(other[0], (list, ndarray)):
                res = []
                for i in range(len(self)):
                    val = sum(self[i][k] * other[k] for k in range(len(other)))
                    res.append(val)
                return ndarray(res)
            return NotImplemented

        def __mul__(self, other):
            if isinstance(other, (int, float)):
                if isinstance(self[0], (list, ndarray)):
                    return ndarray([ndarray([v * other for v in row]) for row in self])
                return ndarray([v * other for v in self])
            # Element-wise fallback (simplified)
            return NotImplemented
            
        def __rmul__(self, other):
            return self.__mul__(other)
            
        def __sub__(self, other):
            if isinstance(other, (list, ndarray)):
                if isinstance(self[0], (list, ndarray)):
                    return ndarray([ndarray([a - b for a, b in zip(r1, r2)]) for r1, r2 in zip(self, other)])
                return ndarray([a - b for a, b in zip(self, other)])
            if isinstance(other, (int, float)):
                if isinstance(self[0], (list, ndarray)):
                    return ndarray([ndarray([v - other for v in row]) for row in self])
                return ndarray([v - other for v in self])
            return NotImplemented

        def __add__(self, other):
             if isinstance(other, (list, ndarray)):
                 if isinstance(self[0], (list, ndarray)):
                     return ndarray([ndarray([a + b for a, b in zip(r1, r2)]) for r1, r2 in zip(self, other)])
                 return ndarray([a + b for a, b in zip(self, other)])
             res = []
             if isinstance(other, (int, float)):
                  if isinstance(self[0], (list, ndarray)):
                      return ndarray([ndarray([v + other for v in row]) for row in self])
                  return ndarray([v + other for v in self])
             return NotImplemented
            
        def __truediv__(self, other):
            if isinstance(other, (int, float)):
                if isinstance(self[0], (list, ndarray)):
                    return ndarray([ndarray([v / other for v in row]) for row in self])
                return ndarray([v / other for v in self])
            if isinstance(other, (list, ndarray)):
                if isinstance(self[0], (list, ndarray)):
                    return ndarray([ndarray([a / b for a, b in zip(r1, r2)]) for r1, r2 in zip(self, other)])
                return ndarray([a / b for a, b in zip(self, other)])
            return NotImplemented
            
        def __rtruediv__(self, other):
             if isinstance(other, (int, float)):
                 if isinstance(self[0], (list, ndarray)):
                     return ndarray([ndarray([other / v for v in row]) for row in self])
                 return ndarray([other / v for v in self])
             return NotImplemented
            
        @property
        def T(self):
            if not isinstance(self[0], (list, ndarray)):
                return self
            return ndarray([ndarray([self[j][i] for j in range(len(self))]) for i in range(len(self[0]))])
            
        def flatten(self):
            if not isinstance(self[0], (list, ndarray)):
                return self
            res = []
            for row in self:
                res.extend(row)
            return ndarray(res)
            
        def reshape(self, *shape):
            if len(shape) == 1 and isinstance(shape[0], tuple):
                shape = shape[0]
            flat = self.flatten()
            if len(shape) == 2:
                if shape[0] == -1:
                    rows = len(flat) // shape[1]
                    cols = shape[1]
                else:
                    rows = shape[0]
                    cols = shape[1]
                res = []
                idx = 0
                for _ in range(rows):
                    res.append(ndarray(flat[idx:idx+cols]))
                    idx += cols
                return ndarray(res)
            return self # Fallback
            
        def astype(self, dtype):
            return self

    class _NpFallback:
        float32 = float
        ndarray = ndarray
        @staticmethod
        def zeros(shape, dtype=None):
            if isinstance(shape, tuple):
                return ndarray([ndarray([0.0] * shape[1]) for _ in range(shape[0])])
            return ndarray([0.0] * shape)
        @staticmethod
        def array(lst, dtype=None):
            if not lst: return ndarray([])
            if isinstance(lst[0], (list, tuple)):
                return ndarray([ndarray(r) for r in lst])
            return ndarray(lst)
        @staticmethod
        def exp(x):
            if isinstance(x, ndarray):
                if isinstance(x[0], ndarray):
                    return ndarray([ndarray([math.exp(v) for v in row]) for row in x])
                return ndarray([math.exp(v) for v in x])
            if isinstance(x, (list, tuple)):
                return ndarray([math.exp(v) for v in x])
            return math.exp(x)
        @staticmethod
        def clip(x, lo, hi):
            if isinstance(x, ndarray):
                if isinstance(x[0], ndarray):
                    return ndarray([ndarray([max(lo, min(hi, v)) for v in row]) for row in x])
                return ndarray([max(lo, min(hi, v)) for v in x])
            return max(lo, min(hi, x))
        @staticmethod
        def sqrt(x):
            return math.sqrt(x)
        @staticmethod
        def sum(x, axis=None, keepdims=False):
            if isinstance(x, ndarray):
                if isinstance(x[0], ndarray):
                    if axis == -1 or axis == 1:
                        res = ndarray([sum(row) for row in x])
                        if keepdims:
                            return ndarray([ndarray([v]) for v in res])
                        return res
                return sum(x)
            return sum(x) if isinstance(x, (list, tuple)) else x
        @staticmethod
        def max(x, axis=None, keepdims=False):
            if isinstance(x, ndarray):
                if isinstance(x[0], ndarray):
                    if axis == -1 or axis == 1:
                        res = ndarray([max(row) for row in x])
                        if keepdims:
                            return ndarray([ndarray([v]) for v in res])
                        return res
                return max(x)
            return max(x) if isinstance(x, (list, tuple)) else x
        class random:
            @staticmethod
            def randn(*shape):
                import random as _r
                if len(shape) == 2:
                    return ndarray([ndarray([_r.gauss(0,1) for _ in range(shape[1])]) for _ in range(shape[0])])
                return ndarray([_r.gauss(0,1) for _ in range(shape[0])])
        def savez(self, *a, **kw): pass
        def load(self, *a, **kw): return {}
    np = _NpFallback()


class TransformerMulliganNet:
    """A Transformer-based Neural Network to predict Goldfish Win Turn.
    
    Uses Multi-Head Attention to evaluate the synergy between the 7 opening cards.
    """
    
    def __init__(self, seq_len=7, embed_dim=16, num_heads=4):
        self.seq_len = seq_len
        self.embed_dim = embed_dim
        self.num_heads = num_heads
        
        # Linear projections for Q, K, V
        self.W_q = np.random.randn(embed_dim, embed_dim).astype(np.float32) * 0.1
        self.W_k = np.random.randn(embed_dim, embed_dim).astype(np.float32) * 0.1
        self.W_v = np.random.randn(embed_dim, embed_dim).astype(np.float32) * 0.1
        
        # Output projection
        self.W_o = np.random.randn(embed_dim, embed_dim).astype(np.float32) * 0.1
        
        # Dense layer to collapse sequence to single win-turn prediction
        self.W_out = np.random.randn(seq_len * embed_dim, 1).astype(np.float32) * 0.1
        self.b_out = np.zeros(1, dtype=np.float32)
        
    def _softmax(self, x: np.ndarray) -> np.ndarray:
        # Subtract max for numerical stability
        e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return e_x / np.sum(e_x, axis=-1, keepdims=True)

    def forward(self, x_seq: np.ndarray) -> float:
        """
        x_seq: shape (seq_len, embed_dim) representing the 7 cards.
        """
        # Q, K, V projections
        Q = x_seq @ self.W_q
        K = x_seq @ self.W_k
        V = x_seq @ self.W_v
        
        # Scaled Dot-Product Attention
        d_k = self.embed_dim // self.num_heads
        scores = (Q @ K.T) / np.sqrt(d_k)
        attn = self._softmax(scores)
        
        # Context vector
        context = attn @ V
        out_seq = context @ self.W_o
        
        # Flatten and predict
        flat = out_seq.flatten()
        pred = (flat @ self.W_out) + self.b_out
        
        # Output is expected goldfish turn (typically between 3 and 12)
        # Cap output logically between 1 and 20
        return float(np.clip(pred[0], 1.0, 20.0))
        
    def load(self, path: str):
        try:
            data = np.load(path)
            self.W_q = data['W_q']
            self.W_k = data['W_k']
            self.W_v = data['W_v']
            self.W_o = data['W_o']
            self.W_out = data['W_out']
            self.b_out = data['b_out']
        except Exception:
            pass
            
    def save(self, path: str):
        np.savez(path, W_q=self.W_q, W_k=self.W_k, W_v=self.W_v, W_o=self.W_o, W_out=self.W_out, b_out=self.b_out)
    
    def train_step(self, x_seq: np.ndarray, target: float, lr: float = 0.001) -> float:
        """Single SGD training step with MSE loss.
        
        Backpropagates through the attention mechanism and output layer.
        Returns the loss (squared error).
        """
        # ─── Forward pass (saving intermediates) ───
        Q = x_seq @ self.W_q
        K = x_seq @ self.W_k
        V = x_seq @ self.W_v
        
        d_k = self.embed_dim // self.num_heads
        scores = (Q @ K.T) / np.sqrt(d_k)
        attn = self._softmax(scores)
        context = attn @ V
        out_seq = context @ self.W_o
        
        flat = out_seq.flatten()
        raw = flat @ self.W_out.flatten() + self.b_out[0]
        pred = float(np.clip(raw, 1.0, 20.0))
        
        # ─── Loss ───
        error = pred - target
        loss = error ** 2
        
        # ─── Backward pass ───
        # Gradient of output layer
        d_pred = 2.0 * error  # d(loss)/d(pred)
        
        # Clip gradient to prevent explosion
        d_pred = np.clip(d_pred, -5.0, 5.0)
        
        # d(W_out): flat^T * d_pred
        d_W_out = flat.reshape(-1, 1) * d_pred
        d_b_out = np.array([d_pred], dtype=np.float32)
        
        # d(flat) → reshape to d(out_seq)
        d_flat = self.W_out.flatten() * d_pred
        d_out_seq = d_flat.reshape(self.seq_len, self.embed_dim)
        
        # d(W_o): context^T @ d_out_seq
        d_W_o = context.T @ d_out_seq
        d_context = d_out_seq @ self.W_o.T
        
        # d(V) through attention: attn^T @ d_context
        d_V = attn.T @ d_context
        
        # d(W_v): x_seq^T @ d_V
        d_W_v = x_seq.T @ d_V
        
        # Approximate d(W_q) and d(W_k) through attention scores
        # d(attn) @ V^T → d_scores (simplified)
        d_attn = d_context @ V.T
        d_scores = d_attn * attn * (1 - attn) / np.sqrt(d_k)  # softmax derivative approx
        
        d_Q = d_scores @ K
        d_K = d_scores.T @ Q
        
        d_W_q = x_seq.T @ d_Q
        d_W_k = x_seq.T @ d_K
        
        # ─── Update weights ───
        self.W_q -= lr * np.clip(d_W_q, -1.0, 1.0)
        self.W_k -= lr * np.clip(d_W_k, -1.0, 1.0)
        self.W_v -= lr * np.clip(d_W_v, -1.0, 1.0)
        self.W_o -= lr * np.clip(d_W_o, -1.0, 1.0)
        self.W_out -= lr * np.clip(d_W_out, -1.0, 1.0)
        self.b_out -= lr * d_b_out
        
        return loss


class MulliganAI:
    """Wrapper for the TransformerMulliganNet model with training/inference logic.
    
    Handles vectorizing MTG hands, estimating goldfish win turns with a heuristic,
    and predicting them against unseen hands using the trained Transformer.
    """
    def __init__(self, model_path=None):
        self.net = TransformerMulliganNet()
        if model_path:
            self.net.load(model_path)
            
    def _vectorize_hand(self, hand: List[Card], deck: Deck) -> np.ndarray:
        """Convert a hand into a sequence of card vectors for Transformer.
        
        Returns:
            np.ndarray of shape (7, 16) - seq_len=7 hand cards, embed_dim=16 attributes.
        """
        seq = np.zeros((7, self.net.embed_dim), dtype=np.float32)
        
        from engine.player import Player
        
        # Compute deck land ratio once (not per card!)
        deck_cards = deck.maindeck
        land_ratio = sum(1 for c in deck_cards if c.is_land) / max(len(deck_cards), 1)
        
        for i, card in enumerate(hand[:7]):
            # 0: is_land, 1: cmc, 2: power, 3: toughness
            seq[i, 0] = 1.0 if card.is_land else 0.0
            seq[i, 1] = Player._parse_cmc(card.cost) if card.cost else 0.0
            seq[i, 2] = card.power or 0.0
            seq[i, 3] = card.toughness or 0.0
            
            # 4-8: WUBRG requirements (simplified)
            seq[i, 4] = 1.0 if 'W' in card.color_identity else 0.0
            seq[i, 5] = 1.0 if 'U' in card.color_identity else 0.0
            seq[i, 6] = 1.0 if 'B' in card.color_identity else 0.0
            seq[i, 7] = 1.0 if 'R' in card.color_identity else 0.0
            seq[i, 8] = 1.0 if 'G' in card.color_identity else 0.0
            
            # 9-11: Types
            seq[i, 9] = 1.0 if card.is_creature else 0.0
            seq[i, 10] = 1.0 if card.is_instant or card.is_sorcery else 0.0
            seq[i, 11] = 1.0 if card.is_removal else 0.0
            
            # 12: Deck land ratio (shared context)
            seq[i, 12] = land_ratio
            
            # 13: Evasion (flying, menace, trample, unblockable)
            seq[i, 13] = 1.0 if (card.has_flying or card.has_menace or 
                                 card.has_trample or card.is_unblockable) else 0.0
            
            # 14: Interaction (removal, counter, burn)
            seq[i, 14] = 1.0 if (card.is_removal or card.is_counter or card.is_burn) else 0.0
            
            # 15: Mana dork / ramp
            seq[i, 15] = 1.0 if getattr(card, 'is_mana_dork', False) else 0.0
            
        return seq
        
    def heuristic_goldfish_turn(self, hand: List[Card], deck_archetype: str = None) -> float:
        """A non-neural heuristic to estimate goldfish given the hand.
        
        T6: Enhanced with archetype-specific requirements.
        """
        lands = sum(1 for c in hand if c.is_land)
        if lands <= 1 or lands >= len(hand):
            return 99.0  # Unplayable
            
        from engine.player import Player
        cmcs = [Player._parse_cmc(c.cost) for c in hand if not c.is_land and c.cost]
        
        # Ideal curve is having a 1-drop, 2-drop, 3-drop
        score = 10.0
        if 1 in cmcs: score -= 1.0
        if 2 in cmcs: score -= 1.5
        if 3 in cmcs: score -= 1.0
        
        # Too many high drops we can't cast
        uncastable = sum(1 for cmc in cmcs if cmc > lands + 2)
        score += uncastable * 1.5
        
        # Card quality bonuses
        spells = [c for c in hand if not c.is_land]
        for c in spells:
            if c.is_removal or c.is_counter: score -= 0.3
            if c.is_discard: score -= 0.4
            if getattr(c, 'is_mana_dork', False): score -= 0.5
            if c.is_creature and (c.has_flying or c.has_menace or c.is_unblockable):
                score -= 0.3
            if c.has_deathtouch or c.has_first_strike or c.has_double_strike:
                score -= 0.2
            if c.has_haste: score -= 0.2
            if c.has_lifelink: score -= 0.15
            if c.etb_effect: score -= 0.2
            if c.is_planeswalker: score -= 0.5
            if getattr(c, 'tap_ability_effect', None): score -= 0.3
            if c.is_draw: score -= 0.2
            if c.has_flash: score -= 0.15
            if c.is_board_wipe: score += 0.5
        
        # Land count sweet spot: 2-3 lands is ideal
        if lands in (2, 3): score -= 0.5
        elif lands == 1: score += 0.5
        elif lands in (4, 5): score += 0.5
        
        # Color-mana matching
        land_colors = set()
        for c in hand:
            if c.is_land:
                for color in getattr(c, 'produces', []):
                    land_colors.add(color)
        needed_colors = set()
        for c in spells:
            if c.cost:
                for color in re.findall(r'\{([WUBRG])\}', c.cost):
                    needed_colors.add(color)
        missing = needed_colors - land_colors
        if missing:
            score += len(missing) * 0.5
        
        # ── T6: Archetype-Weighted Mulligan Requirements ─────────────
        if deck_archetype:
            arch = deck_archetype.lower()
        else:
            # Auto-detect archetype from hand composition
            creature_count = sum(1 for c in spells if c.is_creature)
            interaction_count = sum(1 for c in spells if c.is_removal or c.is_counter or c.is_burn)
            arch = 'aggro' if creature_count >= len(spells) * 0.6 else ('control' if interaction_count >= len(spells) * 0.4 else 'midrange')
        
        if arch == 'aggro':
            # Aggro MUST have at least one spell with CMC <= 2
            has_early_play = any(cmc <= 2 for cmc in cmcs)
            if not has_early_play:
                score += 2.5  # Heavy penalty — no early pressure
            # No plays before T3 is auto-mull for aggro
            if not any(cmc <= 2 for cmc in cmcs) and lands < 3:
                return 99.0
        elif arch == 'control':
            # Control MUST have at least one interaction piece
            has_interaction = any(c.is_counter or c.is_removal or c.is_draw or c.is_burn for c in spells)
            if not has_interaction:
                score += 2.0  # Penalty — no answers
        
        return max(3.0, score)

    def evaluate_hand(self, hand: List[Card], deck: Deck) -> float:
        """Returns the predicted Goldfish Win Turn."""
        vec = self._vectorize_hand(hand, deck)
        # Use neural net prediction shifted by heuristic
        nn_pred = self.net.forward(vec)
        heur_pred = self.heuristic_goldfish_turn(hand)
        
        # Ensemble average for stability
        return (nn_pred * 0.2) + (heur_pred * 0.8)

    def should_mulligan(self, hand: List[Card], deck: Deck, mulligan_count: int, meta_archetype: str = "Midrange") -> 'Tuple[bool, str]':
        """Decide whether to mulligan based on predicted goldfish performance against Meta.
        
        Enhanced with granular matchup-specific thresholds and interaction-awareness.
        
        Returns:
            (bool, str): Whether to mulligan and the explanation.
        """
        if mulligan_count >= 3:
            return False, "Never mulligan below 4 cards (Risk of passing turn 1 too high)"
            
        predicted_turn = self.evaluate_hand(hand, deck)
        
        # Matchup-specific thresholds (lower = need faster hand)
        thresholds = {
            "Aggro": 4.5,      # Need early interaction or fast clock
            "Burn": 4.0,       # Must interact by turn 2 or race
            "Control": 6.5,    # Card quantity matters more than speed
            "Combo": 3.8,      # Must interact FAST or win first
            "Ramp": 5.0,       # Need to punish before they go over the top
            "Midrange": 5.5,   # Standard threshold
            "Tempo": 4.8,      # Need cheap efficient plays
            "Tokens": 5.0,     # Need sweepers or go-wide answers
        }
        base_acceptable = thresholds.get(meta_archetype, 5.5)
        acceptable_turn = base_acceptable + (mulligan_count * 1.2)
        
        # Interaction bonus: hands with early interaction are keepable even if slow
        interaction_count = sum(1 for c in hand if 
                               c.is_removal or c.is_counter or c.is_burn or 
                               getattr(c, 'is_board_wipe', False))
        if meta_archetype in ("Combo", "Burn") and interaction_count >= 2:
            acceptable_turn += 1.5  # Interaction-heavy hands are great vs combo/burn
        elif meta_archetype in ("Aggro", "Tokens") and interaction_count >= 1:
            acceptable_turn += 0.5  # Some interaction helps stabilize
        
        is_mull = predicted_turn > acceptable_turn
        
        lands = sum(1 for c in hand if c.is_land)
        reasons = []
        
        if lands <= 0:
            reasons.append(f"{lands} lands in hand")
        elif lands >= len(hand) - 1 and len(hand) >= 5:
            reasons.append(f"Flooded ({lands} lands)")
        elif lands == 1 and predicted_turn > 5:
            reasons.append("Only 1 land and no cheap interaction")
            
        if is_mull:
            if not reasons:
                reasons.append(f"Too slow vs {meta_archetype} (Speed: T{predicted_turn:.1f}, Min Req: T{acceptable_turn:.1f})")
            else:
                reasons.append(f"Too slow (Speed: T{predicted_turn:.1f})")
            return True, "Mulligan: " + " + ".join(reasons)
        else:
            if not reasons:
                reasons.append(f"Curve is acceptable vs {meta_archetype}")
            reasons.append(f"Speed: T{predicted_turn:.1f}")
            if interaction_count >= 2:
                reasons.append(f"{interaction_count} interaction pieces")
            return False, "Keep: " + " + ".join(reasons)

    def train_from_decks(self, decks: List[Deck], epochs: int = 10, 
                          samples_per_deck: int = 50, lr: float = 0.001,
                          save_path: str = None) -> dict:
        """Train the neural network on sampled hands from decks.
        
        Generates training data by:
        1. For each deck, sample random 7-card hands
        2. Compute heuristic goldfish turn as target label
        3. Vectorize hand and train via SGD
        
        Args:
            decks: List of Deck objects to sample from
            epochs: Number of training epochs
            samples_per_deck: Hands to sample per deck per epoch
            lr: Learning rate
            save_path: Optional path to save trained weights
            
        Returns:
            dict with training stats (losses per epoch, final MSE)
        """
        
        # Generate training data
        training_data = []
        for deck in decks:
            cards = deck.get_game_deck()
            if len(cards) < 7:
                continue
            for _ in range(samples_per_deck):
                hand = random.sample(cards, 7)
                vec = self._vectorize_hand(hand, deck)
                target = self.heuristic_goldfish_turn(hand)
                if target < 50:  # Skip unplayable hands
                    training_data.append((vec, target))
        
        if not training_data:
            return {'epochs': 0, 'final_mse': 0, 'samples': 0}
        
        # Normalize targets to [0, 1] range for training stability
        targets = [t for _, t in training_data]
        t_min, t_max = min(targets), max(targets)
        t_range = max(t_max - t_min, 1.0)
        
        epoch_losses = []
        
        for epoch in range(epochs):
            random.shuffle(training_data)
            epoch_loss = 0.0
            
            # Learning rate decay
            current_lr = lr * (0.95 ** epoch)
            
            for vec, target in training_data:
                # Normalize target
                norm_target = (target - t_min) / t_range * 15.0 + 3.0  # Scale to [3, 18]
                loss = self.net.train_step(vec, norm_target, current_lr)
                epoch_loss += loss
            
            avg_loss = epoch_loss / len(training_data)
            epoch_losses.append(avg_loss)
        
        # Save if requested
        if save_path:
            self.net.save(save_path)
        
        return {
            'epochs': epochs,
            'samples': len(training_data),
            'final_mse': epoch_losses[-1] if epoch_losses else 0,
            'loss_history': epoch_losses
        }
