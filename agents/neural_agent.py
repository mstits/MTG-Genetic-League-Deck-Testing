"""NeuralAgent — Deep learning agent using policy/value networks with MCTS.

Replaces MCTS rollout heuristics with a trained neural network that:
1. Policy head: outputs probability distribution over legal actions
2. Value head: estimates win probability from game state

Training pipeline:
    1. Self-play generates (state_vector, action_taken, game_outcome) tuples
    2. Supervised training on collected data
    3. Policy distillation from MCTS visit counts
"""

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    import math as _math
    class _NpFallback:
        float32 = float
        @staticmethod
        def zeros(shape, dtype=None):
            if isinstance(shape, tuple):
                return [[0.0]*shape[1] for _ in range(shape[0])]
            return [0.0]*shape
        @staticmethod
        def array(lst, dtype=None):
            return list(lst)
        class random:
            @staticmethod
            def randn(*shape):
                import random as _r
                if len(shape) == 1:
                    return [_r.gauss(0, 1) for _ in range(shape[0])]
                return [[_r.gauss(0, 1) for _ in range(shape[1])] for _ in range(shape[0])]
            @staticmethod
            def choice(n, p=None):
                import random as _r
                if p:
                    r = _r.random()
                    cumsum = 0
                    for i, prob in enumerate(p):
                        cumsum += prob
                        if r <= cumsum:
                            return i
                    return len(p) - 1
                return _r.randint(0, n-1)
        @staticmethod
        def sqrt(x):
            return _math.sqrt(x) if isinstance(x, (int, float)) else x
        @staticmethod
        def exp(x):
            if isinstance(x, list):
                return [_math.exp(v) for v in x]
            return _math.exp(x)
        @staticmethod
        def maximum(a, b):
            if isinstance(b, list):
                return [max(a, v) for v in b]
            return max(a, b)
        @staticmethod
        def tanh(x):
            if isinstance(x, list):
                return [_math.tanh(v) for v in x]
            return _math.tanh(x)
        @staticmethod
        def argmax(x):
            return x.index(max(x))
        @staticmethod
        def outer(a, b):
            return [[ai * bi for bi in b] for ai in a]
    np = _NpFallback()

import random
import math
import os
import json
from typing import Dict, List, Any, Tuple, Optional
from agents.base_agent import BaseAgent
from agents.heuristic_agent import HeuristicAgent


class SimpleNeuralNet:
    """Lightweight neural network (no PyTorch/TF dependency).
    
    Architecture: State → FC(276→512) → ReLU → FC(512→256) → ReLU →
                  Policy: FC(256→action_dim) → Softmax
                  Value:  FC(256→1) → Tanh
    
    Uses numpy-only forward pass for inference. Weights can be loaded
    from a trained PyTorch model or trained in-process via simple SGD.
    """
    
    def __init__(self, state_dim=276, hidden_dim=512, hidden_dim2=256):
        self.state_dim = state_dim
        self.hidden_dim = hidden_dim
        self.hidden_dim2 = hidden_dim2
        
        # Initialize weights with Xavier initialization
        self.W1 = np.random.randn(state_dim, hidden_dim).astype(np.float32) * np.sqrt(2.0 / state_dim)
        self.b1 = np.zeros(hidden_dim, dtype=np.float32)
        self.W2 = np.random.randn(hidden_dim, hidden_dim2).astype(np.float32) * np.sqrt(2.0 / hidden_dim)
        self.b2 = np.zeros(hidden_dim2, dtype=np.float32)
        
        # Value head
        self.Wv = np.random.randn(hidden_dim2, 1).astype(np.float32) * np.sqrt(2.0 / hidden_dim2)
        self.bv = np.zeros(1, dtype=np.float32)
        
        # Policy head (outputs raw action scores — softmax applied externally per legal actions)
        self.Wp = np.random.randn(hidden_dim2, 20).astype(np.float32) * np.sqrt(2.0 / hidden_dim2)
        self.bp = np.zeros(20, dtype=np.float32)
    
    def forward(self, state_vec: np.ndarray) -> Tuple[np.ndarray, float]:
        """Forward pass returning (policy_logits, value).
        
        Args:
            state_vec: (276,) game state vector
        
        Returns:
            (policy_logits (20,), value scalar in [-1, 1])
        """
        # Hidden layer 1
        h1 = np.maximum(0, state_vec @ self.W1 + self.b1)  # ReLU
        # Hidden layer 2
        h2 = np.maximum(0, h1 @ self.W2 + self.b2)  # ReLU
        # Value head
        value = np.tanh(h2 @ self.Wv + self.bv)[0]
        # Policy head
        policy_logits = h2 @ self.Wp + self.bp
        
        return policy_logits, value
    
    def save(self, path: str):
        """Save weights to numpy file."""
        np.savez(path,
                 W1=self.W1, b1=self.b1,
                 W2=self.W2, b2=self.b2,
                 Wv=self.Wv, bv=self.bv,
                 Wp=self.Wp, bp=self.bp)
    
    def load(self, path: str):
        """Load weights from numpy file."""
        data = np.load(path)
        self.W1 = data['W1']
        self.b1 = data['b1']
        self.W2 = data['W2']
        self.b2 = data['b2']
        self.Wv = data['Wv']
        self.bv = data['bv']
        self.Wp = data['Wp']
        self.bp = data['bp']


class NeuralMCTSNode:
    """MCTS node using neural network evaluation."""
    
    def __init__(self, state, player_idx: int, prior: float = 0.0,
                 action: Dict = None, parent=None):
        self.state = state
        self.player_idx = player_idx
        self.action = action
        self.parent = parent
        self.children = []
        self.visits = 0
        self.total_value = 0.0
        self.prior = prior  # From policy network
        self.untried_actions = None
    
    @property
    def q_value(self) -> float:
        return self.total_value / max(self.visits, 1)
    
    def puct_score(self, c_puct=1.5) -> float:
        """PUCT selection (AlphaZero-style)."""
        if self.parent is None:
            return 0
        exploration = c_puct * self.prior * math.sqrt(self.parent.visits) / (1 + self.visits)
        return self.q_value + exploration


class NeuralAgent(BaseAgent):
    """Neural MCTS Agent: AlphaZero-inspired with policy/value network.
    
    Combines neural network evaluation with MCTS tree search:
    - Policy network guides tree expansion (prior probabilities)
    - Value network evaluates leaf nodes (replaces rollout)
    - PUCT selection balances exploration vs exploitation
    """
    
    def __init__(self, model: SimpleNeuralNet = None, max_iterations: int = 50,
                 c_puct: float = 1.5, temperature: float = 1.0):
        self.model = model or SimpleNeuralNet()
        self.max_iterations = max_iterations
        self.c_puct = c_puct
        self.temperature = temperature
        self.fallback_agent = HeuristicAgent()
        
        # Training data collection
        self.training_data: List[Tuple[np.ndarray, int, float]] = []
    
    def get_action(self, game, player) -> Dict:
        """Select action using Neural MCTS."""
        from engine.game_state_vector import vectorize_game_state, vectorize_actions
        
        raw_legal = game.get_legal_actions()
        if not raw_legal:
            return {'type': 'pass'}
        if len(raw_legal) == 1:
            return raw_legal[0]
        
        player_idx = game.players.index(player)
        
        # Get neural evaluation of current state
        state_vec = vectorize_game_state(game, player_idx)
        policy_logits, value = self.model.forward(state_vec)
        
        # Create root node (determinized for partial observability)
        root = NeuralMCTSNode(state=game.clone(determinize_for_player=player), player_idx=player_idx)
        
        # Expand root using policy network
        action_features = vectorize_actions(game, raw_legal, player_idx)
        action_scores = action_features @ policy_logits[:action_features.shape[1]]
        
        # Softmax to get priors
        exp_scores = np.exp(action_scores - np.max(action_scores))
        priors = exp_scores / (exp_scores.sum() + 1e-8)
        
        root.untried_actions = list(zip(raw_legal, priors))
        
        # Run MCTS iterations
        for _ in range(self.max_iterations):
            node = self._select(root)
            
            if node.state.game_over:
                reward = self._terminal_value(node.state, player_idx)
            elif node.untried_actions:
                node = self._expand(node, player_idx)
                reward = self._evaluate(node.state, player_idx)
            else:
                reward = self._evaluate(node.state, player_idx)
            
            self._backpropagate(node, reward)
        
        if not root.children:
            # Fallback to heuristic if MCTS didn't expand
            return self.fallback_agent.get_action(game, player)
        
        # Select action by visit count (with temperature)
        visits = np.array([c.visits for c in root.children], dtype=np.float32)
        if self.temperature < 0.01:
            # Greedy
            best_idx = np.argmax(visits)
        else:
            # Temperature-weighted sampling
            visit_probs = visits ** (1.0 / self.temperature)
            visit_probs /= visit_probs.sum()
            best_idx = np.random.choice(len(root.children), p=visit_probs)
        
        # Store training data: (state, action_idx, eventual_outcome)
        self.training_data.append((state_vec, best_idx, 0.0))  # Outcome filled later
        
        return root.children[best_idx].action
    
    def _select(self, node: NeuralMCTSNode) -> NeuralMCTSNode:
        """Select node to expand using PUCT."""
        while not node.state.game_over:
            if node.untried_actions is None:
                actions = node.state.get_legal_actions()
                if not actions:
                    break
                # Get priors from policy network
                from engine.game_state_vector import vectorize_game_state, vectorize_actions
                state_vec = vectorize_game_state(node.state, node.player_idx)
                policy_logits, _ = self.model.forward(state_vec)
                action_features = vectorize_actions(node.state, actions, node.player_idx)
                scores = action_features @ policy_logits[:action_features.shape[1]]
                exp_s = np.exp(scores - np.max(scores))
                priors = exp_s / (exp_s.sum() + 1e-8)
                node.untried_actions = list(zip(actions, priors))
            
            if node.untried_actions:
                return node
            
            if not node.children:
                return node
            
            node = max(node.children, key=lambda c: c.puct_score(self.c_puct))
        
        return node
    
    def _expand(self, node: NeuralMCTSNode, player_idx: int) -> NeuralMCTSNode:
        """Expand one untried action."""
        action, prior = node.untried_actions.pop()
        new_state = node.state.clone()
        new_state.apply_action(action)
        
        child = NeuralMCTSNode(
            state=new_state, player_idx=player_idx,
            prior=prior, action=action, parent=node
        )
        node.children.append(child)
        return child
    
    def _evaluate(self, state, player_idx: int) -> float:
        """Evaluate state using value network (no rollout needed)."""
        from engine.game_state_vector import vectorize_game_state
        state_vec = vectorize_game_state(state, player_idx)
        _, value = self.model.forward(state_vec)
        return (value + 1.0) / 2.0  # Map [-1,1] → [0,1]
    
    def _terminal_value(self, state, player_idx: int) -> float:
        """Get value of terminal state."""
        if state.winner == state.players[player_idx]:
            return 1.0
        elif state.winner is None:
            return 0.5
        return 0.0
    
    def _backpropagate(self, node: NeuralMCTSNode, reward: float):
        """Backpropagate value up the tree."""
        while node is not None:
            node.visits += 1
            node.total_value += reward
            node = node.parent
    
    def update_training_outcomes(self, won: bool):
        """After a game ends, update all collected training data with the outcome."""
        outcome = 1.0 if won else 0.0
        for i in range(len(self.training_data)):
            state, action_idx, _ = self.training_data[i]
            self.training_data[i] = (state, action_idx, outcome)
    
    def train_batch(self, lr: float = 0.001):
        """Simple SGD training step on collected self-play data."""
        if not self.training_data:
            return
        
        for state_vec, action_idx, outcome in self.training_data:
            # Forward pass
            h1 = np.maximum(0, state_vec @ self.model.W1 + self.model.b1)
            h2 = np.maximum(0, h1 @ self.model.W2 + self.model.b2)
            value = np.tanh(h2 @ self.model.Wv + self.model.bv)[0]
            
            # Value loss gradient (MSE)
            target = outcome * 2 - 1  # Map [0,1] → [-1,1]
            value_loss = value - target
            
            # Backprop through value head (simplified)
            dWv = np.outer(h2, np.array([value_loss * (1 - value**2)]))
            self.model.Wv -= lr * dWv
            self.model.bv -= lr * np.array([value_loss * (1 - value**2)])
        
        self.training_data.clear()
