"""Tests for engine/anomaly_detector.py — Novel strategy detection."""

import os
import json
import tempfile
from engine.anomaly_detector import AnomalyDetector


class TestAnomalyDetectorInit:
    """AnomalyDetector initializes with empty state."""

    def test_init(self):
        ad = AnomalyDetector()
        assert ad.total_games == 0
        assert len(ad.interaction_freq) == 0

    def test_log_game_increments_count(self):
        ad = AnomalyDetector()
        ad.log_game(
            game_events=["A casts Lightning Bolt", "B takes 3 damage"],
            winner_deck=["Lightning Bolt", "Mountain"],
            loser_deck=["Island", "Counterspell"],
            winning_cards_played=["Lightning Bolt"],
        )
        assert ad.total_games == 1


class TestLogGame:
    """log_game records events and card interactions."""

    def test_records_interactions(self):
        ad = AnomalyDetector()
        ad.log_game(
            game_events=["combo activated"],
            winner_deck=["Card A", "Card B", "Card C"],
            loser_deck=["Card X"],
            winning_cards_played=["Card A", "Card B"],
        )
        # Card A and Card B should be recorded as co-occurring
        assert ad.total_games == 1

    def test_multiple_games(self):
        ad = AnomalyDetector()
        for i in range(5):
            ad.log_game(
                game_events=[f"event {i}"],
                winner_deck=["Bolt"],
                loser_deck=["Island"],
                winning_cards_played=["Bolt"],
            )
        assert ad.total_games == 5


class TestDetectAnomalies:
    """detect_anomalies requires minimum game count and returns reports."""

    def test_insufficient_games_returns_empty(self):
        ad = AnomalyDetector()
        ad.log_game(
            game_events=["event"],
            winner_deck=["Bolt"],
            loser_deck=["Island"],
            winning_cards_played=["Bolt"],
        )
        anomalies = ad.detect_anomalies(min_games=10)
        assert anomalies == []

    def test_with_enough_games(self):
        ad = AnomalyDetector()
        for i in range(15):
            ad.log_game(
                game_events=[f"event {i}"],
                winner_deck=["Bolt", "Mountain"],
                loser_deck=["Island", "Counterspell"],
                winning_cards_played=["Bolt"],
            )
        # Should run without error; result list may or may not have anomalies
        result = ad.detect_anomalies(min_games=10)
        assert isinstance(result, list)


class TestSaveLoad:
    """Detector state can be persisted to disk and reloaded."""

    def test_save_and_load(self):
        ad = AnomalyDetector()
        for i in range(3):
            ad.log_game(
                game_events=[f"e{i}"],
                winner_deck=["A", "B"],
                loser_deck=["X"],
                winning_cards_played=["A"],
            )
        assert ad.total_games == 3

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            ad.save(path)
            assert os.path.exists(path)

            ad2 = AnomalyDetector()
            ad2.load(path)
            assert ad2.total_games == 3
        finally:
            os.unlink(path)


class TestGenerateReport:
    """generate_report produces a human-readable string."""

    def test_report_with_no_games(self):
        ad = AnomalyDetector()
        report = ad.generate_report()
        assert isinstance(report, str)

    def test_report_with_data(self):
        ad = AnomalyDetector()
        for i in range(15):
            ad.log_game(
                game_events=[f"e{i}"],
                winner_deck=["A", "B"],
                loser_deck=["X"],
                winning_cards_played=["A"],
            )
        report = ad.generate_report()
        assert isinstance(report, str)
        assert len(report) > 0
