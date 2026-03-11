"""Deck management routes — detail views, suggestions, comparison, export, and browsing.

These endpoints handle everything related to individual deck inspection,
deck-to-deck comparison, card suggestions, and export functionality.

Endpoints:
    GET  /deck/{deck_id}                 — Deck detail page (HTML)
    GET  /api/deck/{deck_id}/suggestions — AI card suggestions matching color identity
    GET  /api/compare                    — Side-by-side deck comparison
    GET  /api/export/{deck_id}           — Export deck in Arena/MTGO format
    GET  /api/cards/search               — Card name autocomplete
    GET  /decks                          — Browse all decks with filters (HTML)
    GET  /season/{season_id}             — Season detail page (HTML)
    GET  /deck/{deck_id}/lineage         — Genetic lineage family tree (HTML)
    GET  /api/deck/{deck_id}/sideboard-guide — Sideboard recommendations
    GET  /match/{match_id}               — Match detail page (HTML)
"""

import os
import json
import logging
import urllib.parse

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, JSONResponse
from data.db import get_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["decks"])

# ─── Shared State ─────────────────────────────────────────────────────────────
# These are imported from app.py at request time to avoid circular imports.
# They rely on module-level caches that live in app.py.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))


def _load_card_pool() -> dict:
    """Load the card pool from legal_cards.json (or processed_cards.json fallback).
    
    Returns dict mapping card name -> card data dict.
    """
    cp_path = os.path.join(PROJECT_ROOT, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(PROJECT_ROOT, 'data', 'processed_cards.json')
    pool = {}
    if os.path.exists(cp_path):
        with open(cp_path) as f:
            for c in json.load(f):
                pool[c['name']] = c
    return pool


def _parse_cmc(mana_cost: str) -> int:
    """Parse converted mana cost from a mana cost string like '{2}{W}{U}'.
    
    Returns integer CMC. Generic mana (digits) are summed; colored pips each count as 1.
    """
    if not mana_cost:
        return 0
    import re
    total = 0
    for sym in re.findall(r'\{([^}]+)\}', mana_cost):
        if sym.isdigit():
            total += int(sym)
        elif sym != 'X':
            total += 1
    return total


# ─── Card Search Cache ───────────────────────────────────────────────────────
# Lightweight card index for autocomplete (loaded once on first request)

_card_search_cache = None


def get_card_search_cache():
    """Build and cache a lightweight card index for autocomplete.
    
    Each entry has: name, name_lower, mana_cost, type_line, colors, cmc.
    """
    global _card_search_cache
    if _card_search_cache is not None:
        return _card_search_cache
    cp_path = os.path.join(PROJECT_ROOT, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(PROJECT_ROOT, 'data', 'processed_cards.json')
    with open(cp_path) as f:
        raw = json.load(f)
    _card_search_cache = []
    for c in raw:
        name = c.get('name', '')
        _card_search_cache.append({
            'name': name,
            'name_lower': name.lower(),
            'mana_cost': c.get('mana_cost', ''),
            'type_line': c.get('type_line', ''),
            'colors': c.get('colors', []),
            'cmc': c.get('cmc', 0),
        })
    return _card_search_cache


# ─── Deck Detail Page ────────────────────────────────────────────────────────

@router.get("/deck/{deck_id}", response_class=HTMLResponse)
async def view_deck(request: Request, deck_id: int):
    """Render the dedicated page detailing a specific genetic deck.

    Shows: card list with Scryfall links, mana curve chart, archetype classification,
    per-card win rates, Game 1 stats, recent match history, matchup spread by
    opponent archetype, and ELO trajectory chart.
    """
    from web.cache import templates
    card_pool = _load_card_pool()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("<h1>Deck Not Found</h1>", status_code=404)
        deck = dict(row)

        cards = json.loads(deck['card_list'])
        if isinstance(cards, list):
            c = {}
            for n in cards: c[n] = c.get(n, 0) + 1
            cards = c
        deck['cards'] = cards

        # Compute mana curve
        curve = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
        for name, count in cards.items():
            data = card_pool.get(name, {})
            if 'Land' in data.get('type_line', name):
                continue
            cmc = _parse_cmc(data.get('mana_cost', ''))
            bucket = min(cmc, 6)
            curve[bucket] += count
        deck['curve'] = curve

        # Compute archetype from average CMC
        total_cmc = 0
        spell_count = 0
        creature_count = 0
        for name, count in cards.items():
            data = card_pool.get(name, {})
            if 'Land' not in data.get('type_line', name):
                total_cmc += _parse_cmc(data.get('mana_cost', '')) * count
                spell_count += count
                if 'Creature' in data.get('type_line', ''):
                    creature_count += count

        avg_cmc = total_cmc / max(spell_count, 1)
        if avg_cmc < 2.5:
            deck['archetype'] = 'Aggro 🗡️'
        elif avg_cmc < 3.8:
            deck['archetype'] = 'Midrange ⚖️'
        else:
            deck['archetype'] = 'Control 🛡️'
        deck['avg_cmc'] = round(avg_cmc, 2)
        deck['creature_count'] = creature_count
        deck['spell_count'] = spell_count
        deck['draws'] = deck.get('draws', 0) or 0

        # Build card info with Scryfall links + keywords
        card_info = {}
        for name in cards.keys():
            data = card_pool.get(name, {})
            scryfall = data.get('scryfall_uri', '')
            if not scryfall:
                scryfall = f"https://scryfall.com/search?q=%21%22{urllib.parse.quote(name)}%22"

            oracle = data.get('oracle_text', '').lower()
            keywords = []
            for kw in ['flying', 'lifelink', 'deathtouch', 'first strike', 'double strike',
                       'trample', 'haste', 'vigilance', 'reach', 'flash', 'hexproof',
                       'menace', 'indestructible', 'defender']:
                if kw in oracle:
                    keywords.append(kw.title())

            has_etb = 'enters the battlefield' in oracle or 'enters play' in oracle

            card_info[name] = {
                'scryfall_uri': scryfall,
                'type_line': data.get('type_line', ''),
                'mana_cost': data.get('mana_cost', ''),
                'keywords': keywords,
                'has_etb': has_etb,
                'win_rate': None,
            }

        # Fetch per-card win rates from card_stats
        card_names_list = list(cards.keys())
        if card_names_list:
            placeholders = ','.join(['%s'] * len(card_names_list))
            cursor.execute(f'''
                SELECT card_name, wins, total_matches,
                       ROUND(CAST(wins AS NUMERIC) / CASE WHEN total_matches = 0 THEN 1 ELSE total_matches END * 100, 1) AS win_rate
                FROM card_stats
                WHERE card_name IN ({placeholders}) AND total_matches >= 3
            ''', tuple(card_names_list))
            for row in cursor.fetchall():
                r = dict(row)
                if r['card_name'] in card_info:
                    card_info[r['card_name']]['win_rate'] = r['win_rate']
                    card_info[r['card_name']]['win_rate_matches'] = r['total_matches']

        # Game 1 Stats
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE deck1_id = %s OR deck2_id = %s', (deck_id, deck_id))
        total_p_matches = cursor.fetchone()['c']
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE game1_winner_id = %s', (deck_id,))
        game1_wins = cursor.fetchone()['c']

        deck['total_matches'] = total_p_matches
        deck['game1_wins'] = game1_wins
        if total_p_matches > 0:
            deck['game1_winrate'] = f"{game1_wins / total_p_matches * 100:.1f}%"
        else:
            deck['game1_winrate'] = "N/A"

        # Recent matches
        cursor.execute('''
            SELECT m.id as match_id, m.turns, m.timestamp,
                   d1.name AS deck1_name, d2.name AS deck2_name,
                   w.name AS winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            LEFT JOIN decks w ON m.winner_id = w.id
            WHERE m.deck1_id = %s OR m.deck2_id = %s
            ORDER BY m.timestamp DESC
            LIMIT 15
        ''', (deck_id, deck_id))
        matches = [dict(row) for row in cursor.fetchall()]

        # Matchup Spread — win% by opponent archetype
        matchup_spread = []
        try:
            cursor.execute('''
                SELECT 
                    COALESCE(d_opp.archetype, 'Unknown') as archetype,
                    COUNT(*) as total,
                    SUM(CASE WHEN m.winner_id = %s THEN 1 ELSE 0 END) as wins
                FROM matches m
                JOIN decks d_opp ON d_opp.id = CASE 
                    WHEN m.deck1_id = %s THEN m.deck2_id 
                    ELSE m.deck1_id END
                WHERE m.deck1_id = %s OR m.deck2_id = %s
                GROUP BY COALESCE(d_opp.archetype, 'Unknown')
                ORDER BY total DESC
            ''', (deck_id, deck_id, deck_id, deck_id))
            for row in cursor.fetchall():
                r = dict(row)
                r['win_rate'] = round(r['wins'] / max(r['total'], 1) * 100, 1)
                matchup_spread.append(r)
        except Exception as e:
            logger.debug("Matchup spread query failed (archetype column may be missing): %s", e)

        # Elo History — compute trajectory from match results
        elo_history = []
        try:
            cursor.execute('''
                SELECT m.winner_id, m.timestamp
                FROM matches m
                WHERE m.deck1_id = %s OR m.deck2_id = %s
                ORDER BY m.timestamp ASC
            ''', (deck_id, deck_id))
            running_elo = 1200.0
            for row in cursor.fetchall():
                r = dict(row)
                if r['winner_id'] == deck_id:
                    running_elo += 16
                elif r['winner_id'] is not None:
                    running_elo -= 16
                elo_history.append(round(running_elo, 1))
        except Exception as e:
            logger.debug("Elo history computation failed: %s", e)

    return templates.TemplateResponse(request, "deck.html", {
        "deck": deck, "matches": matches,
        "card_info": card_info, "matchup_spread": matchup_spread,
        "elo_history": elo_history
    })


# ─── Card Suggestions ────────────────────────────────────────────────────────

@router.get("/api/deck/{deck_id}/suggestions", response_class=JSONResponse)
async def deck_suggestions(deck_id: int):
    """Suggest high win-rate cards the deck could add (color-compatible).

    Queries card_stats for cards with >= 5 matches and high win rates,
    filters for color identity compatibility, and excludes lands.

    Returns:
        suggestions: list of up to 10 cards with name, win_rate, matches, type, cost
    """
    card_pool = _load_card_pool()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT card_list, colors FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"suggestions": []})

        deck_cards = json.loads(row['card_list'])
        if isinstance(deck_cards, list):
            deck_card_names = set(deck_cards)
        else:
            deck_card_names = set(deck_cards.keys())

        deck_colors = set(row['colors'] or '')

        cursor.execute('''
            SELECT card_name, wins, total_matches,
                   ROUND(CAST(wins AS NUMERIC) / CASE WHEN total_matches = 0 THEN 1 ELSE total_matches END * 100, 1) AS win_rate
            FROM card_stats
            WHERE total_matches >= 5
            ORDER BY win_rate DESC
            LIMIT 200
        ''')

        suggestions = []
        for r in cursor.fetchall():
            card = dict(r)
            name = card['card_name']
            if name in deck_card_names:
                continue

            pool_data = card_pool.get(name, {})
            card_colors = set(pool_data.get('color_identity', []))
            type_line = pool_data.get('type_line', '')

            if 'Land' in type_line:
                continue
            if card_colors and deck_colors and not card_colors.issubset(deck_colors):
                continue

            suggestions.append({
                'name': name,
                'win_rate': card['win_rate'],
                'matches': card['total_matches'],
                'type_line': type_line,
                'mana_cost': pool_data.get('mana_cost', ''),
            })

            if len(suggestions) >= 10:
                break

    return JSONResponse({"suggestions": suggestions})


# ─── Deck Comparison ──────────────────────────────────────────────────────────

@router.get("/api/compare", response_class=JSONResponse)
async def compare_decks(deck1_id: int, deck2_id: int):
    """Compare two decks side-by-side: shared cards, unique cards, stat diffs.

    Returns overlap percentage, per-deck stats, and categorized card lists.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        decks = {}
        for did in (deck1_id, deck2_id):
            cursor.execute('SELECT id, name, card_list, elo, wins, losses, draws, colors, archetype FROM decks WHERE id = %s', (did,))
            row = cursor.fetchone()
            if not row:
                return JSONResponse({"error": f"Deck {did} not found"}, status_code=404)
            d = dict(row)
            cards = json.loads(d['card_list'])
            if isinstance(cards, list):
                c = {}
                for n in cards: c[n] = c.get(n, 0) + 1
                cards = c
            d['cards'] = cards
            decks[did] = d

        d1, d2 = decks[deck1_id], decks[deck2_id]
        all_cards = set(d1['cards'].keys()) | set(d2['cards'].keys())
        shared = []
        only_d1 = []
        only_d2 = []

        for name in sorted(all_cards):
            c1 = d1['cards'].get(name, 0)
            c2 = d2['cards'].get(name, 0)
            if c1 > 0 and c2 > 0:
                shared.append({'name': name, 'count1': c1, 'count2': c2})
            elif c1 > 0:
                only_d1.append({'name': name, 'count': c1})
            else:
                only_d2.append({'name': name, 'count': c2})

    return JSONResponse({
        "deck1": {"id": d1['id'], "name": d1['name'], "elo": round(d1['elo']),
                  "wins": d1['wins'], "losses": d1['losses'], "colors": d1['colors'],
                  "archetype": d1['archetype'], "total_cards": sum(d1['cards'].values())},
        "deck2": {"id": d2['id'], "name": d2['name'], "elo": round(d2['elo']),
                  "wins": d2['wins'], "losses": d2['losses'], "colors": d2['colors'],
                  "archetype": d2['archetype'], "total_cards": sum(d2['cards'].values())},
        "shared": shared,
        "only_deck1": only_d1,
        "only_deck2": only_d2,
        "overlap_pct": round(len(shared) / max(len(all_cards), 1) * 100, 1)
    })


# ─── Deck Export ──────────────────────────────────────────────────────────────

@router.get("/api/export/{deck_id}")
async def export_deck(deck_id: int, format: str = "arena"):
    """Export a deck in Arena or MTGO format, including generated sideboard.

    The sideboard is derived from historical sideboarding plans (most commonly
    boarded-in cards for the deck across all matchups).

    Args:
        deck_id: League deck ID
        format: 'arena' or 'mtgo' (default: arena)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, card_list FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "Deck not found"}, status_code=404)

        cursor.execute('''
            SELECT card_in, SUM(count) as total
            FROM sideboard_plans
            WHERE deck_id = %s
            GROUP BY card_in
            ORDER BY total DESC
            LIMIT 15
        ''', (deck_id,))
        sb_rows = cursor.fetchall()

    deck = dict(row)
    cards = json.loads(deck['card_list'])
    if isinstance(cards, list):
        c = {}
        for n in cards: c[n] = c.get(n, 0) + 1
        cards = c

    sideboard_cards = {r['card_in']: 1 for r in sb_rows} if sb_rows else {}

    if format == "arena":
        lines = [f"// {deck['name']}", "// Exported from MTG Genetic League", ""]
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        if sideboard_cards:
            lines.extend(["", "Sideboard"])
            for name in sorted(sideboard_cards.keys()):
                lines.append(f"1 {name}")
        text = "\n".join(lines)
    else:
        lines = [f"// {deck['name']}"]
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        if sideboard_cards:
            lines.extend(["", "Sideboard"])
            for name in sorted(sideboard_cards.keys()):
                lines.append(f"1 {name}")
        text = "\n".join(lines)

    return JSONResponse({"deck_name": deck['name'], "format": format, "decklist": text,
                         "sideboard_size": len(sideboard_cards)})


# ─── Card Search (Autocomplete) ──────────────────────────────────────────────

@router.get("/api/cards/search")
async def search_cards(q: str = ""):
    """Card name autocomplete for the deck builder.

    Returns up to 10 matches. Prefix matches are prioritized over substring matches.
    Requires minimum 2 characters.
    """
    if not q or len(q) < 2:
        return JSONResponse([])
    query = q.lower()
    cache = get_card_search_cache()
    prefix = []
    substring = []
    for card in cache:
        if card['name_lower'].startswith(query):
            prefix.append(card)
        elif query in card['name_lower']:
            substring.append(card)
        if len(prefix) + len(substring) >= 10:
            break
    results = (prefix + substring)[:10]
    return JSONResponse([{
        'name': r['name'],
        'mana_cost': r['mana_cost'],
        'type_line': r['type_line'],
        'colors': r['colors'],
        'cmc': r['cmc'],
    } for r in results])


# ─── Browse Decks ─────────────────────────────────────────────────────────────

@router.get("/decks", response_class=HTMLResponse)
async def browse_decks(request: Request, page: int = 1, active: int = None,
                       boss: int = None, colors: str = None, division: str = None):
    """Browse all league decks with optional filters and pagination.

    Filters:
        active: 1=active only, 0=retired only
        boss: 1=boss decks only, 0=exclude bosses
        colors: exact color string match (e.g. 'WU')
        division: division tier filter
    """
    from web.cache import templates
    page_size = 50
    offset = (page - 1) * page_size

    with get_db_connection() as conn:
        cursor = conn.cursor()

        query = "SELECT * FROM decks WHERE 1=1"
        params = []

        if active is not None:
            query += " AND active = %s"
            params.append(active)

        if boss is not None:
            if boss:
                query += " AND name LIKE 'BOSS:%'"
            else:
                query += " AND name NOT LIKE 'BOSS:%'"

        if colors:
            query += " AND colors = %s"
            params.append(colors)

        if division:
            query += " AND division = %s"
            params.append(division)

        count_query = query.replace("SELECT *", "SELECT COUNT(*) as c")
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()['c']

        query += " ORDER BY elo DESC LIMIT %s OFFSET %s"
        params.extend([page_size, offset])
        cursor.execute(query, params)
        decks = [dict(r) for r in cursor.fetchall()]

    return templates.TemplateResponse(request, "decks.html", {
        "decks": decks,
        "count": total_count,
        "page": page,
        "next_page": page + 1 if (page * page_size) < total_count else None,
        "prev_page": page - 1 if page > 1 else None,
        "filters": {"active": active, "boss": boss, "colors": colors, "division": division}
    })


# ─── Season Detail ────────────────────────────────────────────────────────────

@router.get("/season/{season_id}", response_class=HTMLResponse)
async def view_season(request: Request, season_id: int):
    """Render the season detail page showing top performer and match count."""
    from web.cache import templates
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE season_id = %s', (season_id,))
        match_count = cursor.fetchone()['c']

        if match_count == 0:
            return HTMLResponse(f"<h1>Season {season_id} not found or has no matches</h1>", status_code=404)

        cursor.execute('''
            SELECT d.id, d.name, d.colors, d.elo, d.wins || 'W-' || d.losses || 'L' as record,
                   COUNT(m.id) as season_games,
                   SUM(CASE WHEN m.winner_id = d.id THEN 1 ELSE 0 END) as season_wins
            FROM decks d
            JOIN matches m ON (m.deck1_id = d.id OR m.deck2_id = d.id)
            WHERE m.season_id = %s
            GROUP BY d.id
            ORDER BY season_wins DESC
            LIMIT 1
        ''', (season_id,))
        winner_row = cursor.fetchone()
        winner = dict(winner_row) if winner_row else None

    return templates.TemplateResponse(request, "season.html", {
        "season": {
            "id": season_id,
            "match_count": match_count,
            "winner": winner,
            "promotions": [],
            "relegations": []
        }
    })


# ─── Deck Lineage (Family Tree) ──────────────────────────────────────────────

@router.get("/deck/{deck_id}/lineage", response_class=HTMLResponse)
async def view_lineage(request: Request, deck_id: int):
    """Render the genetic lineage tree for a deck using Cytoscape.js.

    Traverses up to 5 ancestor generations and 1 child generation to build
    a directed graph showing the deck's evolutionary history.
    """
    from web.cache import templates
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("Deck not found", status_code=404)
        current_deck = dict(row)

        nodes = {}
        edges = set()

        def get_deck_info(d_id):
            cursor.execute('SELECT id, name, elo, generation, parent_ids FROM decks WHERE id = %s', (d_id,))
            return cursor.fetchone()

        nodes[deck_id] = {
            'name': current_deck['name'],
            'elo': current_deck['elo'],
            'gen': current_deck['generation'],
            'type': 'current'
        }

        # Traverse ancestors (up to 5 generations)
        queue = [(deck_id, 0)]
        visited = {deck_id}

        while queue:
            curr_id, depth = queue.pop(0)
            if depth >= 5:
                continue

            row = get_deck_info(curr_id)
            if not row:
                continue

            parents = json.loads(row['parent_ids']) if row['parent_ids'] else []
            if isinstance(parents, int):
                parents = [parents]

            for p_id in parents:
                if p_id not in nodes:
                    p_info = get_deck_info(p_id)
                    if p_info:
                        nodes[p_id] = {
                            'name': p_info['name'],
                            'elo': p_info['elo'],
                            'gen': p_info['generation'],
                            'type': 'ancestor'
                        }
                        if p_id not in visited:
                            visited.add(p_id)
                            queue.append((p_id, depth + 1))
                edges.add((p_id, curr_id))

        # Traverse immediate children (down 1 generation)
        cursor.execute("SELECT id, name, elo, generation FROM decks WHERE parent_ids LIKE %s", (f'%{deck_id}%',))
        children = cursor.fetchall()

        for child in children:
            c_id = child['id']
            if c_id not in nodes:
                nodes[c_id] = {
                    'name': child['name'],
                    'elo': child['elo'],
                    'gen': child['generation'],
                    'type': 'child'
                }
            nodes[c_id]['type'] = 'child'
            edges.add((deck_id, c_id))

        # Build Cytoscape.js graph data
        cy_nodes = []
        for n_id, data in nodes.items():
            node_type = data['type']
            if 'BOSS' in data['name']:
                node_type = 'boss'
            cy_nodes.append({
                'id': str(n_id),
                'label': data['name'],
                'elo': round(data['elo']),
                'gen': data['gen'],
                'type': node_type
            })

        cy_edges = [{'source': str(src), 'target': str(dst)} for src, dst in edges]
        no_lineage = len(nodes) <= 1 and len(edges) == 0

    return templates.TemplateResponse(request, "lineage.html", {
        "deck": current_deck,
        "cy_nodes": json.dumps(cy_nodes),
        "cy_edges": json.dumps(cy_edges),
        "no_lineage": no_lineage
    })


# ─── Sideboard Guide ─────────────────────────────────────────────────────────

@router.get("/api/deck/{deck_id}/sideboard-guide")
async def get_sideboard_guide(deck_id: int):
    """Retrieve aggregated sideboarding heuristics for a specific deck.

    Analyzes historical sideboard plans to determine the most common
    card swaps (in/out) for each opponent archetype. Returns the top 6
    opponent matchups with up to 7 in/out recommendations each.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT opp_archetype, card_in, card_out, SUM(count) as total
            FROM sideboard_plans
            WHERE deck_id = %s
            GROUP BY opp_archetype, card_in, card_out
            ORDER BY opp_archetype, total DESC
        ''', (deck_id,))
        rows = cursor.fetchall()

    guide = {}
    for r in rows:
        opp = r['opp_archetype'].title() if r['opp_archetype'] else 'Unknown'
        if opp not in guide:
            guide[opp] = []
        guide[opp].append({
            'card_in': r['card_in'],
            'card_out': r['card_out'],
            'count': r['total']
        })

    # Top 6 opponents by sideboarding frequency
    opp_totals = {opp: sum(s['count'] for s in swaps) for opp, swaps in guide.items()}
    top_opponents = sorted(opp_totals.keys(), key=lambda o: opp_totals[o], reverse=True)[:6]

    summary = {}
    for opp in top_opponents:
        swaps = guide[opp]
        cards_in = {}
        cards_out = {}
        for s in swaps:
            cards_in[s['card_in']] = cards_in.get(s['card_in'], 0) + s['count']
            cards_out[s['card_out']] = cards_out.get(s['card_out'], 0) + s['count']

        top_in = sorted(cards_in.items(), key=lambda x: x[1], reverse=True)[:7]
        top_out = sorted(cards_out.items(), key=lambda x: x[1], reverse=True)[:7]

        summary[opp] = {
            "in": [{"name": k} for k, v in top_in],
            "out": [{"name": k} for k, v in top_out]
        }

    return JSONResponse({"deck_id": deck_id, "guide": summary})


# ─── Match Detail ─────────────────────────────────────────────────────────────

@router.get("/match/{match_id}", response_class=HTMLResponse)
async def view_match(request: Request, match_id: int):
    """Render the detailed view of a specific match log.

    Shows deck information for both players, game result, turn count,
    and the full game log (from log file or DB summary).
    """
    from web.cache import templates, BASE_DIR
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.*, 
                   d1.name AS deck1_name, d1.id AS d1_id, d1.elo AS d1_elo, d1.colors AS d1_colors, d1.archetype AS d1_archetype,
                   d2.name AS deck2_name, d2.id AS d2_id, d2.elo AS d2_elo, d2.colors AS d2_colors, d2.archetype AS d2_archetype,
                   w.name AS winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            LEFT JOIN decks w ON m.winner_id = w.id
            WHERE m.id = %s
        ''', (match_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("<h1>Match Not Found</h1>", status_code=404)
        match = dict(row)

    # Prefer full log from file, fall back to DB summary
    game_log = []
    log_path = match.get('log_path', '')
    allowed_logs_dir = os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), 'logs'))
    if log_path:
        abs_log_path = os.path.abspath(log_path)
        if abs_log_path.startswith(allowed_logs_dir) and os.path.exists(abs_log_path):
            with open(abs_log_path, 'r') as f:
                game_log = f.read().splitlines()

    if not game_log:
        game_log = json.loads(match.get('game_log', '[]') or '[]')

    return templates.TemplateResponse(request, "match.html", { "match": match, "game_log": game_log
    })
