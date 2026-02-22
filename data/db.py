"""Database layer — SQLite persistence for decks, matches, and card statistics.

Schema:
    decks      — Deck name, card_list (JSON), ELO rating, division, generation, colors
    matches    — Bo3 match records linking two deck IDs with a winner
    seasons    — Season tracking with start/end timestamps
    card_stats — Per-card win/loss aggregates for metagame analysis

Connection management uses WAL mode for concurrent reads and a 5-second
busy timeout to handle multi-process access from parallel simulations.
"""

import sqlite3
import json
import os
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "league.db")

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')  # Better concurrent read performance
    conn.execute('PRAGMA busy_timeout=5000')  # Wait 5s on lock instead of failing
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Decks Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                card_list JSON, -- JSON serialization of card names/counts
                division TEXT DEFAULT 'Provisional', -- Provisional, Bronze, Silver, Gold, Mythic
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                draws INTEGER DEFAULT 0,
                elo REAL DEFAULT 1200.0,
                generation INTEGER DEFAULT 0,
                parent_ids TEXT, -- JSON list of parent deck IDs
                colors TEXT DEFAULT '', -- Color identity e.g. "RB", "WUG"
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT 1
            )
        ''')
        
        # Matches Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER,
                deck1_id INTEGER,
                deck2_id INTEGER,
                winner_id INTEGER,
                turns INTEGER,
                game_log TEXT, -- JSON array of game events
                log_path TEXT, -- Path to detailed log file if saved
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(deck1_id) REFERENCES decks(id),
                FOREIGN KEY(deck2_id) REFERENCES decks(id)
            )
        ''')
        
        # Seasons Table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER,
                status TEXT, -- Active, Completed
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            )
        ''')
        
        # Card Stats Table (NEW)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS card_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_name TEXT UNIQUE,
                appearances INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_matches INTEGER DEFAULT 0,
                avg_elo_of_decks REAL DEFAULT 1200.0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_deck_division ON decks(division)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_deck_elo ON decks(elo)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_deck_colors ON decks(colors)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_deck_generation ON decks(generation)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_deck_active ON decks(active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_match_decks ON matches(deck1_id, deck2_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_match_season ON matches(season_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_match_winner ON matches(winner_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_card_stats_name ON card_stats(card_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_card_stats_winrate ON card_stats(wins, total_matches)')
        
        conn.commit()
        logger.info("Database initialized at %s", DB_PATH)

def save_deck(name, card_list, generation=0, parent_ids=None, colors=""):
    """Insert a new deck into the database. Returns the new deck's row ID."""
    if parent_ids is None:
        parent_ids = []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO decks (name, card_list, generation, parent_ids, colors)
            VALUES (?, ?, ?, ?, ?)
        ''', (name, json.dumps(card_list), generation, json.dumps(parent_ids), colors))
        conn.commit()
        return cursor.lastrowid

def update_card_stats(card_names, won: bool):
    """Update win/loss stats for each card in a deck after a match."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for name in card_names:
            cursor.execute('''
                INSERT INTO card_stats (card_name, appearances, wins, losses, total_matches)
                VALUES (?, 1, ?, ?, 1)
                ON CONFLICT(card_name) DO UPDATE SET
                    appearances = appearances + 1,
                    wins = wins + ?,
                    losses = losses + ?,
                    total_matches = total_matches + 1,
                    last_updated = CURRENT_TIMESTAMP
            ''', (name, 1 if won else 0, 0 if won else 1, 1 if won else 0, 0 if won else 1))
        conn.commit()

def get_top_cards(min_matches=5, limit=20):
    """Get cards with highest win rates (minimum match threshold)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT card_name, wins, losses, total_matches,
                   ROUND(CAST(wins AS REAL) / CASE WHEN total_matches = 0 THEN 1 ELSE total_matches END * 100, 1) AS win_rate
            FROM card_stats
            WHERE total_matches >= ?
            ORDER BY win_rate DESC
            LIMIT ?
        ''', (min_matches, limit))
        return [dict(row) for row in cursor.fetchall()]

if __name__ == "__main__":
    init_db()
