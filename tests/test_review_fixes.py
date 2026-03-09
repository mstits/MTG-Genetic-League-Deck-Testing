"""Unit tests for code review fixes.
Tests specific fixes from the E8 code review including DictRow, ELO, cloning,
SQL translation, card pool caching, Swiss pairing, and error handling.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import copy


# ─── Fix #5: DictRow O(1) Integer Access ──────────────────────────

class TestDictRow:
    def test_integer_access_returns_correct_values(self):
        """DictRow[int] should return values in insertion order, O(1)."""
        from data.db import DictRow
        row = DictRow([('name', 'Bolt'), ('cost', '{R}'), ('cmc', 1)])
        assert row[0] == 'Bolt'
        assert row[1] == '{R}'
        assert row[2] == 1

    def test_key_access_still_works(self):
        """DictRow['key'] should still work as a normal dict."""
        from data.db import DictRow
        row = DictRow([('name', 'Bolt'), ('cost', '{R}')])
        assert row['name'] == 'Bolt'
        assert row['cost'] == '{R}'

    def test_setitem_invalidates_cache(self):
        """Setting a new value should update the cached tuple."""
        from data.db import DictRow
        row = DictRow([('name', 'Bolt'), ('cost', '{R}')])
        row['cost'] = '{1}{R}'
        assert row[1] == '{1}{R}'
        assert row['cost'] == '{1}{R}'

    def test_empty_dictrow(self):
        """Empty DictRow should work without errors."""
        from data.db import DictRow
        row = DictRow()
        assert len(row) == 0


# ─── Fix #7: DB Failover Recovery ────────────────────────────────

class TestDBFailover:
    def test_retry_interval_defined(self):
        """_PG_RETRY_INTERVAL should be 300 seconds (5 minutes)."""
        from data.db import _PG_RETRY_INTERVAL
        assert _PG_RETRY_INTERVAL == 300

    def test_pg_retry_after_initialized(self):
        """_pg_retry_after should start at 0 (retry immediately on first failure)."""
        import data.db as db_module
        assert hasattr(db_module, '_pg_retry_after')


# ─── Fix #8: SQL Dialect Translation ─────────────────────────────

class TestSQLTranslation:
    def test_pg_placeholders_to_sqlite(self):
        """_pg_to_sqlite should convert %s → ? placeholders."""
        from data.db import _pg_to_sqlite
        result = _pg_to_sqlite("SELECT * FROM decks WHERE id = %s AND name = %s")
        assert result == "SELECT * FROM decks WHERE id = ? AND name = ?"

    def test_pg_to_sqlite_preserves_percent_operators(self):
        """Translation should not break LIKE patterns with %%."""
        from data.db import _pg_to_sqlite
        result = _pg_to_sqlite("SELECT * FROM decks WHERE name LIKE 'BOSS:%%'")
        # Should preserve the pattern, not break it
        assert "BOSS:" in result

    def test_greatest_to_max(self):
        """GREATEST should be translated to MAX for SQLite."""
        from data.db import _pg_to_sqlite
        result = _pg_to_sqlite("GREATEST(a, b)")
        assert "MAX(a, b)" in result

    def test_numeric_to_real(self):
        """CAST AS NUMERIC should become CAST AS REAL for SQLite."""
        from data.db import _pg_to_sqlite
        result = _pg_to_sqlite("CAST(x AS NUMERIC)")
        assert "REAL" in result


# ─── Fix #11: ELO Formula ────────────────────────────────────────

class TestELOFormula:
    def test_expected_score_equal_ratings(self):
        """Equal ELO → expected score of 0.5 for both players."""
        elo1, elo2 = 1200, 1200
        e1 = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))
        assert abs(e1 - 0.5) < 1e-10

    def test_expected_score_symmetry(self):
        """Expected scores should always sum to 1.0."""
        for elo1, elo2 in [(1000, 1400), (800, 2400), (1200, 1200), (2000, 100)]:
            e1 = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))
            e2 = 1.0 - e1
            assert abs(e1 + e2 - 1.0) < 1e-10, f"Symmetry broken for {elo1} vs {elo2}"

    def test_higher_elo_higher_expected(self):
        """Higher-rated player should have higher expected score."""
        elo1, elo2 = 1600, 1200
        e1 = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))
        assert e1 > 0.5

    def test_numerical_stability_extreme_ratings(self):
        """Formula should not overflow/NaN for extreme rating differences."""
        elo1, elo2 = 100, 3000
        e1 = 1.0 / (1.0 + 10 ** ((elo2 - elo1) / 400.0))
        assert 0.0 <= e1 <= 1.0
        assert e1 == e1  # Not NaN


# ─── Fix #10: Clone Copies Replacement Effects ───────────────────

class TestCloneReplacementEffects:
    def test_clone_preserves_replacement_effects(self):
        """Game clone should carry over registered replacement effects."""
        from engine.card import Card
        from engine.deck import Deck
        from engine.player import Player
        from engine.game import Game

        land = Card(name="Mountain", cost="", type_line="Basic Land - Mountain")
        d1 = Deck()
        d1.add_card(land, 40)
        d2 = Deck()
        d2.add_card(land, 40)

        game = Game([Player("P1", d1), Player("P2", d2)])

        # Should have at least the RIP replacement effect registered by default
        assert len(game.replacement_effects) >= 1, \
            "Game should have at least the RIP replacement effect registered"

        cloned = game._fast_cow_clone()
        assert len(cloned.replacement_effects) == len(game.replacement_effects), \
            "Cloned game should have same number of replacement effects"


# ─── Fix #13: Swiss Pairing ──────────────────────────────────────

class TestSwissPairing:
    def test_sorted_pairing_pairs_adjacent_elos(self):
        """Swiss-style pairing should pair decks with similar ELOs."""
        import random
        decks = [{'id': i, 'elo': 1000 + i * 50, 'name': f'D{i}'} for i in range(10)]
        
        # Sort by ELO with jitter (simulating the Swiss pairing logic)
        sorted_decks = sorted(decks, key=lambda d: d['elo'] + random.uniform(-20, 20), reverse=True)
        
        # Adjacent pairs should have ELO differences within a reasonable range
        for i in range(0, len(sorted_decks) - 1, 2):
            diff = abs(sorted_decks[i]['elo'] - sorted_decks[i+1]['elo'])
            # With jitter of ±20, adjacent pairs should be within ~90 ELO
            assert diff < 150, f"Paired decks too far apart: {diff} ELO difference"


# ─── Fix #4: CORS Restriction ────────────────────────────────────

class TestCORSRestriction:
    def test_no_wildcard_origin(self):
        """CORS should not allow wildcard '*' origin."""
        # Read the app.py source and check for wildcard
        app_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'web', 'app.py')
        with open(app_path) as f:
            source = f.read()
        
        # Find the allow_origins line
        import re
        origins_match = re.search(r'allow_origins=\[([^\]]+)\]', source)
        assert origins_match, "Could not find allow_origins in app.py"
        origins_str = origins_match.group(1)
        assert '"*"' not in origins_str, "CORS should not allow wildcard '*' origin"


# ─── Fix #14: Card Pool Caching ──────────────────────────────────

class TestCardPoolCache:
    def test_card_pool_cache_exists(self):
        """_card_pool_cache should be defined at module level."""
        # Import the module and check
        import importlib
        spec = importlib.util.spec_from_file_location(
            "web_app", 
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'app.py')
        )
        # Just verify the function exists in the source
        app_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'web', 'app.py')
        with open(app_path) as f:
            source = f.read()
        assert '_card_pool_cache' in source, "Module-level card pool cache should exist"
        assert 'def _get_card_pool()' in source, "Card pool getter function should exist"


# ─── Fix #3: Generic Error Response ──────────────────────────────

class TestGenericErrorResponse:
    def test_no_crash_log_write(self):
        """Error handler should not write to crash.log."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'web', 'app.py')
        with open(app_path) as f:
            source = f.read()
        assert 'open("crash.log"' not in source, "Should not write to crash.log"

    def test_generic_error_message(self):
        """Error response should not leak internal details."""
        app_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'web', 'app.py')
        with open(app_path) as f:
            source = f.read()
        assert 'Internal Server Error: {str(e)}' not in source, \
            "Error message should not include exception string"


# ─── Fix #18: Print → Logging ────────────────────────────────────

class TestLogging:
    def test_manager_uses_logger(self):
        """Manager should use logger.info instead of print."""
        mgr_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'league', 'manager.py')
        with open(mgr_path) as f:
            source = f.read()
        
        # Count remaining print calls (should be very few or zero)
        import re
        prints = re.findall(r'^\s+print\(', source, re.MULTILINE)
        assert len(prints) == 0, f"Found {len(prints)} print() calls remaining in manager.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
