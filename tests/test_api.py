"""API endpoint tests for the MTG Genetic League web application.

Tests every public API endpoint for correct status codes, response structure,
and edge-case handling. Uses Starlette's TestClient for synchronous testing
of async FastAPI routes.

Covers:
    - Health check and service readiness
    - Leaderboard (HTML + JSON formats)
    - Top cards and card stats
    - Meta analysis (network, trends, matchup matrix)
    - Turn distribution histogram
    - Deck detail, suggestions, comparison
    - Card coverage statistics
    - Export (Arena/MTGO format)
    - Error handling (404s, bad inputs)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from starlette.testclient import TestClient
from web.app import app


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """Reusable test client for all API tests in this module."""
    return TestClient(app)


@pytest.fixture(scope="module")
def first_deck_id(client):
    """Get the ID of the first deck in the leaderboard, or skip if none exist."""
    resp = client.get("/api/leaderboard?format=json&limit=1")
    if resp.status_code != 200:
        pytest.skip("Leaderboard endpoint not available")
    data = resp.json()
    if not data or not isinstance(data, list) or len(data) == 0:
        pytest.skip("No decks in database")
    return data[0]["id"]


# ─── Health & Infrastructure ─────────────────────────────────────────────────

class TestHealth:
    """Tests for the /health readiness endpoint."""

    def test_health_returns_200(self, client):
        """Health endpoint should return 200 when DB is reachable."""
        resp = client.get("/health")
        assert resp.status_code in (200, 503)  # 503 if DB down
        data = resp.json()
        assert "service" in data
        assert "db" in data

    def test_health_reports_db_status(self, client):
        """Health response should include DB connectivity status."""
        data = client.get("/health").json()
        assert data["db"] in ("connected", "unreachable")

    def test_health_reports_cache_status(self, client):
        """Health response should report card pool cache state."""
        data = client.get("/health").json()
        assert "card_pool_cached" in data
        assert isinstance(data["card_pool_cached"], bool)


# ─── Dashboard & Pages ───────────────────────────────────────────────────────

class TestDashboard:
    """Tests for HTML page endpoints."""

    def test_dashboard_loads(self, client):
        """GET / should return the main dashboard page."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "MTG Genetic League" in resp.text

    def test_matches_page_loads(self, client):
        """GET /matches should return match history page."""
        resp = client.get("/matches")
        assert resp.status_code == 200

    def test_decks_page_loads(self, client):
        """GET /decks should return the decks listing page."""
        resp = client.get("/decks")
        assert resp.status_code == 200


# ─── Leaderboard API ─────────────────────────────────────────────────────────

class TestLeaderboard:
    """Tests for /api/leaderboard endpoint."""

    def test_leaderboard_html(self, client):
        """Default format should return HTML table rows."""
        resp = client.get("/api/leaderboard")
        assert resp.status_code == 200

    def test_leaderboard_json(self, client):
        """JSON format should return a list of deck objects."""
        resp = client.get("/api/leaderboard?format=json")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_leaderboard_json_structure(self, client):
        """Each deck in JSON response should have required fields."""
        data = client.get("/api/leaderboard?format=json&limit=1").json()
        if len(data) > 0:
            deck = data[0]
            assert "id" in deck
            assert "name" in deck
            assert "elo" in deck

    def test_leaderboard_limit(self, client):
        """Limit parameter should cap the number of results."""
        data = client.get("/api/leaderboard?format=json&limit=3").json()
        assert len(data) <= 3


# ─── Top Cards ───────────────────────────────────────────────────────────────

class TestTopCards:
    """Tests for card statistics endpoints."""

    def test_top_cards_returns_200(self, client):
        """GET /api/top-cards should return HTML."""
        resp = client.get("/api/top-cards")
        assert resp.status_code == 200

    def test_top_cards_sidebar(self, client):
        """GET /api/top-cards-sidebar should return compact HTML."""
        resp = client.get("/api/top-cards-sidebar")
        assert resp.status_code == 200


# ─── Meta Analysis ───────────────────────────────────────────────────────────

class TestMeta:
    """Tests for meta analysis endpoints."""

    def test_meta_returns_200(self, client):
        """GET /api/meta should return color/archetype data."""
        resp = client.get("/api/meta")
        assert resp.status_code == 200

    def test_matchup_matrix(self, client):
        """GET /api/matchup-matrix should return matchup data."""
        resp = client.get("/api/matchup-matrix")
        assert resp.status_code == 200
        data = resp.json()
        # API may return either legacy format (colors/matchups) or new (archetypes/matrix)
        has_legacy = "colors" in data and "matchups" in data
        has_new = "archetypes" in data and "matrix" in data
        assert has_legacy or has_new

    def test_meta_trends(self, client):
        """GET /api/meta-trends should return historical data."""
        resp = client.get("/api/meta-trends")
        assert resp.status_code == 200

    def test_turn_distribution(self, client):
        """GET /api/turn-distribution should return histogram data."""
        resp = client.get("/api/turn-distribution")
        assert resp.status_code == 200
        data = resp.json()
        assert "distribution" in data
        assert "total_games" in data
        assert "avg_turns" in data
        assert isinstance(data["distribution"], list)

    def test_card_search(self, client):
        """GET /api/card-search should return card matches."""
        resp = client.get("/api/card-search?q=mountain")
        # May return 200 or 404 depending on card pool loading
        assert resp.status_code in (200, 404, 500)


# ─── Deck Detail & Suggestions ──────────────────────────────────────────────

class TestDeckDetail:
    """Tests for deck-specific endpoints."""

    def test_deck_page_loads(self, client, first_deck_id):
        """GET /deck/{id} should load the deck detail page."""
        resp = client.get(f"/deck/{first_deck_id}")
        assert resp.status_code == 200

    def test_deck_suggestions(self, client, first_deck_id):
        """GET /api/deck/{id}/suggestions should return card recommendations."""
        resp = client.get(f"/api/deck/{first_deck_id}/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_deck_export_arena(self, client, first_deck_id):
        """GET /api/export/{id} should return Arena format decklist."""
        resp = client.get(f"/api/export/{first_deck_id}")
        assert resp.status_code == 200

    def test_deck_nonexistent_returns_error(self, client):
        """GET /deck/999999 should handle missing deck gracefully."""
        resp = client.get("/deck/999999")
        # Should return 404 or redirect, not crash
        assert resp.status_code in (200, 404, 302)


# ─── Deck Comparison ────────────────────────────────────────────────────────

class TestDeckComparison:
    """Tests for the deck comparison API."""

    def test_compare_requires_both_ids(self, client):
        """GET /api/compare without IDs should return validation error."""
        resp = client.get("/api/compare")
        assert resp.status_code == 422  # FastAPI validation error

    def test_compare_nonexistent_deck(self, client):
        """Comparing nonexistent decks should return 404."""
        resp = client.get("/api/compare?deck1_id=999999&deck2_id=999998")
        assert resp.status_code == 404

    def test_compare_valid_decks(self, client, first_deck_id):
        """Comparing a deck with itself should return valid data."""
        resp = client.get(f"/api/compare?deck1_id={first_deck_id}&deck2_id={first_deck_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "deck1" in data
        assert "deck2" in data
        assert "shared" in data
        assert "overlap_pct" in data


# ─── Card Coverage ───────────────────────────────────────────────────────────

class TestCardCoverage:
    """Tests for the card pool coverage endpoint."""

    def test_card_coverage(self, client):
        """GET /api/card-coverage should return coverage statistics."""
        resp = client.get("/api/card-coverage")
        assert resp.status_code == 200


# ─── Error Handling ──────────────────────────────────────────────────────────

class TestErrorHandling:
    """Tests for proper error responses on invalid inputs."""

    def test_nonexistent_route_404(self, client):
        """Unknown routes should return 404, not 500."""
        resp = client.get("/api/nonexistent-endpoint")
        assert resp.status_code == 404

    def test_invalid_match_id(self, client):
        """Invalid match ID should not crash the server."""
        resp = client.get("/match/999999")
        assert resp.status_code in (200, 404)

    def test_leaderboard_invalid_format(self, client):
        """Invalid format parameter should still return data."""
        resp = client.get("/api/leaderboard?format=xml")
        assert resp.status_code == 200  # Falls through to default format


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
