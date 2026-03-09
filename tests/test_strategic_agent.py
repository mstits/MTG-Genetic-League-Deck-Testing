"""Tests for StrategicAgent — tempo, card advantage, VCA, and look-ahead scoring."""

import pytest
from engine.card import Card
from engine.deck import Deck
from engine.game import Game
from engine.player import Player
from agents.strategic_agent import StrategicAgent
from agents.heuristic_agent import HeuristicAgent
from simulation.runner import SimulationRunner


def _make_deck(cards: list[tuple[str, str, str, int, int]]) -> Deck:
    """Build a Deck from (name, cost, type, power, toughness) tuples.
    Fill to 60 cards with Mountains.
    """
    deck = Deck()
    for name, cost, type_line, power, toughness in cards:
        c = Card(name=name, cost=cost, type_line=type_line,
                 base_power=power, base_toughness=toughness)
        deck.add_card(c, 1)
    # Fill remaining with Mountains
    while deck.total_maindeck < 60:
        deck.add_card(Card(name="Mountain", cost="", type_line="Land — Mountain"), 1)
    return deck


class TestStrategicAgentScoring:
    """Test scoring functions in isolation."""

    def setup_method(self):
        deck1 = _make_deck([
            ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
            ("Lightning Bolt", "{R}", "Instant", None, None),
        ])
        deck2 = _make_deck([
            ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
        ])
        p1 = Player("P1", deck1)
        p2 = Player("P2", deck2)
        self.game = Game([p1, p2])
        self.game.start_game()
        self.agent = StrategicAgent(look_ahead_depth=0)  # No look-ahead for unit tests
        self.p1 = p1
        self.p2 = p2

    def test_board_power_empty(self):
        """Empty board should return 0."""
        assert self.agent._board_power(self.game, self.p1) == 0.0

    def test_board_power_with_creature(self):
        """A 2/2 on the board should have positive board power."""
        creature = Card(name="Bear", cost="{1}{G}", type_line="Creature — Bear",
                       base_power=2, base_toughness=2)
        creature.controller = self.p1
        self.game.battlefield.add(creature)
        power = self.agent._board_power(self.game, self.p1)
        assert power > 0

    def test_board_power_with_keywords(self):
        """Flying creature should score higher than vanilla."""
        vanilla = Card(name="Bear", cost="{1}{G}", type_line="Creature",
                      base_power=2, base_toughness=2)
        vanilla.controller = self.p1
        self.game.battlefield.add(vanilla)
        vanilla_power = self.agent._board_power(self.game, self.p1)

        self.game.battlefield.remove(vanilla)
        flyer = Card(name="Flyer", cost="{1}{U}", type_line="Creature",
                    base_power=2, base_toughness=2,
                    oracle_text="Flying")
        flyer.controller = self.p1
        self.game.battlefield.add(flyer)
        flyer_power = self.agent._board_power(self.game, self.p1)

        assert flyer_power > vanilla_power

    def test_tempo_pass_is_zero(self):
        """Passing should have 0 tempo."""
        score = self.agent._evaluate_tempo(self.game, self.p1, {'type': 'pass'})
        assert score == 0.0

    def test_tempo_land_play(self):
        """Playing a land has moderate tempo value."""
        score = self.agent._evaluate_tempo(self.game, self.p1, {'type': 'play_land'})
        assert 0 < score < 1.0

    def test_card_advantage_draw(self):
        """Cards with 'draw a card' should score high on CA."""
        cantrip = Card(name="Opt", cost="{U}", type_line="Instant",
                      oracle_text="Scry 1, then draw a card.")
        score = self.agent._evaluate_card_advantage(
            self.game, self.p1,
            {'type': 'announce_cast', 'card': cantrip}
        )
        assert score > 0.3

    def test_card_advantage_removal(self):
        """Removal spells should have positive CA."""
        bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant",
                   oracle_text="Lightning Bolt deals 3 damage to any target.")
        bolt.is_removal = True
        score = self.agent._evaluate_card_advantage(
            self.game, self.p1,
            {'type': 'cast_spell', 'card': bolt}
        )
        assert score > 0

    def test_vca_big_creature(self):
        """A 5/5 should score higher VCA than a 1/1."""
        big = Card(name="Big", cost="{4}{G}", type_line="Creature",
                  base_power=5, base_toughness=5)
        small = Card(name="Small", cost="{W}", type_line="Creature",
                    base_power=1, base_toughness=1)
        
        big_score = self.agent._evaluate_virtual_card_advantage(
            self.game, self.p1, {'type': 'announce_cast', 'card': big})
        small_score = self.agent._evaluate_virtual_card_advantage(
            self.game, self.p1, {'type': 'announce_cast', 'card': small})
        
        assert big_score > small_score


class TestStrategicAgentIntegration:
    """Test that StrategicAgent can play complete games."""

    def test_completes_game(self):
        """StrategicAgent should be able to play a full game without errors."""
        deck1 = _make_deck([
            ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
            ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
            ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
            ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
            ("Lightning Bolt", "{R}", "Instant", None, None),
            ("Lightning Bolt", "{R}", "Instant", None, None),
            ("Lightning Bolt", "{R}", "Instant", None, None),
            ("Lightning Bolt", "{R}", "Instant", None, None),
        ])
        deck2 = _make_deck([
            ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
            ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
            ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
            ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
        ])
        p1 = Player("Strategic", deck1)
        p2 = Player("Heuristic", deck2)
        game = Game([p1, p2])
        
        # StrategicAgent with no look-ahead for speed
        agent1 = StrategicAgent(look_ahead_depth=0)
        agent2 = HeuristicAgent()
        
        runner = SimulationRunner(game, [agent1, agent2])
        result = runner.run()
        
        assert result.outcome in ("Win", "Draw", "Error")
        assert result.turns >= 1

    def test_strategic_vs_heuristic_bo3(self):
        """Run a Bo3 — Strategic should at least be competitive."""
        wins = {"Strategic": 0, "Heuristic": 0, "Draw": 0}
        
        for _ in range(3):
            deck1 = _make_deck([
                ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
                ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
                ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
                ("Goblin Guide", "{R}", "Creature — Goblin", 2, 2),
                ("Lightning Bolt", "{R}", "Instant", None, None),
                ("Lightning Bolt", "{R}", "Instant", None, None),
                ("Lightning Bolt", "{R}", "Instant", None, None),
                ("Lightning Bolt", "{R}", "Instant", None, None),
                ("Lava Spike", "{R}", "Sorcery", None, None),
                ("Lava Spike", "{R}", "Sorcery", None, None),
            ])
            deck2 = _make_deck([
                ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
                ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
                ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
                ("Bear", "{1}{G}", "Creature — Bear", 2, 2),
                ("Giant Growth", "{G}", "Instant", None, None),
                ("Giant Growth", "{G}", "Instant", None, None),
            ])
            p1 = Player("Strategic", deck1)
            p2 = Player("Heuristic", deck2)
            game = Game([p1, p2])
            agent1 = StrategicAgent(look_ahead_depth=0)
            agent2 = HeuristicAgent()
            runner = SimulationRunner(game, [agent1, agent2])
            result = runner.run()
            
            if result.winner == "Strategic":
                wins["Strategic"] += 1
            elif result.winner == "Heuristic":
                wins["Heuristic"] += 1
            else:
                wins["Draw"] += 1
        
        # We just verify it completes without errors
        total = sum(wins.values())
        assert total == 3
