"""Tests for agents/neural_agent.py — AlphaZero-style neural network agent."""

import math
from agents.neural_agent import SimpleNeuralNet, NeuralMCTSNode, NeuralAgent
import numpy as np


class TestSimpleNeuralNet:
    """Lightweight NumPy neural network for state evaluation."""

    def test_init_shapes(self):
        net = SimpleNeuralNet(state_dim=10, hidden_dim=20, hidden_dim2=15)
        # Weights
        assert net.W1.shape == (10, 20)
        assert net.W2.shape == (20, 15)
        assert net.Wp.shape == (15, 20)  # action_dim=20 hardcoded in model
        assert net.Wv.shape == (15, 1)

    def test_forward_pass(self):
        net = SimpleNeuralNet(state_dim=276)
        state_vec = np.zeros(276)
        state_vec[0] = 1.0  # mock some data
        
        policy_logits, value = net.forward(state_vec)
        
        # Policy output should be length 20
        assert len(policy_logits) == 20
        
        # Value output should be a scalar
        assert isinstance(value, float)
        assert -1.0 <= value <= 1.0


class TestNeuralMCTSNode:
    """MCTS Node using PUCT selection."""

    def test_init(self):
        node = NeuralMCTSNode(state="dummy", player_idx=0, prior=0.7)
        assert node.prior == 0.7
        assert node.visits == 0
        assert node.total_value == 0.0

    def test_puct_score_zero_visits(self):
        parent = NeuralMCTSNode(state="parent", player_idx=0)
        parent.visits = 10
        
        # Child with 0 visits should use puct properly: Q=0 + c * prior * sqrt(10) / 1
        child = NeuralMCTSNode(state="child", player_idx=1, parent=parent, prior=0.5)
        expected = 1.5 * 0.5 * math.sqrt(10) / 1.0
        assert abs(child.puct_score() - expected) < 0.001

    def test_puct_score_calculation(self):
        parent = NeuralMCTSNode(state="parent", player_idx=0)
        parent.visits = 10
        
        child = NeuralMCTSNode(state="child", player_idx=1, parent=parent, prior=0.4)
        child.visits = 2
        child.total_value = 1.0  # avg value = 0.5
        
        # PUCT = Q + c * prior * sqrt(parent.visits) / (1 + child.visits)
        # Q = 1.0 / 2 = 0.5
        # U = 1.5 * 0.4 * sqrt(10) / (1 + 2)
        expected = 0.5 + 1.5 * 0.4 * math.sqrt(10) / 3.0
        assert abs(child.puct_score() - expected) < 0.001


class TestNeuralAgent:
    """AlphaZero-inspired agent logic."""

    def test_init(self):
        agent = NeuralAgent(max_iterations=15, c_puct=2.0)
        assert agent.max_iterations == 15
        assert agent.c_puct == 2.0
        assert agent.model is not None
        assert agent.training_data == []

    def test_terminal_value(self):
        agent = NeuralAgent()
        
        class MockPlayer:
            def __init__(self, name):
                self.name = name
                
        class MockState:
            def __init__(self, winner):
                self.players = [MockPlayer("P1"), MockPlayer("P2")]
                self.winner = winner
                self.game_over = True
                
        # 1.0 for win, -1.0 for loss, 0.0 for draw
        state_win = MockState("P1")
        assert agent._terminal_value(state_win, 0) == 1.0
        assert agent._terminal_value(state_win, 1) == 0.0
        
        state_draw = MockState(None)
        assert agent._terminal_value(state_draw, 0) == 0.5
        assert agent._terminal_value(state_draw, 1) == 0.5
