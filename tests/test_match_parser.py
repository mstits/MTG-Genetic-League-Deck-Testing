"""Tests for web/match_parser.py — Game log → structured replay data."""

from web.match_parser import parse_match_log


class TestParseMatchLog:
    """parse_match_log converts raw text game logs into structured JSON."""

    SAMPLE_LOG = (
        "MATCH: D1234 vs D5678\n"
        "--- Game 1 (8 turns, winner: D1234) ---\n"
        "--- T1 | D1234 (20hp, 7cards, 0lands) vs D5678 (20hp, 7cards, 0lands) [WP: 0.50] ---\n"
        "  Board: D1234 [empty] | D5678 [empty]\n"
        "D1234 plays Mountain\n"
        "D1234 casts Goblin Guide\n"
        "--- T2 | D1234 (20hp, 6cards, 1lands) vs D5678 (18hp, 7cards, 0lands) [WP: 0.60] ---\n"
        "  Board: D1234 [Goblin Guide 2/2] | D5678 [empty]\n"
        "D1234 attacks with Goblin Guide\n"
    )

    def test_parses_match_info(self):
        result = parse_match_log(self.SAMPLE_LOG)
        assert "match_info" in result
        assert "D1234 vs D5678" in result["match_info"]["title"]

    def test_parses_game_count(self):
        result = parse_match_log(self.SAMPLE_LOG)
        assert len(result["games"]) == 1

    def test_parses_game_winner(self):
        result = parse_match_log(self.SAMPLE_LOG)
        assert result["games"][0]["winner"] == "D1234"

    def test_parses_turn_structure(self):
        result = parse_match_log(self.SAMPLE_LOG)
        game = result["games"][0]
        assert len(game["turns"]) == 2
        assert game["turns"][0]["turn_num"] == 1
        assert game["turns"][1]["turn_num"] == 2

    def test_parses_player_stats(self):
        result = parse_match_log(self.SAMPLE_LOG)
        t1 = result["games"][0]["turns"][0]
        assert t1["p1"]["hp"] == 20
        assert t1["p1"]["cards"] == 7
        assert t1["p1"]["lands"] == 0
        assert t1["p2"]["hp"] == 20

    def test_parses_win_probability(self):
        result = parse_match_log(self.SAMPLE_LOG)
        assert result["games"][0]["turns"][0]["win_prob"] == 0.50
        assert result["games"][0]["turns"][1]["win_prob"] == 0.60

    def test_parses_board_state(self):
        result = parse_match_log(self.SAMPLE_LOG)
        t1 = result["games"][0]["turns"][0]
        # Board line has leading whitespace: "  Board: D1234 [empty] | D5678 [empty]"
        # [empty] should result in empty list
        assert t1["p1"]["board"] == []

    def test_parses_actions(self):
        result = parse_match_log(self.SAMPLE_LOG)
        t1_actions = result["games"][0]["turns"][0]["actions"]
        assert any("Mountain" in a for a in t1_actions)
        assert any("Goblin Guide" in a for a in t1_actions)

    def test_empty_log(self):
        result = parse_match_log("")
        assert result["games"] == []

    def test_multi_game_log(self):
        log = (
            "MATCH: A vs B\n"
            "--- Game 1 (5 turns, winner: A) ---\n"
            "--- T1 | A (20hp, 7cards, 0lands) vs B (20hp, 7cards, 0lands) ---\n"
            "A plays Forest\n"
            "--- Game 2 (3 turns, winner: B) ---\n"
            "--- T1 | A (20hp, 7cards, 0lands) vs B (20hp, 7cards, 0lands) ---\n"
            "B plays Island\n"
        )
        result = parse_match_log(log)
        assert len(result["games"]) == 2
        assert result["games"][0]["winner"] == "A"
        assert result["games"][1]["winner"] == "B"
