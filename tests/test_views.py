"""Tests for web/routes/views.py — HTML dashboard endpoints."""

from starlette.testclient import TestClient
from web.app import app


class TestViewRoutes:
    """Verifies all main HTML endpoints return 200 OK and expected content."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_read_root_dashboard(self):
        response = self.client.get("/")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]
        # The dashboard template uses the title
        assert b"MTG Genetic League" in response.content

    def test_get_leaderboard(self):
        response = self.client.get("/api/leaderboard")
        assert response.status_code == 200
        # FastAPI returns a JSON-encoded string unless response_class=HTMLResponse is specified
        assert "html" in response.headers["content-type"]

    def test_get_top_cards_api(self):
        response = self.client.get("/api/top-cards")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_get_top_cards_sidebar(self):
        response = self.client.get("/api/top-cards-sidebar")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_get_match_history(self):
        response = self.client.get("/api/match-history")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_get_stats(self):
        response = self.client.get("/api/stats")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_get_matchups(self):
        response = self.client.get("/api/matchups")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_view_matches_page(self):
        response = self.client.get("/matches")
        assert response.status_code == 200
        assert "html" in response.headers["content-type"]

    def test_get_engine_config(self):
        response = self.client.get("/api/engine/config")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        data = response.json()
        assert "max_workers" in data
        assert "headless_mode" in data

    def test_meta_map_json(self):
        response = self.client.get("/api/admin/meta-map")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]
        data = response.json()
        assert "points" in data
