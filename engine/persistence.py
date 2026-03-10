"""
The Sovereign Ascendant - Persistence Layer

Connects to PostgreSQL/TimescaleDB to store every generation, agent Elo rating, and deck mutation.
"""

import os
import json
import logging
try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError:
    psycopg2 = None

logger = logging.getLogger(__name__)

class SovereignDB:
    def __init__(self, db_url=None):
        self.db_url = db_url or os.environ.get("DATABASE_URL", "postgresql://user:password@localhost:5432/sovereign")
        self.conn = None
        self._connected = False

    def connect(self):
        if not psycopg2:
            logger.warning("psycopg2 not installed. Trying SQLite fallback.")
            return self._connect_sqlite()
            
        try:
            self.conn = psycopg2.connect(self.db_url)
            self._connected = True
            self._backend = 'postgres'
            logger.info("Connected to Sovereign PostgreSQL Database.")
            self._init_schemas()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            return self._connect_sqlite()
    
    def _connect_sqlite(self):
        """SQLite fallback for local development without PostgreSQL."""
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sovereign.db')
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        try:
            self.conn = sqlite3.connect(db_path)
            self._connected = True
            self._backend = 'sqlite'
            logger.info(f"Connected to SQLite fallback: {db_path}")
            self._init_sqlite_schemas()
            return True
        except Exception as e:
            logger.error(f"SQLite fallback also failed: {e}")
            return False
    
    def _init_sqlite_schemas(self):
        cur = self.conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS generations (
            generation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT DEFAULT (datetime('now')),
            agent_version TEXT NOT NULL,
            win_rate REAL NOT NULL,
            avg_turns REAL
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS elo_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            elo_rating REAL NOT NULL,
            timestamp TEXT DEFAULT (datetime('now'))
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS deck_mutations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation_id INTEGER NOT NULL,
            deck_hash TEXT NOT NULL,
            mutation_diff TEXT NOT NULL,
            win_rate_delta REAL
        )''')
        self.conn.commit()

    def _init_schemas(self):
        """Initializes tables using PostgreSQL and TimescaleDB."""
        if not self._connected: return
        
        with self.conn.cursor() as cur:
            # TimescaleDB hypertable for generations
            cur.execute("""
                CREATE TABLE IF NOT EXISTS generations (
                    generation_id SERIAL,
                    time TIMESTAMPTZ NOT NULL,
                    agent_version VARCHAR(50) NOT NULL,
                    win_rate FLOAT NOT NULL,
                    avg_turns FLOAT,
                    PRIMARY KEY (generation_id, time)
                )
            """)
            
            # Timescale command to create hypertable if it doesn't exist
            try:
                cur.execute("SELECT create_hypertable('generations', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);")
            except Exception as e:
                # TimescaleDB extension might not be installed; fallback to standard table partition if needed
                self.conn.rollback()
                pass
                
            cur.execute("""
                CREATE TABLE IF NOT EXISTS elo_history (
                    id SERIAL PRIMARY KEY,
                    agent_name VARCHAR(100) NOT NULL,
                    elo_rating FLOAT NOT NULL,
                    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cur.execute("""
                CREATE TABLE IF NOT EXISTS deck_mutations (
                    id SERIAL PRIMARY KEY,
                    generation_id INT NOT NULL,
                    deck_hash VARCHAR(64) NOT NULL,
                    mutation_diff JSONB NOT NULL,
                    win_rate_delta FLOAT
                )
            """)
            self.conn.commit()

    def record_generation(self, agent_version: str, win_rate: float, avg_turns: float):
        """Logs generation statistics."""
        if not self._connected: return
        if self._backend == 'sqlite':
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO generations (agent_version, win_rate, avg_turns) VALUES (?, ?, ?)",
                (agent_version, win_rate, avg_turns)
            )
            self.conn.commit()
            return
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO generations (time, agent_version, win_rate, avg_turns) VALUES (CURRENT_TIMESTAMP, %s, %s, %s)",
                (agent_version, win_rate, avg_turns)
            )
            self.conn.commit()

    def record_elo(self, agent_name: str, elo: float):
        if not self._connected: return
        if self._backend == 'sqlite':
            cur = self.conn.cursor()
            cur.execute("INSERT INTO elo_history (agent_name, elo_rating) VALUES (?, ?)", (agent_name, elo))
            self.conn.commit()
            return
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO elo_history (agent_name, elo_rating) VALUES (%s, %s)",
                (agent_name, elo)
            )
            self.conn.commit()
            
    def record_deck_mutation(self, generation_id: int, deck_hash: str, diff: dict):
        if not self._connected: return
        if self._backend == 'sqlite':
            cur = self.conn.cursor()
            cur.execute(
                "INSERT INTO deck_mutations (generation_id, deck_hash, mutation_diff) VALUES (?, ?, ?)",
                (generation_id, deck_hash, json.dumps(diff))
            )
            self.conn.commit()
            return
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO deck_mutations (generation_id, deck_hash, mutation_diff) VALUES (%s, %s, %s)",
                (generation_id, deck_hash, Json(diff))
            )
            self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self._connected = False
