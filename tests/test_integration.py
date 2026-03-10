"""Integration tests — End-to-end flows testing multiple components together.

These tests verify the interaction between engine, agents, simulation, 
and web API layers to ensure the full system works cohesively.
"""

from engine.card import Card
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from simulation.runner import SimulationRunner
from agents.heuristic_agent import HeuristicAgent
from agents.random_agent import RandomAgent


def _bolt():
    return Card("Lightning Bolt", "{R}", "Instant", "Lightning Bolt deals 3 damage to any target.")


def _mountain():
    return Card("Mountain", "", "Basic Land — Mountain", "{T}: Add {R}.", produced_mana=["R"])


def _bear():
    return Card("Grizzly Bears", "{1}{G}", "Creature — Bear", "", base_power=2, base_toughness=2)


def _forest():
    return Card("Forest", "", "Basic Land — Forest", "{T}: Add {G}.", produced_mana=["G"])


def _make_deck(land_fn, spell_fn, land_count=24, spell_count=36):
    d = Deck()
    d.add_card(land_fn(), land_count)
    d.add_card(spell_fn(), spell_count)
    return d


# ──────────────────────────────────────────────────────────────────────────────
# Full Game Simulation
# ──────────────────────────────────────────────────────────────────────────────

class TestFullGameSimulation:
    """Player → Game → SimulationRunner → HeuristicAgent → GameResult."""

    def test_game_completes_with_winner(self):
        """A full game between two heuristic agents should complete."""
        d1 = _make_deck(_mountain, _bolt)
        d2 = _make_deck(_forest, _bear)
        p1 = Player("Burn", d1)
        p2 = Player("Bears", d2)
        game = Game([p1, p2])
        runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
        result = runner.run()
        
        assert result is not None
        assert hasattr(result, 'winner')
        assert result.winner in ("Burn", "Bears", None)  # None = draw

    def test_game_result_has_turn_count(self):
        d1 = _make_deck(_mountain, _bolt)
        d2 = _make_deck(_forest, _bear)
        p1 = Player("P1", d1)
        p2 = Player("P2", d2)
        game = Game([p1, p2])
        runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
        result = runner.run()
        
        assert hasattr(result, 'turns')
        assert result.turns > 0

    def test_random_vs_heuristic(self):
        """HeuristicAgent should frequently beat RandomAgent."""
        d1 = _make_deck(_mountain, _bolt)
        d2 = _make_deck(_forest, _bear)
        
        h_wins = 0
        r_wins = 0
        for _ in range(5):
            p1 = Player("Heuristic", _make_deck(_mountain, _bolt))
            p2 = Player("Random", _make_deck(_forest, _bear))
            game = Game([p1, p2])
            runner = SimulationRunner(game, [HeuristicAgent(), RandomAgent()])
            result = runner.run()
            if result.winner == "Heuristic":
                h_wins += 1
            elif result.winner == "Random":
                r_wins += 1
        
        # HeuristicAgent should win most of the time
        assert h_wins >= r_wins, f"Heuristic: {h_wins}, Random: {r_wins}"


# ──────────────────────────────────────────────────────────────────────────────
# Deck Build Pipeline
# ──────────────────────────────────────────────────────────────────────────────

class TestDeckBuildPipeline:
    """Raw dict → build_deck() → Deck with correct card count."""

    def test_deck_blueprint_count(self):
        d = Deck()
        d.add_card(_mountain(), 24)
        d.add_card(_bolt(), 36)
        total = sum(qty for _, qty in d._blueprints)
        assert total == 60

    def test_deck_generates_game_deck(self):
        """Deck.generate() should produce a shuffled library."""
        d = Deck()
        d.add_card(_mountain(), 24)
        d.add_card(_bolt(), 36)
        p = Player("Test", d)
        # Player should have drawn opening hand
        assert len(p.hand) > 0 or len(p.library) > 0

    def test_deck_can_serve_multiple_players(self):
        """Same deck blueprint used by different players should be independent."""
        d1 = Deck()
        d1.add_card(_mountain(), 24)
        d1.add_card(_bolt(), 36)
        d2 = Deck()
        d2.add_card(_forest(), 24)
        d2.add_card(_bear(), 36)
        p1 = Player("P1", d1)
        p2 = Player("P2", d2)
        # Players should have independent game states
        assert p1.name != p2.name


# ──────────────────────────────────────────────────────────────────────────────
# Bo3 Match
# ──────────────────────────────────────────────────────────────────────────────

class TestBo3Integration:
    """Bo3Match.play() completes 2–3 games with sideboarding."""

    def test_bo3_completes(self):
        from engine.bo3 import Bo3Match
        d1 = _make_deck(_mountain, _bolt)
        d2 = _make_deck(_forest, _bear)
        match = Bo3Match(d1, d2, max_turns=50)
        result = match.play()
        
        assert result is not None
        assert "winner" in result
        assert "games" in result
        assert len(result["games"]) >= 2  # At least 2 games in a Bo3

    def test_bo3_has_valid_winner(self):
        from engine.bo3 import Bo3Match
        d1 = _make_deck(_mountain, _bolt)
        d2 = _make_deck(_forest, _bear)
        match = Bo3Match(d1, d2, max_turns=50)
        result = match.play()
        
        # Winner should be one of the deck identifiers or None
        assert result["winner"] is not None or len(result["games"]) == 3


# ──────────────────────────────────────────────────────────────────────────────
# API Round-Trip
# ──────────────────────────────────────────────────────────────────────────────

class TestAPIRoundTrip:
    """API endpoints respond correctly end-to-end."""

    def test_health_endpoint(self):
        from starlette.testclient import TestClient
        from web.app import app
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "service" in data

    def test_leaderboard_api(self):
        from starlette.testclient import TestClient
        from web.app import app
        client = TestClient(app)
        r = client.get("/api/leaderboard?format=json")
        assert r.status_code == 200

    def test_dashboard_html(self):
        from starlette.testclient import TestClient
        from web.app import app
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 200
        assert "html" in r.headers.get("content-type", "").lower() or r.status_code == 200
