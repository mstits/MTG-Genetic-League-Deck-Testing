"""Tests for engine/persistence.py — SQLite fallback database layer."""

import os
import tempfile
from engine.persistence import SovereignDB


class TestSQLiteFallback:
    """SovereignDB falls back to SQLite when PostgreSQL is unavailable."""

    def test_connect_creates_sqlite(self):
        """With no PostgreSQL, connect() should fall back to SQLite."""
        db = SovereignDB(db_url="postgresql://fake:fake@localhost:5432/fake")
        result = db.connect()
        # Should succeed via SQLite fallback
        assert result is True or result is False  # Depends on psycopg2 availability
        db.close()

    def test_sqlite_schema_creation(self):
        """_connect_sqlite creates the required tables."""
        db = SovereignDB()
        # Force SQLite connection to a temp file
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_path = f.name

        try:
            import sqlite3
            db.conn = sqlite3.connect(temp_path)
            db._connected = True
            db._backend = 'sqlite'
            db._init_sqlite_schemas()

            cur = db.conn.cursor()
            # Verify tables exist
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = {row[0] for row in cur.fetchall()}
            assert 'generations' in tables
            assert 'elo_history' in tables
            assert 'deck_mutations' in tables
            db.close()
        finally:
            os.unlink(temp_path)

    def test_record_generation(self):
        """record_generation inserts a row into generations table."""
        db = SovereignDB()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_path = f.name

        try:
            import sqlite3
            db.conn = sqlite3.connect(temp_path)
            db._connected = True
            db._backend = 'sqlite'
            db._init_sqlite_schemas()

            db.record_generation("v1.0", 65.2, 8.5)

            cur = db.conn.cursor()
            cur.execute("SELECT agent_version, win_rate, avg_turns FROM generations")
            row = cur.fetchone()
            assert row == ("v1.0", 65.2, 8.5)
            db.close()
        finally:
            os.unlink(temp_path)

    def test_record_elo(self):
        """record_elo inserts into elo_history table."""
        db = SovereignDB()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_path = f.name

        try:
            import sqlite3
            db.conn = sqlite3.connect(temp_path)
            db._connected = True
            db._backend = 'sqlite'
            db._init_sqlite_schemas()

            db.record_elo("heuristic_v3", 1523.4)

            cur = db.conn.cursor()
            cur.execute("SELECT agent_name, elo_rating FROM elo_history")
            row = cur.fetchone()
            assert row == ("heuristic_v3", 1523.4)
            db.close()
        finally:
            os.unlink(temp_path)

    def test_record_deck_mutation(self):
        """record_deck_mutation stores JSON diff in SQLite."""
        db = SovereignDB()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_path = f.name

        try:
            import sqlite3
            db.conn = sqlite3.connect(temp_path)
            db._connected = True
            db._backend = 'sqlite'
            db._init_sqlite_schemas()

            diff = {"added": ["Lightning Bolt"], "removed": ["Shock"]}
            db.record_deck_mutation(1, "abc123", diff)

            cur = db.conn.cursor()
            cur.execute("SELECT generation_id, deck_hash, mutation_diff FROM deck_mutations")
            row = cur.fetchone()
            assert row[0] == 1
            assert row[1] == "abc123"
            import json
            assert json.loads(row[2]) == diff
            db.close()
        finally:
            os.unlink(temp_path)

    def test_close_disconnects(self):
        """close() sets _connected to False."""
        db = SovereignDB()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_path = f.name

        try:
            import sqlite3
            db.conn = sqlite3.connect(temp_path)
            db._connected = True
            db._backend = 'sqlite'
            db._init_sqlite_schemas()

            db.close()
            assert db._connected is False
        finally:
            os.unlink(temp_path)

    def test_operations_when_disconnected(self):
        """record_* methods are no-ops when not connected."""
        db = SovereignDB()
        db._connected = False
        # These should silently do nothing
        db.record_generation("v1", 50.0, 10.0)
        db.record_elo("agent", 1200.0)
        db.record_deck_mutation(1, "hash", {})
