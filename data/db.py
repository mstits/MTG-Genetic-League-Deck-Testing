"""Database layer — PostgreSQL with SQLite fallback for local development.

Schema:
    decks      — Deck name, card_list (JSON), ELO rating, division, generation
    matches    — Bo3 match records linking two deck IDs with a winner
    seasons    — Season tracking with start/end timestamps
    card_stats — Per-card win/loss aggregates for metagame analysis
    hall_of_fame — Persistent record of highest-achieving decks

Connection management tries PostgreSQL first via DATABASE_URL, then falls back
to a local SQLite database at data/league.db.
"""

import os
import json
import sqlite3
import logging
import time
from contextlib import contextmanager
from typing import Any, Generator, Optional, Union

# PostgreSQL URL (optional — will fallback to SQLite if unavailable)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mtg_league")

logger = logging.getLogger(__name__)

# Track which backend we're using
_use_sqlite = None
_pg_retry_after = 0  # Timestamp after which we retry PG connection
_PG_RETRY_INTERVAL = 300  # 5 minutes between PG reconnect attempts
_SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'league.db')


class DictRow(dict):
    """A dict subclass that also supports attribute-style access and integer indexing.
    Values are cached as a tuple for O(1) integer access."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._values_cache = tuple(super().values())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values_cache[key]
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._values_cache = tuple(super().values())


class SQLiteDictCursor:
    """Wrapper around sqlite3.Cursor that returns dict-like rows."""
    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()
    
    def execute(self, sql, params=None):
        # Convert PostgreSQL-style %s placeholders to SQLite ? placeholders
        sql = _pg_to_sqlite(sql)
        if params:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self
    
    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self._cursor.description]
        return DictRow(zip(cols, row))
    
    def fetchall(self):
        rows = self._cursor.fetchall()
        if not rows:
            return []
        cols = [desc[0] for desc in self._cursor.description]
        return [DictRow(zip(cols, row)) for row in rows]
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    @property
    def lastrowid(self) -> int:
        """Return the rowid of the last inserted row (SQLite only)."""
        return self._cursor.lastrowid


class SQLiteConnection:
    """Wrapper around sqlite3.Connection to match psycopg2 API."""
    def __init__(self, path):
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.autocommit = False
    
    def cursor(self, cursor_factory=None):
        return SQLiteDictCursor(self._conn)
    
    def commit(self):
        self._conn.commit()
    
    def rollback(self):
        self._conn.rollback()
    
    def execute(self, sql, params=None):
        """Direct execute on the connection (convenience method)."""
        cursor = SQLiteDictCursor(self._conn)
        return cursor.execute(sql, params)
    
    def close(self):
        self._conn.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


def _pg_to_sqlite(sql: str) -> str:
    """Convert PostgreSQL SQL to be SQLite-compatible."""
    import re
    # %s -> ?
    sql = sql.replace('%s', '?')
    # SERIAL PRIMARY KEY -> INTEGER PRIMARY KEY AUTOINCREMENT
    sql = re.sub(r'\bSERIAL\s+PRIMARY\s+KEY\b', 'INTEGER PRIMARY KEY AUTOINCREMENT', sql, flags=re.IGNORECASE)
    # JSONB -> TEXT
    sql = re.sub(r'\bJSONB\b', 'TEXT', sql, flags=re.IGNORECASE)
    # BOOLEAN -> INTEGER
    sql = re.sub(r'\bBOOLEAN\b', 'INTEGER', sql, flags=re.IGNORECASE)
    # TRUE -> 1, FALSE -> 0
    sql = re.sub(r'\bDEFAULT\s+TRUE\b', 'DEFAULT 1', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDEFAULT\s+FALSE\b', 'DEFAULT 0', sql, flags=re.IGNORECASE)
    # REAL is fine in both
    # TIMESTAMP DEFAULT CURRENT_TIMESTAMP is fine in both
    # RETURNING id — not supported in older SQLite, strip it
    sql = re.sub(r'\s*RETURNING\s+\w+\s*$', '', sql, flags=re.IGNORECASE)
    # GREATEST() not in SQLite — replace GREATEST(a, b) with MAX(a, b)
    sql = re.sub(r'\bGREATEST\b', 'MAX', sql, flags=re.IGNORECASE)
    # CAST(x AS NUMERIC) -> CAST(x AS REAL) for SQLite
    sql = re.sub(r'CAST\((.+?) AS NUMERIC\)', r'CAST(\1 AS REAL)', sql, flags=re.IGNORECASE)
    # ROUND with CAST AS REAL works in SQLite
    # ON CONFLICT(column) DO UPDATE — SQLite supports this (upsert)
    return sql


# ─── Connection Pool ──────────────────────────────────────────────────────────
# ThreadedConnectionPool reuses PG connections across requests instead of
# opening/closing on every query. Min=2 idle connections, max=10 under load.
_pg_pool = None  # Lazy-initialized on first successful PG connection


def _get_or_create_pool():
    """Lazily create a ThreadedConnectionPool for PostgreSQL.
    
    Returns the pool if PG is available, None otherwise.
    Thread-safe: worst case is two threads both create a pool on startup,
    but the GIL ensures the global assignment is atomic.
    """
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    try:
        from psycopg2.pool import ThreadedConnectionPool
        _pg_pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
        )
        logger.info("PostgreSQL connection pool created (min=2, max=10)")
        return _pg_pool
    except Exception as e:
        logger.debug("Could not create PG pool: %s", e)
        return None


@contextmanager
def get_db_connection():
    """Yield a database connection with PostgreSQL pooling and SQLite fallback.
    
    Connection lifecycle:
    1. Try to get a connection from the ThreadedConnectionPool (PG)
    2. If PG fails, fall back to SQLite at data/league.db
    3. PG recovery is retried every 5 minutes after a failover
    
    The connection is automatically returned to the pool (PG) or closed (SQLite)
    when the context manager exits.
    """
    global _use_sqlite, _pg_retry_after
    
    # Try PostgreSQL if we haven't decided yet, or if retry interval has elapsed
    should_try_pg = (_use_sqlite is None or 
                     (_use_sqlite is True and time.time() >= _pg_retry_after))
    
    if should_try_pg:
        pool = _get_or_create_pool()
        if pool is not None:
            try:
                conn = pool.getconn()
                conn.autocommit = False
                if _use_sqlite is not False:
                    logger.info("Connected to PostgreSQL%s (pooled)", 
                               " (recovered from SQLite failover)" if _use_sqlite else "")
                _use_sqlite = False
                try:
                    yield conn
                finally:
                    pool.putconn(conn)  # Return to pool, don't close
                return
            except Exception as e:
                if _use_sqlite is None:
                    logger.warning("PostgreSQL unavailable (%s), falling back to SQLite at %s", e, _SQLITE_PATH)
                _use_sqlite = True
                _pg_retry_after = time.time() + _PG_RETRY_INTERVAL
                _pg_pool = None  # Reset pool so it's recreated on next retry
        else:
            if _use_sqlite is None:
                logger.warning("PostgreSQL pool unavailable, falling back to SQLite at %s", _SQLITE_PATH)
            _use_sqlite = True
            _pg_retry_after = time.time() + _PG_RETRY_INTERVAL
    
    if _use_sqlite is False:
        # PostgreSQL mode (confirmed working) — use pool
        pool = _get_or_create_pool()
        if pool is not None:
            try:
                conn = pool.getconn()
                conn.autocommit = False
                try:
                    yield conn
                finally:
                    pool.putconn(conn)
                return
            except Exception:
                logger.warning("PostgreSQL connection lost, failing over to SQLite")
                _use_sqlite = True
                _pg_retry_after = time.time() + _PG_RETRY_INTERVAL
                _pg_pool = None
    
    # SQLite mode
    conn = SQLiteConnection(_SQLITE_PATH)
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all required tables and indexes if they do not exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decks (
                id SERIAL PRIMARY KEY,
                name TEXT,
                card_list JSONB,
                division TEXT DEFAULT 'Provisional',
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                draws INTEGER DEFAULT 0,
                play_wins INTEGER DEFAULT 0,
                draw_wins INTEGER DEFAULT 0,
                elo REAL DEFAULT 1200.0,
                generation INTEGER DEFAULT 0,
                parent_ids JSONB,
                colors TEXT DEFAULT '',
                archetype TEXT DEFAULT 'Unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Matches Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                season_id INTEGER,
                deck1_id INTEGER,
                deck2_id INTEGER,
                winner_id INTEGER,
                game1_winner_id INTEGER,
                turns INTEGER,
                game_log JSONB,
                log_path TEXT,
                p1_mulligans INTEGER DEFAULT 0,
                p2_mulligans INTEGER DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deck1_id) REFERENCES decks(id) ON DELETE CASCADE,
                FOREIGN KEY(deck2_id) REFERENCES decks(id) ON DELETE CASCADE
            )
        ''')
        
        # Add mulligan columns if they don't exist
        try:
            cursor.execute('ALTER TABLE matches ADD COLUMN p1_mulligans INTEGER DEFAULT 0')
            cursor.execute('ALTER TABLE matches ADD COLUMN p2_mulligans INTEGER DEFAULT 0')
        except Exception:
            pass # Columns likely already exist
        
        # Seasons Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS seasons (
                id SERIAL PRIMARY KEY,
                number INTEGER,
                status TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            )
        ''')
        
        # Sideboard Plans Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sideboard_plans (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER,
                opp_archetype TEXT,
                card_in TEXT,
                card_out TEXT,
                count INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deck_id) REFERENCES decks(id) ON DELETE CASCADE,
                UNIQUE(deck_id, opp_archetype, card_in, card_out)
            )
        ''')
        
        # Card Stats Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS card_stats (
                id SERIAL PRIMARY KEY,
                card_name TEXT UNIQUE,
                appearances INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_matches INTEGER DEFAULT 0,
                avg_elo_of_decks REAL DEFAULT 1200.0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Mutations Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mutations (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER,
                generation INTEGER,
                card_added TEXT,
                card_removed TEXT,
                elo_before REAL,
                elo_after REAL,
                elo_delta REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deck_id) REFERENCES decks(id) ON DELETE CASCADE
            )
        ''')
        
        # Hall of Fame
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS hall_of_fame (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER UNIQUE,
                deck_name TEXT,
                peak_elo REAL,
                peak_season INTEGER,
                total_wins INTEGER,
                total_matches INTEGER,
                colors TEXT DEFAULT '',
                card_list JSONB,
                inducted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deck_id) REFERENCES decks(id) ON DELETE CASCADE
            )
        ''')
        
        # Create indexes (IF NOT EXISTS works in both PG and SQLite)
        for idx_sql in [
            'CREATE INDEX IF NOT EXISTS idx_deck_division ON decks(division)',
            'CREATE INDEX IF NOT EXISTS idx_deck_elo ON decks(elo)',
            'CREATE INDEX IF NOT EXISTS idx_deck_colors ON decks(colors)',
            'CREATE INDEX IF NOT EXISTS idx_deck_generation ON decks(generation)',
            'CREATE INDEX IF NOT EXISTS idx_deck_active ON decks(active)',
            'CREATE INDEX IF NOT EXISTS idx_match_decks ON matches(deck1_id, deck2_id)',
            'CREATE INDEX IF NOT EXISTS idx_match_season ON matches(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_match_winner ON matches(winner_id)',
            'CREATE INDEX IF NOT EXISTS idx_card_stats_name ON card_stats(card_name)',
            'CREATE INDEX IF NOT EXISTS idx_mutations_deck ON mutations(deck_id)',
            'CREATE INDEX IF NOT EXISTS idx_hof_elo ON hall_of_fame(peak_elo)',
            'CREATE INDEX IF NOT EXISTS idx_sideboard_deck_arch ON sideboard_plans(deck_id, opp_archetype)',
        ]:
            cursor.execute(idx_sql)
        
        conn.commit()
        backend = "SQLite" if _use_sqlite else "PostgreSQL"
        logger.info("Database initialized (%s)", backend)


def save_deck(name: str, card_list: list[dict], generation: int = 0,
              parent_ids: Optional[list[int]] = None, colors: str = "",
              archetype: Optional[str] = None) -> int:
    """Insert a new deck into the database. Returns the new deck's row ID."""
    if parent_ids is None:
        parent_ids = []
        
    if archetype is None:
        from engine.archetype_classifier import classify_deck
        arch_data = classify_deck(card_list)
        archetype = arch_data.get('archetype', 'Unknown')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO decks (name, card_list, generation, parent_ids, colors, archetype)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        ''', (name, json.dumps(card_list), generation, json.dumps(parent_ids), colors, archetype))
        
        # PG returns the id via RETURNING; SQLite strips it, so use lastrowid
        row = cursor.fetchone()
        if row:
            deck_id = row['id'] if isinstance(row, dict) else row[0]
        else:
            deck_id = cursor.lastrowid
        
        conn.commit()
        return deck_id


def update_card_stats(card_names, won: bool):
    """Update win/loss stats for each card in a deck after a match."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for name in card_names:
            cursor.execute('''
                INSERT INTO card_stats (card_name, appearances, wins, losses, total_matches)
                VALUES (%s, 1, %s, %s, 1)
                ON CONFLICT(card_name) DO UPDATE SET
                    appearances = card_stats.appearances + 1,
                    wins = card_stats.wins + %s,
                    losses = card_stats.losses + %s,
                    total_matches = card_stats.total_matches + 1,
                    last_updated = CURRENT_TIMESTAMP
            ''', (name, 1 if won else 0, 0 if won else 1, 1 if won else 0, 0 if won else 1))
        conn.commit()


def get_top_cards(min_matches: int = 5, limit: int = 20) -> list[dict]:
    """Get cards with highest win rates (minimum match threshold)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT card_name, wins, losses, total_matches,
                   ROUND(CAST(wins AS NUMERIC) / CASE WHEN total_matches = 0 THEN 1 ELSE total_matches END * 100, 1) AS win_rate
            FROM card_stats
            WHERE total_matches >= %s
            ORDER BY win_rate DESC
            LIMIT %s
        ''', (min_matches, limit))
        return [dict(row) for row in cursor.fetchall()]


# ─── Mutation Tracking ────────────────────────────────────────────────────────

def log_mutation(deck_id: int, generation: int, card_added: str,
                 card_removed: str, elo_before: float, elo_after: float):
    """Record a GA mutation (card swap) with ELO impact."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO mutations (deck_id, generation, card_added, card_removed,
                                   elo_before, elo_after, elo_delta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        ''', (deck_id, generation, card_added, card_removed,
              elo_before, elo_after, elo_after - elo_before))
        conn.commit()


def get_mutation_heatmap(limit: int = 50) -> list[dict]:
    """Get top card swaps ranked by average ELO delta."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT card_added, card_removed,
                   ROUND(CAST(AVG(elo_delta) AS NUMERIC), 1) AS avg_delta,
                   COUNT(*) AS swap_count
            FROM mutations
            GROUP BY card_added, card_removed
            HAVING COUNT(*) >= 2
            ORDER BY avg_delta DESC
            LIMIT %s
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


# ─── Hall of Fame ─────────────────────────────────────────────────────────────

def induct_to_hall_of_fame(deck_id: int, deck_name: str, peak_elo: float,
                           peak_season: int, wins: int, matches: int,
                           colors: str = "", card_list: str = "{}"):
    """Induct a deck into the Hall of Fame (or update if already there)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO hall_of_fame (deck_id, deck_name, peak_elo, peak_season,
                                      total_wins, total_matches, colors, card_list)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(deck_id) DO UPDATE SET
                peak_elo = GREATEST(hall_of_fame.peak_elo, EXCLUDED.peak_elo),
                total_wins = EXCLUDED.total_wins,
                total_matches = EXCLUDED.total_matches
        ''', (deck_id, deck_name, peak_elo, peak_season, wins, matches,
              colors, card_list))
        conn.commit()


def get_hall_of_fame(limit: int = 50) -> list[dict]:
    """Get all Hall of Fame inductees, ranked by peak ELO."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT deck_id, deck_name, peak_elo, peak_season,
                   total_wins, total_matches, colors, inducted_at
            FROM hall_of_fame
            ORDER BY peak_elo DESC
            LIMIT %s
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {'SQLite: ' + _SQLITE_PATH if _use_sqlite else 'PostgreSQL'}")
