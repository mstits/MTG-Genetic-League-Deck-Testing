"""Meta analytics routes — metagame analysis, trends, and statistics.

These endpoints power the Meta Analysis tab in the dashboard, providing
insights into archetype distribution, matchup strengths, and game pacing.

Endpoints:
    GET  /api/matchup-matrix     — Color vs color win-rate matrix (top 15 colors)
    GET  /api/turn-distribution  — Bucketed histogram of game lengths
    GET  /api/meta-trends        — Historical archetype popularity + win rates by season
    GET  /api/card-coverage      — Card pool play rates across active decks
"""

import json
import logging
from fastapi import APIRouter
from starlette.responses import JSONResponse
from data.db import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meta"])


# ─── Matchup Matrix ──────────────────────────────────────────────────────────

@router.get("/api/matchup-matrix", response_class=JSONResponse)
async def get_matchup_matrix_json():
    """Color vs color win-rate matrix for the metagame wheel visualization.

    Queries all matches between decks with known color identities, then
    computes pairwise win rates. Filters to the top 15 most-played colors
    and requires a minimum of 5 games per matchup for statistical relevance.

    Returns:
        colors: list[str] — top 15 color identities ranked by total games
        matchups: list[dict] — each with attacker, defender, win_rate, games
        total_matchups: int — total number of unique matchup pairs
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT d1.colors AS c1, d2.colors AS c2,
                   COUNT(*) AS games,
                   SUM(CASE WHEN m.winner_id = d1.id THEN 1 ELSE 0 END) AS c1_wins,
                   SUM(CASE WHEN m.winner_id = d2.id THEN 1 ELSE 0 END) AS c2_wins
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            WHERE d1.colors != '' AND d2.colors != ''
            GROUP BY d1.colors, d2.colors
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC
        ''')
        rows = [dict(r) for r in cursor.fetchall()]

    # Rank colors by total game volume
    color_games = {}
    for r in rows:
        color_games[r['c1']] = color_games.get(r['c1'], 0) + r['games']
        color_games[r['c2']] = color_games.get(r['c2'], 0) + r['games']
    top_colors = sorted(color_games.keys(), key=lambda c: color_games[c], reverse=True)[:15]

    # Build matchup pairs (exclude mirror matches)
    matchups = []
    for r in rows:
        if r['c1'] in top_colors and r['c2'] in top_colors and r['c1'] != r['c2']:
            wr = r['c1_wins'] / r['games'] * 100 if r['games'] > 0 else 50
            matchups.append({
                "attacker": r['c1'],
                "defender": r['c2'],
                "win_rate": round(wr, 1),
                "games": r['games'],
            })

    return {
        "colors": top_colors,
        "matchups": matchups,
        "total_matchups": len(matchups)
    }


# ─── Turn Distribution ───────────────────────────────────────────────────────

@router.get("/api/turn-distribution", response_class=JSONResponse)
async def api_turn_distribution():
    """Bucketed histogram of game lengths across all completed matches.

    Groups game turns into 6 readable buckets (1-5, 6-8, 9-11, 12-14, 15-18, 19+)
    to show whether the meta is fast (aggro-dominated) or grindy (control-heavy).

    Returns:
        distribution: list[dict] — each with range (str) and count (int)
        total_games: int — total games with turn data
        avg_turns: float — weighted average game length
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT turns, COUNT(*) as count
            FROM matches
            WHERE turns IS NOT NULL AND turns > 0
            GROUP BY turns
            ORDER BY turns ASC
        ''')
        rows = [dict(r) for r in cursor.fetchall()]

    # Bucket into readable ranges for the bar chart
    buckets = {}
    for r in rows:
        t = r['turns']
        if t <= 5:
            key = "1-5"
        elif t <= 8:
            key = "6-8"
        elif t <= 11:
            key = "9-11"
        elif t <= 14:
            key = "12-14"
        elif t <= 18:
            key = "15-18"
        else:
            key = "19+"
        buckets[key] = buckets.get(key, 0) + r['count']

    # Maintain display order (shortest to longest)
    ordered_keys = ["1-5", "6-8", "9-11", "12-14", "15-18", "19+"]
    distribution = [{"range": k, "count": buckets.get(k, 0)} for k in ordered_keys]
    total = sum(b['count'] for b in distribution)
    avg_turns = sum(r['turns'] * r['count'] for r in rows) / max(total, 1) if rows else 0

    return JSONResponse({
        "distribution": distribution,
        "total_games": total,
        "avg_turns": round(avg_turns, 1)
    })


# ─── Meta Trends ─────────────────────────────────────────────────────────────

@router.get("/api/meta-trends", response_class=JSONResponse)
async def api_meta_trends():
    """Historical archetype popularity AND win rates aggregated by season.

    Two data series are returned:
    1. Popularity series — unique deck count per archetype per season
    2. Win rate series — average win % per archetype per season

    Data is formatted for direct consumption by chart.js or similar.

    Returns:
        categories: list[str] — season labels ("S1", "S2", ...)
        series: list[dict] — popularity data {name, data: [int, ...]}
        win_rate_series: list[dict] — win rate data {name, data: [float, ...]}
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Popularity: count unique decks per archetype per season
        cursor.execute('''
            SELECT m.season_id, d.archetype, COUNT(DISTINCT d.id) as deck_count
            FROM matches m
            JOIN decks d ON (m.deck1_id = d.id OR m.deck2_id = d.id)
            WHERE m.season_id IS NOT NULL AND d.archetype != 'Unknown'
            GROUP BY m.season_id, d.archetype
            ORDER BY m.season_id ASC
        ''')
        pop_rows = cursor.fetchall()

        # Win rates: average win rate per archetype per season
        cursor.execute('''
            SELECT m.season_id, d.archetype,
                   COUNT(*) as total_games,
                   SUM(CASE WHEN m.winner_id = d.id THEN 1 ELSE 0 END) as wins
            FROM matches m
            JOIN decks d ON (m.deck1_id = d.id OR m.deck2_id = d.id)
            WHERE m.season_id IS NOT NULL AND d.archetype != 'Unknown'
            GROUP BY m.season_id, d.archetype
            ORDER BY m.season_id ASC
        ''')
        wr_rows = cursor.fetchall()

    seasons = sorted(list(set(r['season_id'] for r in pop_rows))) if pop_rows else []

    # Build popularity time series per archetype
    archetypes = {}
    for r in pop_rows:
        arch = r['archetype']
        if arch not in archetypes:
            archetypes[arch] = {s: 0 for s in seasons}
        archetypes[arch][r['season_id']] = r['deck_count']

    series = [{"name": arch, "data": [counts[s] for s in seasons]}
              for arch, counts in archetypes.items()]

    # Build win rate time series per archetype
    wr_archetypes = {}
    for r in wr_rows:
        arch = r['archetype']
        if arch not in wr_archetypes:
            wr_archetypes[arch] = {s: {'wins': 0, 'total': 0} for s in seasons}
        wr_archetypes[arch][r['season_id']]['wins'] += r['wins']
        wr_archetypes[arch][r['season_id']]['total'] += r['total_games']

    win_rate_series = []
    for arch, data_by_season in wr_archetypes.items():
        wr_data = []
        for s in seasons:
            total = data_by_season[s]['total']
            wr = round(data_by_season[s]['wins'] / total * 100, 1) if total > 0 else 0
            wr_data.append(wr)
        win_rate_series.append({"name": arch, "data": wr_data})

    return JSONResponse({
        "categories": [f"S{s}" for s in seasons],
        "series": series,
        "win_rate_series": win_rate_series
    })


# ─── Card Pool Coverage ──────────────────────────────────────────────────────

@router.get("/api/card-coverage")
async def get_card_coverage(limit: int = 100):
    """Report showing which cards from the pool are actually played in active decks.

    Helps identify underexplored design space — cards that exist in the pool
    but never appear in evolved decks may represent untapped synergies.

    Args:
        limit: Maximum cards to return (default: 100, sorted by play rate)

    Returns:
        total_pool: int — total cards available in the card pool
        total_played: int — unique cards appearing in at least one active deck
        coverage_percent: float — played / pool * 100
        total_active_decks: int — number of active decks
        cards: list[dict] — each with name, decks_using, play_rate
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Count active decks for play rate denominator
        cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active = TRUE")
        total_decks = cursor.fetchone()['c']

        if total_decks == 0:
            return {"total_pool": 0, "total_played": 0, "coverage_percent": 0, "cards": []}

        # Get all card lists from active decks
        cursor.execute("SELECT card_list FROM decks WHERE active = TRUE")
        rows = cursor.fetchall()

    # Count how many decks use each card
    card_deck_count = {}
    for row in rows:
        card_list = json.loads(row['card_list'])
        unique_cards = set(card_list) if isinstance(card_list, list) else set(card_list.keys())
        for name in unique_cards:
            card_deck_count[name] = card_deck_count.get(name, 0) + 1

    # Get total card pool size from card search cache
    try:
        from web.app import _get_card_search_cache
        cache = _get_card_search_cache()
        total_pool = len(cache)
    except Exception as e:
        logger.debug("Card search cache unavailable for coverage calc: %s", e)
        total_pool = len(card_deck_count)

    total_played = len(card_deck_count)
    coverage_pct = round(total_played / max(1, total_pool) * 100, 1)

    # Sort by play rate descending, take top N
    sorted_cards = sorted(card_deck_count.items(), key=lambda x: x[1], reverse=True)[:limit]
    cards = [{"name": name, "decks_using": count,
              "play_rate": round(count / total_decks * 100, 1)}
             for name, count in sorted_cards]

    return {
        "total_pool": total_pool,
        "total_played": total_played,
        "coverage_percent": coverage_pct,
        "total_active_decks": total_decks,
        "cards": cards
    }
