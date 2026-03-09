"""Phase 2 feature tests — error handling, error budget, SQL unification, config."""

import pytest
import json


# ── Error Hierarchy ──────────────────────────────────────────────────

class TestErrorHierarchy:
    """Verify the custom error type inheritance chain."""

    def test_game_state_error_is_exception(self):
        from engine.errors import GameStateError
        assert issubclass(GameStateError, Exception)

    def test_card_interaction_error_is_game_state_error(self):
        from engine.errors import GameStateError, CardInteractionError
        assert issubclass(CardInteractionError, GameStateError)

    def test_simulation_budget_error_is_separate(self):
        from engine.errors import GameStateError, SimulationBudgetError
        assert not issubclass(SimulationBudgetError, GameStateError)
        assert issubclass(SimulationBudgetError, Exception)

    def test_simulation_budget_error_carries_data(self):
        from engine.errors import SimulationBudgetError
        err = SimulationBudgetError(
            error_count=15, threshold=10,
            error_summary={"GameStateError": 10, "TypeError": 5}
        )
        assert err.error_count == 15
        assert err.threshold == 10
        assert "GameStateError" in err.error_summary
        assert "15/10" in str(err)

    def test_game_state_error_caught_by_exception(self):
        """GameStateError should be catchable as Exception."""
        from engine.errors import GameStateError
        with pytest.raises(Exception):
            raise GameStateError("test")

    def test_card_interaction_error_caught_by_game_state(self):
        """CardInteractionError should be catchable as GameStateError."""
        from engine.errors import GameStateError, CardInteractionError
        caught = False
        try:
            raise CardInteractionError("etb failed")
        except GameStateError:
            caught = True
        assert caught


# ── Error Budget Tracking ────────────────────────────────────────────

class TestErrorBudget:
    """Verify session-level error tracking in runner.py."""

    def test_reset_clears_counters(self):
        from simulation.runner import (
            reset_error_budget, get_error_budget_status, _record_error
        )
        from engine.errors import GameStateError
        _record_error(GameStateError("test"))
        reset_error_budget()
        status = get_error_budget_status()
        assert status["total_errors"] == 0
        assert status["error_types"] == {}
        assert status["budget_exceeded"] is False

    def test_record_increments_counters(self):
        from simulation.runner import (
            reset_error_budget, get_error_budget_status, _record_error
        )
        from engine.errors import GameStateError
        reset_error_budget()

        _record_error(GameStateError("a"))
        _record_error(GameStateError("b"))
        _record_error(TypeError("c"))

        status = get_error_budget_status()
        assert status["total_errors"] == 3
        assert status["error_types"]["GameStateError"] == 2
        assert status["error_types"]["TypeError"] == 1

    def test_budget_exceeded_flag(self):
        from simulation.runner import (
            reset_error_budget, get_error_budget_status, _record_error
        )
        from engine.engine_config import config
        from engine.errors import GameStateError

        reset_error_budget()
        old_threshold = config.error_budget_threshold
        config.error_budget_threshold = 3

        try:
            for _ in range(4):
                _record_error(GameStateError("test"))
            status = get_error_budget_status()
            assert status["budget_exceeded"] is True
        finally:
            config.error_budget_threshold = old_threshold
            reset_error_budget()


# ── Strict Mode ──────────────────────────────────────────────────────

class TestStrictErrors:
    """Verify that strict_errors re-raises genuine code bugs."""

    def test_strict_mode_off_by_default(self):
        from engine.engine_config import config
        assert config.strict_errors is False

    def test_strict_mode_toggle(self):
        from engine.engine_config import config
        config.strict_errors = True
        assert config.strict_errors is True
        config.strict_errors = False
        assert config.strict_errors is False


# ── Engine Config Serialization ──────────────────────────────────────

class TestEngineConfigSerialization:
    """Verify to_dict/update_from_dict round-trip."""

    def test_to_dict_includes_new_fields(self):
        from engine.engine_config import config
        d = config.to_dict()
        assert "strict_errors" in d
        assert "error_budget_threshold" in d
        assert isinstance(d["strict_errors"], bool)
        assert isinstance(d["error_budget_threshold"], int)

    def test_update_from_dict_applies_values(self):
        from engine.engine_config import config
        old_threshold = config.error_budget_threshold
        old_strict = config.strict_errors

        try:
            config.update_from_dict({
                "strict_errors": True,
                "error_budget_threshold": 42
            })
            assert config.strict_errors is True
            assert config.error_budget_threshold == 42
        finally:
            config.strict_errors = old_strict
            config.error_budget_threshold = old_threshold

    def test_round_trip(self):
        from engine.engine_config import config
        original = config.to_dict()
        config.update_from_dict(original)
        restored = config.to_dict()
        assert original == restored


# ── SQL Unification ──────────────────────────────────────────────────

class TestSQLUnification:
    """Verify unified save_deck and SQLiteDictCursor.lastrowid."""

    def test_save_deck_returns_valid_id(self):
        from data.db import save_deck, get_db_connection
        deck_id = save_deck(
            "test_phase2_sql", {"Lightning Bolt": 4}, generation=99, colors="R"
        )
        assert isinstance(deck_id, int)
        assert deck_id > 0

        # Clean up
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM decks WHERE name = %s", ("test_phase2_sql",))
            conn.commit()

    def test_sqlite_dict_cursor_lastrowid(self):
        from data.db import SQLiteDictCursor
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT)")
        cursor = SQLiteDictCursor(conn)
        cursor.execute("INSERT INTO test (val) VALUES (?)", ("hello",))
        assert cursor.lastrowid >= 1
        conn.close()


# ── GameResult error_type Field ──────────────────────────────────────

class TestGameResultErrorType:
    """Verify the error_type field on GameResult."""

    def test_error_type_default_none(self):
        from simulation.stats import GameResult
        r = GameResult(winner="TestPlayer", turns=5, outcome="Win")
        assert r.error_type is None

    def test_error_type_stored(self):
        from simulation.stats import GameResult
        r = GameResult(
            winner=None, turns=0, outcome="Error",
            error_type="GameStateError"
        )
        assert r.error_type == "GameStateError"
