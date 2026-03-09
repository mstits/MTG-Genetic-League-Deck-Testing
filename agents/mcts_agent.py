"""MCTSAgent — Pro-Level AI agent leveraging Monte Carlo Tree Search.

Builds a local game tree to evaluate immediate tactical lines, performing
truncated rollouts using the HeuristicAgent to simulate 2-3 turns ahead before
falling back on a board-state/life-differential heuristic evaluation.
"""

import math
import random
from typing import Dict, List, Any
from .base_agent import BaseAgent
from .heuristic_agent import HeuristicAgent

class MCTSNode:
    """A node in the MCTS tree storing state and UCT statistics."""
    def __init__(self, state, player_idx: int, action: Dict = None, parent=None):
        self.state = state  # The Game state at this node
        self.player_idx = player_idx
        self.action = action
        self.parent = parent
        self.children = []
        self.visits = 0
        self.wins = 0.0
        
        # We only generate untried actions when expanding
        self.untried_actions = None

    def uct_score(self, c_param=1.414) -> float:
        """Calculate the Upper Confidence Bound applied to Trees (UCT) formula."""
        if self.visits == 0:
            return float('inf')
        return (self.wins / self.visits) + c_param * math.sqrt(math.log(self.parent.visits) / self.visits)

class MCTSAgent(BaseAgent):
    """Pro-Level AI Agent using Monte Carlo Tree Search.
    Builds a game tree and simulates ahead using HeuristicAgent.
    """
    def __init__(self, max_iterations=200, max_rollout_depth=8):
        self.max_iterations = max_iterations
        self.max_rollout_depth = max_rollout_depth
        self.rollout_agent = HeuristicAgent()

    def _expand_macro_actions(self, game, player, actions):
        """Converts generic macro actions (attackers, blockers) into concrete options for the tree search."""
        import itertools
        concrete = []
        opp = game.players[(game.players.index(player) + 1) % 2]
        for a in actions:
            if a['type'] == 'declare_attackers':
                candidates = a.get('candidates', [])
                # Generate powerset of all valid attackers for PERFECT MCTS EXPLORATION
                for r in range(len(candidates) + 1):
                    for subset in itertools.combinations(candidates, r):
                        concrete.append({'type': 'declare_attackers', 'attackers': list(subset)})
            elif a['type'] == 'declare_blockers':
                # 1. No blocks
                concrete.append({'type': 'declare_blockers', 'blocks': {}})
                # 2. Heuristic smart blocks (Doing full 2^N * 2^M for blockers is too complex for MCTS depth, defer to heuristic)
                h_blk = self.rollout_agent._calculate_blocks(game, player, a.get('candidates', []), a.get('attackers', []))
                if h_blk:
                    concrete.append({'type': 'declare_blockers', 'blocks': h_blk})
            else:
                concrete.append(a)
        
        # Deduplicate actions
        unique = []
        for c in concrete:
            if c not in unique: 
                unique.append(c)
        return unique

    def get_action(self, game, player) -> Dict:
        """Selects the best action by running MCTS simulations and picking the most visited root child."""
        raw_legal = game.get_legal_actions()
        legal_actions = self._expand_macro_actions(game, player, raw_legal)
        
        if not legal_actions:
            return {'type': 'pass'}
        # Instantly play forced moves to save compute
        if len(legal_actions) == 1:
            return legal_actions[0]

        player_idx = game.players.index(player)
        # ISMCTS: determinize hidden information for the searching player
        root_state = game.clone(determinize_for_player=player)
        root = MCTSNode(state=root_state, player_idx=player_idx)
        root.untried_actions = self._expand_macro_actions(root.state, root.state.players[player_idx], root.state.get_legal_actions())

        for _ in range(self.max_iterations):
            node = self._tree_policy(root)
            reward = self._default_policy(node.state, player_idx)
            self._backpropagate(node, reward)

        if not root.children:
            return random.choice(legal_actions)

        # Robust child selection: Max visits implies highest confidence and reward.
        best_child = max(root.children, key=lambda c: c.visits)
        return best_child.action

    def _tree_policy(self, node: MCTSNode) -> MCTSNode:
        """Selects or expands a node balancing Exploration (C) vs Exploitation (Wins)."""
        while not node.state.game_over:
            if node.untried_actions is None:
                node.untried_actions = node.state.get_legal_actions()
                
            if len(node.untried_actions) > 0:
                return self._expand(node)
            else:
                if not node.children:
                    return node # Terminal or stuck
                node = max(node.children, key=lambda c: c.uct_score())
        return node
        
    def _expand(self, node: MCTSNode) -> MCTSNode:
        """Pops an untried action and creates a new simulated state instance."""
        action = node.untried_actions.pop()
        
        # ISMCTS: each expansion re-determinizes for partial observability
        new_state = node.state.clone(
            determinize_for_player=node.state.players[node.player_idx]
        )
        new_state.apply_action(action)
        
        child = MCTSNode(
            state=new_state, 
            player_idx=node.player_idx, 
            action=action, 
            parent=node
        )
        node.children.append(child)
        return child
        
    def _default_policy(self, state, opt_player_idx: int) -> float:
        """Rollout 2-3 turns via HeuristicAgent, returning an estimated win probability [0.0 - 1.0]."""
        current_state = state.clone()
        depth = 0
        start_turn = current_state.turn_count
        
        while not current_state.game_over and (current_state.turn_count - start_turn) < self.max_rollout_depth:
            active_p = current_state.active_player
            # Active Player could be opponent; handle properly
            action = self.rollout_agent.get_action(current_state, active_p)
            current_state.apply_action(action)
            depth += 1
            if depth > 50: # Safety breaker
                break
                
        # Objective terminal evaluation
        if current_state.game_over:
            if current_state.winner == current_state.players[opt_player_idx].name:
                return 1.0
            elif current_state.winner is None:
                return 0.5
            else:
                return 0.0
                
        # Heuristic leaf evaluation (Truncated Rollout estimate)
        p1 = current_state.players[opt_player_idx]
        p2 = current_state.players[(opt_player_idx + 1) % 2]
        
        # Life differential (weight: 1%)
        life_diff = p1.life - p2.life
        # Board presence (weight: 2% per power)
        p1_power = sum(c.power or 0 for c in current_state.battlefield.cards if c.controller == p1)
        p2_power = sum(c.power or 0 for c in current_state.battlefield.cards if c.controller == p2)
        
        score = 0.5 + (life_diff * 0.01) + ((p1_power - p2_power) * 0.02)
        return max(0.0, min(1.0, score))

    def _backpropagate(self, node: MCTSNode, reward: float):
        """Pass the simulation rollout reward back up the tree branch."""
        while node is not None:
            node.visits += 1
            node.wins += reward
            node = node.parent
