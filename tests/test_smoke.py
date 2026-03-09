"""Smoke tests for MTG Genetic League application.
Validates core functionality: server imports, DB initialization, simulation,
and API endpoints return expected responses.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import json


# ─── Smoke 1: Server Imports ─────────────────────────────────────

def test_server_imports():
    """FastAPI app should import without errors."""
    from web.app import app
    assert app is not None
    assert app.title == "MTG Genetic League"


def test_core_engine_imports():
    """All core engine modules should import cleanly."""
    from engine.card import Card, StackItem
    from engine.deck import Deck
    from engine.player import Player
    from engine.game import Game
    from simulation.runner import SimulationRunner
    assert Card is not None
    assert Game is not None


# ─── Smoke 2: Database Initialization ────────────────────────────

def test_db_connection():
    """Database connection should be obtainable without error."""
    from data.db import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 AS test_val")
        row = cursor.fetchone()
        assert row is not None


def test_db_tables_exist():
    """Core tables should exist in the database."""
    from data.db import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Check for the decks table
        cursor.execute("SELECT COUNT(*) as c FROM decks")
        result = cursor.fetchone()
        assert result is not None


# ─── Smoke 3: Simulation Completes ───────────────────────────────

def test_simulation_completes():
    """A full game should run to completion without crashing."""
    from engine.card import Card
    from engine.deck import Deck
    from engine.player import Player
    from engine.game import Game
    from simulation.runner import SimulationRunner
    from agents.heuristic_agent import HeuristicAgent

    land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
    bolt = Card(name="Lightning Bolt", cost="{R}", type_line="Instant",
                oracle_text="Lightning Bolt deals 3 damage to any target.")
    creature = Card(name="Goblin Guide", cost="{R}", type_line="Creature — Goblin Scout",
                    oracle_text="Haste", base_power=2, base_toughness=2)

    d1, d2 = Deck(), Deck()
    d1.add_card(land, 24)
    d1.add_card(bolt, 4)
    d1.add_card(creature, 32)
    d2.add_card(land, 24)
    d2.add_card(bolt, 4)
    d2.add_card(creature, 32)

    p1 = Player("Aggro1", d1)
    p2 = Player("Aggro2", d2)
    game = Game([p1, p2])
    runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
    result = runner.run()

    assert result is not None
    assert result.turns > 0
    assert result.outcome in ("Win", "Draw", "Error")


def test_simulation_has_log():
    """Simulation result should include a game log."""
    from engine.card import Card
    from engine.deck import Deck
    from engine.player import Player
    from engine.game import Game
    from simulation.runner import SimulationRunner
    from agents.heuristic_agent import HeuristicAgent

    land = Card(name="Forest", cost="", type_line="Basic Land - Forest")
    d1, d2 = Deck(), Deck()
    d1.add_card(land, 60)
    d2.add_card(land, 60)

    p1 = Player("P1", d1)
    p2 = Player("P2", d2)
    game = Game([p1, p2])
    runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
    result = runner.run()

    assert isinstance(result.game_log, list)
    assert len(result.game_log) > 0


# ─── Smoke 4: API Endpoints (via TestClient) ─────────────────────

@pytest.fixture(scope="module")
def client():
    """Create a test client for the FastAPI app."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        from httpx import Client as TestClient
    from web.app import app
    return TestClient(app)


def test_api_leaderboard(client):
    """GET / should return 200 with dashboard HTML."""
    response = client.get("/")
    assert response.status_code == 200
    assert "MTG Genetic League" in response.text


def test_api_meta(client):
    """GET / should contain meta analysis content when sections are loaded."""
    response = client.get("/")
    assert response.status_code == 200
    # Dashboard page should contain the meta analysis tab
    assert "meta" in response.text.lower() or "Meta" in response.text


def test_api_leaderboard_endpoint(client):
    """GET /api/leaderboard should return 200."""
    response = client.get("/api/leaderboard")
    assert response.status_code == 200


def test_api_matches(client):
    """GET /matches should return 200."""
    response = client.get("/matches")
    assert response.status_code == 200


# ─── Smoke 5: Card Building ──────────────────────────────────────

def test_card_builder_basic():
    """Card builder should create cards from dict data."""
    from engine.card_builder import dict_to_card
    card = dict_to_card({
        'name': 'Grizzly Bears',
        'mana_cost': '{1}{G}',
        'type_line': 'Creature — Bear',
        'oracle_text': '',
        'power': '2',
        'toughness': '2'
    })
    assert card.name == 'Grizzly Bears'
    assert card.power == 2
    assert card.toughness == 2


def test_card_keywords_parsed():
    """Card keywords should be parsed from oracle text."""
    from engine.card import Card
    card = Card(name="Test", cost="{W}", type_line="Creature",
                oracle_text="Flying, lifelink, first strike",
                base_power=2, base_toughness=2)
    assert card.has_flying
    assert card.has_lifelink
    assert card.has_first_strike


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
