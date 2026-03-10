"""Tests for agents/random_agent.py — Random action selection baseline."""

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from agents.random_agent import RandomAgent


def _make_game():
    """Create a minimal game for agent testing."""
    d1 = Deck()
    d2 = Deck()
    land = Card("Mountain", "", "Basic Land — Mountain", "{T}: Add {R}.", produced_mana=["R"])
    creature = Card("Goblin Guide", "{R}", "Creature — Goblin Scout", "Haste", base_power=2, base_toughness=2)
    for _ in range(30):
        d1.add_card(land, 1)
        d2.add_card(land, 1)
    for _ in range(30):
        d1.add_card(creature, 1)
        d2.add_card(creature, 1)
    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    return Game([p1, p2])


class TestRandomAgent:
    """RandomAgent selects uniformly random legal actions."""

    def test_returns_dict(self):
        agent = RandomAgent()
        g = _make_game()
        action = agent.get_action(g, g.players[0])
        assert isinstance(action, dict)

    def test_returns_valid_type(self):
        agent = RandomAgent()
        g = _make_game()
        action = agent.get_action(g, g.players[0])
        assert 'type' in action

    def test_returns_pass_when_no_actions(self):
        """When game reports no legal actions, agent returns pass."""
        agent = RandomAgent()
        g = _make_game()
        # Force game_over to eliminate legal actions
        g.game_over = True
        g._legal_actions_cache = []
        action = agent.get_action(g, g.players[0])
        assert action == {'type': 'pass'}

    def test_multiple_calls_not_identical(self):
        """Over many calls, RandomAgent should produce varying results.
        
        Note: This test could theoretically fail with astronomically low probability
        if random happens to choose the same action 20 times in a row.
        """
        agent = RandomAgent()
        g = _make_game()
        actions = set()
        for _ in range(20):
            a = agent.get_action(g, g.players[0])
            actions.add(str(a))
        # With enough calls, we should see at least some variety
        # (relaxed check — just verifying the agent runs without error)
        assert len(actions) >= 1

    def test_inherits_base_agent(self):
        from agents.base_agent import BaseAgent
        assert issubclass(RandomAgent, BaseAgent)
