"""Tests for agents/mcts_agent.py — Monte Carlo Tree Search Agent."""

import math
from agents.mcts_agent import MCTSNode, MCTSAgent
from engine.game import Game
from engine.player import Player
from engine.deck import Deck
from engine.card import Card


class TestMCTSNode:
    """MCTSNode stores state and calculates UCT."""

    def test_init(self):
        node = MCTSNode(state="dummy", player_idx=0)
        assert node.state == "dummy"
        assert node.player_idx == 0
        assert node.visits == 0
        assert node.wins == 0.0
        assert node.untried_actions is None

    def test_uct_score_zero_visits(self):
        node = MCTSNode(state="dummy", player_idx=0)
        assert node.uct_score() == float('inf')

    def test_uct_score_calculation(self):
        parent = MCTSNode(state="parent", player_idx=0)
        parent.visits = 10
        
        node = MCTSNode(state="child", player_idx=1, parent=parent)
        node.visits = 2
        node.wins = 1.0  # 50% win rate
        
        # UCT = (1/2) + 1.414 * sqrt(ln(10) / 2)
        score = node.uct_score()
        expected = 0.5 + 1.414 * math.sqrt(math.log(10) / 2)
        assert abs(score - expected) < 0.001


def _mock_game():
    d1 = Deck()
    d2 = Deck()
    d1.add_card(Card("Mountain", "", "Land", ""), 60)
    d2.add_card(Card("Mountain", "", "Land", ""), 60)
    g = Game([Player("P1", d1), Player("P2", d2)])
    return g


class TestMCTSAgent:
    """MCTSAgent builds game trees and executes simulations."""

    def test_init(self):
        agent = MCTSAgent(max_iterations=10, max_rollout_depth=4)
        assert agent.max_iterations == 10
        assert agent.max_rollout_depth == 4
        assert agent.rollout_agent is not None

    def test_expand_macro_actions(self):
        agent = MCTSAgent()
        g = _mock_game()
        c1 = Card("Goblin", "{R}", "Creature")
        c2 = Card("Bear", "{G}", "Creature")
        
        actions = [{'type': 'declare_attackers', 'candidates': [c1, c2]}]
        expanded = agent._expand_macro_actions(g, g.players[0], actions)
        
        # Powerset of {c1, c2} should yield 4 options: [], [c1], [c2], [c1, c2]
        assert len(expanded) == 4
        for action in expanded:
            assert action['type'] == 'declare_attackers'
            assert 'attackers' in action

    def test_get_action_immediate_pass(self):
        agent = MCTSAgent(max_iterations=1)
        g = _mock_game()
        g.game_over = True
        g._legal_actions_cache = []
        
        action = agent.get_action(g, g.players[0])
        assert action == {'type': 'pass'}

    def test_get_action_forced_move(self):
        agent = MCTSAgent(max_iterations=1)
        g = _mock_game()
        # Mock legal actions so there's only one choice
        g.get_legal_actions = lambda: [{'type': 'play_land', 'card': Card("Mountain", "", "Land", "")}]
        
        action = agent.get_action(g, g.players[0])
        assert action['type'] == 'play_land'

    def test_backpropagate(self):
        agent = MCTSAgent()
        root = MCTSNode(state="root", player_idx=0)
        child = MCTSNode(state="child", player_idx=1, parent=root)
        
        agent._backpropagate(child, 1.0)
        
        assert child.visits == 1
        assert child.wins == 1.0
        assert root.visits == 1
        assert root.wins == 1.0
