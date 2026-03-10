"""Dashboard and page view routes — HTML fragments and full-page renders.

These endpoints serve the main dashboard UI, leaderboard, meta analysis,
match history, stats overview, matchup matrices, and match replay pages.

Endpoints:
    GET  /                          — Main dashboard homepage
    GET  /api/admin/meta-map        — PCA-projected deck similarity map
    GET  /api/leaderboard           — Ranked deck leaderboard (HTML/JSON)
    GET  /api/top-cards             — Top win-rate cards table fragment
    GET  /api/top-cards-sidebar     — Compact sidebar top cards
    GET  /api/meta                  — Full meta analysis with charts
    GET  /api/match-history         — Recent match history table
    GET  /api/stats                 — League statistics summary
    GET  /api/matchups              — Color matchup matrix
    GET  /matches                   — Full matches page
    GET  /match/{match_id}/replay   — Interactive match replay
    GET  /api/match/{match_id}      — Match data JSON API
"""

import os
import json
import logging
import html as html_mod
import urllib.parse

from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, JSONResponse
from data.db import get_db_connection, get_top_cards

logger = logging.getLogger(__name__)

router = APIRouter(tags=["views"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# ─── Helpers ──────────────────────────────────────────────────────────────────

# Color icon mapping reused across multiple view endpoints
COLOR_ICON_MAP = {
    'W': '<i class="ms ms-w ms-cost ms-shadow text-base"></i>',
    'U': '<i class="ms ms-u ms-cost ms-shadow text-base"></i>',
    'B': '<i class="ms ms-b ms-cost ms-shadow text-base"></i>',
    'R': '<i class="ms ms-r ms-cost ms-shadow text-base"></i>',
    'G': '<i class="ms ms-g ms-cost ms-shadow text-base"></i>',
}

COLOR_BAR_MAP = {
    'W': '#F9D423', 'U': '#2563eb', 'B': '#7c3aed',
    'R': '#ef4444', 'G': '#22c55e', 'C': '#9ca3af'
}

ARCHETYPE_ICONS = {
    'Aggro': '⚔️', 'Control': '🛡️', 'Combo': '✨',
    'Midrange': '⚖️', 'Tempo': '💨', 'Ramp': '🌳'
}

ARCHETYPE_COLORS = {
    'Aggro': '#ef4444', 'Control': '#3b82f6', 'Combo': '#a855f7',
    'Midrange': '#eab308', 'Tempo': '#06b6d4', 'Ramp': '#22c55e'
}

DIV_CSS = {
    'Provisional': 'bg-gray-600 text-gray-200',
    'Bronze': 'bg-amber-700 text-amber-100',
    'Silver': 'bg-slate-500 text-slate-100',
    'Gold': 'bg-yellow-600 text-yellow-100',
    'Mythic': 'bg-purple-600 text-purple-100 font-bold'
}


def _get_templates():
    from web.cache import templates
    return templates


def get_card_search_cache():
    from web.cache import get_card_search_cache
    return get_card_search_cache()


# ─── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the main dashboard overview page."""
    return _get_templates().TemplateResponse("index.html", {"request": request})


# ─── Meta Map (PCA) ──────────────────────────────────────────────────────────

@router.get("/api/admin/meta-map")
async def get_meta_map():
    """Returns 2D projected coordinates of deck fingerprints via PCA.

    Builds sparse card-count vectors from each deck's card_list,
    then projects to 2D using PCA. No external vector DB required.
    """
    try:
        from sklearn.decomposition import PCA
        import numpy as np

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, name, colors, elo, card_list FROM decks WHERE active=1')
            decks = [dict(row) for row in cursor.fetchall()]

        if len(decks) < 3:
            return {"points": []}

        all_cards = set()
        deck_cards = []
        for d in decks:
            try:
                cl = json.loads(d['card_list'])
                if isinstance(cl, list):
                    counts = {}
                    for n in cl:
                        counts[n] = counts.get(n, 0) + 1
                    cl = counts
            except Exception as e:
                logger.debug("Failed to parse card_list for network graph: %s", e)
                cl = {}
            deck_cards.append(cl)
            all_cards.update(cl.keys())

        card_vocab = sorted(all_cards)
        card_idx = {name: i for i, name in enumerate(card_vocab)}

        X = np.zeros((len(decks), len(card_vocab)), dtype=np.float32)
        for i, cl in enumerate(deck_cards):
            for name, count in cl.items():
                if name in card_idx:
                    X[i, card_idx[name]] = count

        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1
        X = X / norms

        pca = PCA(n_components=2)
        X_2d = pca.fit_transform(X)

        points = []
        for i, d in enumerate(decks):
            points.append({
                "x": float(X_2d[i][0]),
                "y": float(X_2d[i][1]),
                "id": d['id'],
                "name": d['name'],
                "colors": d.get('colors', ''),
                "elo": float(d['elo'])
            })

        return {"points": points}
    except Exception as e:
        logger.warning("Meta-Map Error: %s", e)
        return {"points": []}


# ─── Leaderboard ──────────────────────────────────────────────────────────────

@router.get("/api/leaderboard")
async def get_leaderboard(request: Request, format: str = "html", limit: int = 50):
    """Render the HTML fragment or JSON for the ranked deck leaderboard."""
    limit = min(limit, 100)  # Cap at 100
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, division, wins, losses, draws, elo, colors, card_list 
            FROM decks 
            WHERE active=1 
            ORDER BY elo DESC 
            LIMIT %s
        ''', (limit,))
        decks = [dict(row) for row in cursor.fetchall()]
    
    # JSON format for opponent selector and API consumers
    if format == 'json':
        return JSONResponse([{
            'id': d['id'], 'name': d['name'], 'elo': d['elo'],
            'colors': d.get('colors', ''), 'division': d.get('division', ''),
            'wins': d.get('wins', 0), 'losses': d.get('losses', 0)
        } for d in decks])
        
    from engine.salt_score import calculate_salt_score
    import json
    
    html = ""
    for i, d in enumerate(decks):
        decisive = d['wins'] + d['losses']
        wr = f"{d['wins']/decisive*100:.0f}%" if decisive > 0 else "—"
        draws = d.get('draws', 0) or 0
        colors = d.get('colors', '') or ''
        
        try:
            decklist = json.loads(d.get('card_list') or '{}')
        except Exception as e:
            logger.debug("Failed to parse card_list for salt score: %s", e)
            decklist = {}
            
        bracket = calculate_salt_score(decklist).get('bracket', 1)
        
        color_map = {
            'W': '<i class="ms ms-w ms-cost ms-shadow text-base"></i>',
            'U': '<i class="ms ms-u ms-cost ms-shadow text-base"></i>',
            'B': '<i class="ms ms-b ms-cost ms-shadow text-base"></i>',
            'R': '<i class="ms ms-r ms-cost ms-shadow text-base"></i>',
            'G': '<i class="ms ms-g ms-cost ms-shadow text-base"></i>'
        }
        color_badges = ''.join(color_map.get(c, '') for c in colors)
        
        div_colors = {
            'Provisional': 'bg-gray-600 text-gray-200',
            'Bronze': 'bg-amber-700 text-amber-100',
            'Silver': 'bg-slate-500 text-slate-100',
            'Gold': 'bg-yellow-600 text-yellow-100',
            'Mythic': 'bg-purple-600 text-purple-100 font-bold'
        }
        div_cls = div_colors.get(d['division'], 'bg-gray-600')
        
        is_boss = "BOSS:" in d['name']
        name_cls = "text-red-400 font-bold" if is_boss else "text-blue-400 font-medium"
        
        # Archetype classification — real analysis instead of name guessing
        from engine.archetype_classifier import classify_deck
        cache = get_card_search_cache() or []
        card_pool_dict = {c['name']: c for c in cache} if isinstance(cache, list) else cache
        arch_info = classify_deck(decklist, card_pool_dict)
        arch_emoji = {"Aggro": "🗡️", "Control": "🛡️", "Combo": "⚡", "Midrange": "⚖️"}.get(arch_info['archetype'], "")
        arch_cls = {"Aggro": "text-red-400", "Control": "text-blue-400", "Combo": "text-purple-400", "Midrange": "text-green-400"}.get(arch_info['archetype'], "text-gray-400")
        arch_label = arch_info['archetype']
        
        # Row background based on rank
        if i < 3:
            row_cls = "border-b border-gray-700 hover:bg-yellow-900/20 cursor-pointer transition-colors"
        else:
            row_cls = "border-b border-gray-700 hover:bg-gray-700/50 cursor-pointer transition-colors"
        
        safe_name = html_mod.escape(d['name'])
        safe_div = html_mod.escape(d['division'])
        html += f"""
        <tr class="{row_cls}" onclick="window.location.href='/deck/{d['id']}'">
            <td class="p-3 text-gray-500">{i+1}</td>
            <td class="p-3 {name_cls}">{arch_emoji} {safe_name}</td>
            <td class="p-3">{color_badges}</td>
            <td class="p-3 text-sm {arch_cls} font-medium">{arch_label}</td>
            <td class="p-3 text-center font-bold text-gray-300">🧂 {bracket}</td>
            <td class="p-3"><span class="px-2 py-1 rounded-full text-xs {div_cls}">{safe_div}</span></td>
            <td class="p-3 font-mono font-bold text-white">{d['elo']:.0f}</td>
            <td class="p-3 text-sm text-gray-300">{d['wins']}W-{d['losses']}L<span class="text-gray-500">-{draws}D</span></td>
            <td class="p-3 font-mono text-sm text-gray-300">{wr}</td>
        </tr>
        """
    return html
@router.get("/api/top-cards", response_class=HTMLResponse)
async def get_top_cards_api(request: Request):
    """Render the HTML fragment showing the cards with the highest win rates."""
    import urllib.parse
    cards = get_top_cards(min_matches=5, limit=30)
    
    html = ""
    for i, c in enumerate(cards):
        wr = c['win_rate']
        if wr >= 60:
            wr_cls = "text-green-400 font-bold"
        elif wr >= 50:
            wr_cls = "text-blue-400"
        else:
            wr_cls = "text-red-400"
        
        encoded_name = urllib.parse.quote(c['card_name'])
        html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-700/50 transition-colors">
            <td class="p-3 text-gray-500">{i+1}</td>
            <td class="p-3 font-medium text-gray-200">
                <a href="https://scryfall.com/search?q=%21%22{encoded_name}%22" target="_blank" class="hover:text-blue-400 hover:underline">
                    {html_mod.escape(c['card_name'])}
                </a>
            </td>
            <td class="p-3 font-mono {wr_cls}">{wr}%</td>
            <td class="p-3 text-sm text-gray-400">{c['wins']}W-{c['losses']}L</td>
            <td class="p-3 text-sm text-gray-500">{c['total_matches']}</td>
        </tr>
        """
    
    if not cards:
        html = '<tr><td colspan="5" class="p-6 text-center text-gray-500">Gathering card data... (needs 5+ matches per card)</td></tr>'
    
    return html
@router.get("/api/top-cards-sidebar", response_class=HTMLResponse)
async def get_top_cards_sidebar(request: Request):
    """Compact top cards for the dashboard sidebar."""
    import urllib.parse
    cards = get_top_cards(min_matches=5, limit=10)
    
    if not cards:
        return '<div class="text-xs text-gray-500 text-center py-4">Gathering data... (needs 5+ matches)</div>'
    
    max_wr = max(c['win_rate'] for c in cards) if cards else 100
    html = ""
    for c in cards:
        wr = c['win_rate']
        bar_width = wr / max_wr * 100
        if wr >= 60:
            bar_color = '#22c55e'
        elif wr >= 50:
            bar_color = '#6366f1'
        else:
            bar_color = '#ef4444'
        
        encoded_name = urllib.parse.quote(c['card_name'])
        safe_name = html_mod.escape(c['card_name'])
        html += f"""
        <div class="mb-2">
            <div class="flex justify-between items-center mb-0.5">
                <a href="https://scryfall.com/search?q=%21%22{encoded_name}%22" target="_blank" 
                   class="text-xs text-gray-300 hover:text-blue-400 truncate max-w-[140px]">{safe_name}</a>
                <span class="text-xs font-mono font-bold" style="color:{bar_color}">{wr}%</span>
            </div>
            <div class="w-full bg-gray-700/40 rounded-full h-1.5 overflow-hidden">
                <div class="h-full rounded-full" style="width:{bar_width}%;background:{bar_color}"></div>
            </div>
        </div>
        """
    
    return html
@router.get("/api/meta", response_class=HTMLResponse)
async def get_meta(request: Request):
    """Meta analysis — color, archetype, and synergy breakdown."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT colors, COUNT(*) as count, 
                   SUM(wins) as total_wins, SUM(losses) as total_losses,
                   AVG(elo) as avg_elo
            FROM decks 
            WHERE active=1 AND colors != ''
            GROUP BY colors
            ORDER BY avg_elo DESC
        ''')
        color_stats = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute('SELECT COUNT(*) as c FROM matches')
        total_matches = cursor.fetchone()['c']
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE winner_id IS NOT NULL')
        decisive = cursor.fetchone()['c']
        cursor.execute('SELECT COUNT(*) as c FROM decks WHERE active=1')
        active_decks = cursor.fetchone()['c']
        cursor.execute('SELECT COUNT(*) as c FROM decks WHERE active=0')
        retired = cursor.fetchone()['c']
        cursor.execute('SELECT MAX(season_id) as s FROM matches')
        row = cursor.fetchone()
        current_season = row['s'] if row['s'] else 0
        # Boss deck stats
        cursor.execute("SELECT COUNT(*) as c FROM decks WHERE name LIKE 'BOSS:%'")
        boss_count = cursor.fetchone()['c']
    
    # Load pool metadata
    meta_path = os.path.join(os.path.dirname(BASE_DIR), 'data', 'pool_metadata.json')
    pool_meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            pool_meta = json.load(f)
    
    pool_updated = pool_meta.get('last_updated', 'Never')[:10] if pool_meta else 'Never'
    pool_count = pool_meta.get('total_cards', '?')
    pool_format = pool_meta.get('format', 'modern').title()
    
    color_map = {
        'W': '<i class="ms ms-w ms-cost ms-shadow text-base"></i>',
        'U': '<i class="ms ms-u ms-cost ms-shadow text-base"></i>',
        'B': '<i class="ms ms-b ms-cost ms-shadow text-base"></i>',
        'R': '<i class="ms ms-r ms-cost ms-shadow text-base"></i>',
        'G': '<i class="ms ms-g ms-cost ms-shadow text-base"></i>'
    }
    decisive_pct = f"{decisive/total_matches*100:.0f}" if total_matches > 0 else "0"
    
    html = f"""
    <div class="grid grid-cols-2 md:grid-cols-6 gap-3 mb-6">
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700 group cursor-pointer" onclick="window.location.href='/decks?active=1'">
            <div class="text-2xl font-bold text-blue-400 group-hover:text-blue-300 transition-colors">{active_decks}</div>
            <div class="text-xs text-gray-400 mt-1">Active Decks</div>
        </div>
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
            <div class="text-2xl font-bold text-green-400">{total_matches}</div>
            <div class="text-xs text-gray-400 mt-1">Bo3 Matches</div>
        </div>
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
            <div class="text-2xl font-bold text-yellow-400">{decisive_pct}%</div>
            <div class="text-xs text-gray-400 mt-1">Decisive Rate</div>
        </div>
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700 group cursor-pointer" onclick="window.location.href='/decks?boss=1'">
            <div class="text-2xl font-bold text-red-400 group-hover:text-red-300 transition-colors">{boss_count}</div>
            <div class="text-xs text-gray-400 mt-1">Boss Decks</div>
        </div>
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700 group cursor-pointer" onclick="window.location.href='/season/{current_season}'">
            <div class="text-2xl font-bold text-purple-400 group-hover:text-purple-300 transition-colors">{current_season}</div>
            <div class="text-xs text-gray-400 mt-1">Season</div>
        </div>
        <div class="bg-gray-800 rounded-xl p-4 text-center border border-gray-700">
            <div class="text-xl font-bold text-cyan-400">{pool_count}</div>
            <div class="text-xs text-gray-400 mt-1">{pool_format} Cards</div>
            <div class="text-xs text-gray-600 mt-0.5">Updated: {pool_updated}</div>
        </div>
    </div>
    
    <h3 class="text-lg font-bold text-white mb-3">🎨 Color Performance</h3>
    <table class="w-full text-sm">
        <thead><tr class="text-gray-400 text-xs uppercase border-b border-gray-700">
            <th class="p-2 text-left">Colors</th>
            <th class="p-2 text-left">Decks</th>
            <th class="p-2 text-left">Avg Elo</th>
            <th class="p-2 text-left">Record</th>
            <th class="p-2 text-left">Win%</th>
        </tr></thead>
        <tbody>
    """
    
    # Color mapping for bars
    color_bar_map = {
        'W': '#F9D423', 'U': '#2563eb', 'B': '#7c3aed', 
        'R': '#ef4444', 'G': '#22c55e', 'C': '#9ca3af'
    }
    
    # Calculate totals for percentage bars
    total_decks = sum(cs['count'] for cs in color_stats)
    max_count = max((cs['count'] for cs in color_stats), default=1)
    
    # --- Color Distribution Chart ---
    html += """
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div class="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
            <h3 class="text-lg font-bold text-white mb-4">🎨 Color Distribution</h3>
    """
    
    for cs in color_stats:
        if not cs['colors']:
            badges = '<i class="ms ms-c ms-cost ms-shadow text-sm"></i>'
            c_disp = "Colorless"
            bar_color = color_bar_map['C']
        else:
            badges = ''.join(color_map.get(c, '') for c in cs['colors'])
            c_disp = cs['colors']
            # Use the first color for the bar gradient
            bar_color = color_bar_map.get(cs['colors'][0], '#6366f1')
        
        pct = cs['count'] / total_decks * 100 if total_decks > 0 else 0
        bar_width = cs['count'] / max_count * 100
        
        html += f"""
            <div class="flex items-center gap-3 mb-2.5 group">
                <div class="w-16 flex-shrink-0 text-right">{badges}</div>
                <div class="flex-1 relative">
                    <div class="w-full bg-gray-700/40 rounded-full h-6 overflow-hidden">
                        <div class="h-full rounded-full transition-all duration-500 flex items-center px-2"
                             style="width:{bar_width}%;background:linear-gradient(90deg,{bar_color}cc,{bar_color}66)">
                            <span class="text-xs font-bold text-white drop-shadow-md">{cs['count']}</span>
                        </div>
                    </div>
                </div>
                <div class="w-12 text-right text-xs text-gray-400 font-mono">{pct:.0f}%</div>
            </div>
        """
    
    html += """
        </div>
    """
    
    # --- Win Rate Rankings ---
    html += """
        <div class="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
            <h3 class="text-lg font-bold text-white mb-4">🏆 Win Rate by Color</h3>
    """
    
    # Sort by win rate for this section
    wr_sorted = sorted(color_stats, key=lambda x: x['total_wins'] / max(x['total_wins'] + x['total_losses'], 1), reverse=True)
    
    for cs in wr_sorted:
        total = cs['total_wins'] + cs['total_losses']
        if total == 0:
            continue
        wr_pct = cs['total_wins'] / total * 100
        
        if not cs['colors']:
            badges = '<i class="ms ms-c ms-cost ms-shadow text-sm"></i>'
            bar_color = color_bar_map['C']
        else:
            badges = ''.join(color_map.get(c, '') for c in cs['colors'])
            bar_color = color_bar_map.get(cs['colors'][0], '#6366f1')
        
        # Color the bar green (>55%), yellow (45-55%), red (<45%)
        if wr_pct >= 55:
            wr_color = '#22c55e'
        elif wr_pct >= 45:
            wr_color = '#eab308'
        else:
            wr_color = '#ef4444'
        
        html += f"""
            <div class="flex items-center gap-3 mb-2.5">
                <div class="w-16 flex-shrink-0 text-right">{badges}</div>
                <div class="flex-1 relative">
                    <div class="w-full bg-gray-700/40 rounded-full h-6 overflow-hidden">
                        <div class="h-full rounded-full transition-all duration-500 flex items-center justify-end px-2"
                             style="width:{wr_pct}%;background:linear-gradient(90deg,{wr_color}cc,{wr_color}66)">
                            <span class="text-xs font-bold text-white drop-shadow-md">{wr_pct:.1f}%</span>
                        </div>
                    </div>
                </div>
                <div class="w-20 text-right text-xs text-gray-500 font-mono">{cs['total_wins']}W-{cs['total_losses']}L</div>
            </div>
        """
    
    html += """
        </div>
    </div>
    """
    
    # --- Detailed Table ---
    html += """
    <h3 class="text-lg font-bold text-white mb-3">📊 Detailed Color Performance</h3>
    <table class="w-full text-sm">
        <thead><tr class="text-gray-400 text-xs uppercase border-b border-gray-700">
            <th class="p-2 text-left">Colors</th>
            <th class="p-2 text-left">Decks</th>
            <th class="p-2 text-left">Avg Elo</th>
            <th class="p-2 text-left">Record</th>
            <th class="p-2 text-left">Win%</th>
        </tr></thead>
        <tbody>
    """
    
    for cs in color_stats:
        if not cs['colors']:
            badges = '<i class="ms ms-c ms-cost ms-shadow text-base"></i>'
            c_disp = "Colorless"
        else:
            badges = ''.join(color_map.get(c, '') for c in cs['colors'])
            c_disp = cs['colors']
            
        total = cs['total_wins'] + cs['total_losses']
        wr = f"{cs['total_wins']/total*100:.0f}%" if total > 0 else "—"
        
        html += f"""
        <tr class="border-b border-gray-700/50 hover:bg-gray-700/30 cursor-pointer transition-colors" onclick="window.location.href='/decks?colors={cs['colors']}&active=1'">
            <td class="p-2">{badges} <span class="text-gray-400 text-xs ml-1">{c_disp}</span></td>
            <td class="p-2 text-gray-300">{cs['count']}</td>
            <td class="p-2 font-mono text-white">{cs['avg_elo']:.0f}</td>
            <td class="p-2 text-gray-400">{cs['total_wins']}W-{cs['total_losses']}L</td>
            <td class="p-2 font-mono text-gray-200">{wr}</td>
        </tr>
        """
    
    html += "</tbody></table>"
    
    # --- Matchup Matrix Heatmap ---
    # Get color vs color win rates from match data
    with get_db_connection() as conn2:
        cursor2 = conn2.cursor()
        cursor2.execute('''
            SELECT d1.colors as row_colors, d2.colors as col_colors,
                   COUNT(*) as total,
                   SUM(CASE WHEN m.winner_id = m.deck1_id THEN 1 ELSE 0 END) as row_wins
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            WHERE d1.colors != '' AND d2.colors != ''
                  AND m.winner_id IS NOT NULL
            GROUP BY d1.colors, d2.colors
            HAVING COUNT(*) >= 10
        ''')
        matrix_data = [dict(row) for row in cursor2.fetchall()]
    
    if matrix_data:
        # Build the matrix
        all_colors = set()
        for md in matrix_data:
            all_colors.add(md['row_colors'])
            all_colors.add(md['col_colors'])
        
        # Sort by single colors first, then multi-color
        sorted_colors = sorted(all_colors, key=lambda c: (len(c), c))
        # Limit to top 10 for readability
        if len(sorted_colors) > 10:
            # Keep the ones with most match data
            color_totals = {}
            for md in matrix_data:
                color_totals[md['row_colors']] = color_totals.get(md['row_colors'], 0) + md['total']
                color_totals[md['col_colors']] = color_totals.get(md['col_colors'], 0) + md['total']
            sorted_colors = sorted(all_colors, key=lambda c: color_totals.get(c, 0), reverse=True)[:10]
        
        # Build lookup
        matrix = {}
        for md in matrix_data:
            if md['row_colors'] in sorted_colors and md['col_colors'] in sorted_colors:
                matrix[(md['row_colors'], md['col_colors'])] = md
        
        def color_badges_small(colors_str):
            if not colors_str:
                return '<i class="ms ms-c ms-cost text-xs"></i>'
            return ''.join(f'<i class="ms ms-{c.lower()} ms-cost text-xs"></i>' for c in colors_str)
        
        html += """
        <h3 class="text-lg font-bold text-white mb-3 mt-8">🎯 Matchup Matrix</h3>
        <p class="text-xs text-gray-400 mb-3">Win rate of row color vs column color (10+ matches required)</p>
        <div class="overflow-x-auto">
            <table class="text-xs">
                <thead><tr>
                    <th class="p-1.5"></th>
        """
        
        for c in sorted_colors:
            html += f'<th class="p-1.5 text-center min-w-[50px]">{color_badges_small(c)}</th>'
        
        html += "</tr></thead><tbody>"
        
        for rc in sorted_colors:
            html += f'<tr><td class="p-1.5 font-medium text-right pr-2">{color_badges_small(rc)}</td>'
            
            for cc in sorted_colors:
                entry = matrix.get((rc, cc))
                if entry and entry['total'] > 0:
                    wr = entry['row_wins'] / entry['total'] * 100
                    # Color: green >55%, yellow 45-55%, red <45%
                    if wr >= 55:
                        bg = f'rgba(34,197,94,{min(0.15 + (wr - 55) * 0.015, 0.6)})'
                        text_cls = 'text-green-300'
                    elif wr >= 45:
                        bg = 'rgba(234,179,8,0.15)'
                        text_cls = 'text-yellow-300'
                    else:
                        bg = f'rgba(239,68,68,{min(0.15 + (45 - wr) * 0.015, 0.6)})'
                        text_cls = 'text-red-300'
                    
                    html += f'<td class="p-1.5 text-center font-mono {text_cls}" style="background:{bg};border:1px solid rgba(55,65,81,0.3);border-radius:4px">{wr:.0f}%</td>'
                elif rc == cc:
                    html += '<td class="p-1.5 text-center text-gray-600" style="background:rgba(55,65,81,0.2);border:1px solid rgba(55,65,81,0.3);border-radius:4px">—</td>'
                else:
                    html += '<td class="p-1.5 text-center text-gray-600" style="border:1px solid rgba(55,65,81,0.15);border-radius:4px"></td>'
            
            html += '</tr>'
        
        html += "</tbody></table></div>"
    
    # --- Win Rate by Archetype ---
    with get_db_connection() as conn3:
        cursor3 = conn3.cursor()
        cursor3.execute('''
            SELECT archetype, COUNT(*) as count,
                   SUM(wins) as total_wins, SUM(losses) as total_losses,
                   ROUND(AVG(elo)) as avg_elo
            FROM decks
            WHERE active=1 AND archetype IS NOT NULL AND archetype != '' AND archetype != 'Unknown'
            GROUP BY archetype
            ORDER BY AVG(elo) DESC
        ''')
        archetype_stats = [dict(row) for row in cursor3.fetchall()]
    
    if archetype_stats:
        archetype_icons = {
            'Aggro': '⚔️', 'Control': '🛡️', 'Combo': '✨',
            'Midrange': '⚖️', 'Tempo': '💨', 'Ramp': '🌳'
        }
        archetype_colors = {
            'Aggro': '#ef4444', 'Control': '#3b82f6', 'Combo': '#a855f7',
            'Midrange': '#eab308', 'Tempo': '#06b6d4', 'Ramp': '#22c55e'
        }
        
        html += """
        <h3 class="text-lg font-bold text-white mb-3 mt-8">🏹 Win Rate by Archetype</h3>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        """
        
        # Bar chart side
        html += '<div class="bg-gray-800/50 rounded-xl p-5 border border-gray-700">'
        
        max_arch_count = max(a['count'] for a in archetype_stats) if archetype_stats else 1
        for arch in archetype_stats:
            total = arch['total_wins'] + arch['total_losses']
            wr_pct = arch['total_wins'] / total * 100 if total > 0 else 0
            bar_color = archetype_colors.get(arch['archetype'], '#6366f1')
            icon = archetype_icons.get(arch['archetype'], '📦')
            bar_width = arch['count'] / max_arch_count * 100
            
            if wr_pct >= 55:
                wr_color = '#22c55e'
            elif wr_pct >= 45:
                wr_color = '#eab308'
            else:
                wr_color = '#ef4444'
            
            html += f"""
                <div class="flex items-center gap-3 mb-3">
                    <div class="w-28 flex-shrink-0 text-right">
                        <span class="text-sm">{icon}</span>
                        <span class="text-sm font-semibold text-gray-200">{arch['archetype']}</span>
                    </div>
                    <div class="flex-1 relative">
                        <div class="w-full bg-gray-700/40 rounded-full h-7 overflow-hidden">
                            <div class="h-full rounded-full transition-all duration-500 flex items-center justify-end px-2"
                                 style="width:{wr_pct}%;background:linear-gradient(90deg,{bar_color}cc,{bar_color}66)">
                                <span class="text-xs font-bold text-white drop-shadow-md">{wr_pct:.1f}%</span>
                            </div>
                        </div>
                    </div>
                    <div class="w-16 text-right text-xs text-gray-400 font-mono">{arch['count']} decks</div>
                </div>
            """
        
        html += '</div>'
        
        # Detailed stats table side
        html += """
        <div class="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
            <table class="w-full text-sm">
                <thead><tr class="text-gray-400 text-xs uppercase border-b border-gray-700">
                    <th class="p-2 text-left">Archetype</th>
                    <th class="p-2 text-left">Decks</th>
                    <th class="p-2 text-left">Avg Elo</th>
                    <th class="p-2 text-left">Record</th>
                    <th class="p-2 text-left">Win%</th>
                </tr></thead>
                <tbody>
        """
        
        for arch in archetype_stats:
            total = arch['total_wins'] + arch['total_losses']
            wr = f"{arch['total_wins']/total*100:.1f}%" if total > 0 else "—"
            icon = archetype_icons.get(arch['archetype'], '📦')
            bar_color = archetype_colors.get(arch['archetype'], '#6366f1')
            
            html += f"""
            <tr class="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">
                <td class="p-2"><span class="mr-1">{icon}</span><span class="text-gray-200 font-medium">{arch['archetype']}</span></td>
                <td class="p-2 text-gray-300">{arch['count']}</td>
                <td class="p-2 font-mono text-white">{arch['avg_elo']:.0f}</td>
                <td class="p-2 text-gray-400 font-mono">{arch['total_wins']}W-{arch['total_losses']}L</td>
                <td class="p-2 font-mono font-bold" style="color:{bar_color}">{wr}</td>
            </tr>
            """
        
        html += "</tbody></table></div></div>"
    
    # --- Archetype Distribution by Season ---
    with get_db_connection() as conn4:
        cursor4 = conn4.cursor()
        cursor4.execute('''
            SELECT m.season_id, d.archetype,
                   COUNT(*) as appearances,
                   SUM(CASE WHEN m.winner_id = m.deck1_id THEN 1 ELSE 0 END) as wins
            FROM matches m
            JOIN decks d ON m.deck1_id = d.id
            WHERE d.archetype IS NOT NULL AND d.archetype != '' AND d.archetype != 'Unknown'
                  AND m.season_id IS NOT NULL
            GROUP BY m.season_id, d.archetype
            ORDER BY m.season_id DESC
        ''')
        season_arch_data = [dict(row) for row in cursor4.fetchall()]
    
    if season_arch_data:
        # Group by season
        seasons = {}
        for row in season_arch_data:
            sid = row['season_id']
            if sid not in seasons:
                seasons[sid] = {}
            seasons[sid][row['archetype']] = {
                'count': row['appearances'],
                'wins': row['wins']
            }
        
        # Get all archetypes across all seasons
        all_archetypes = sorted(set(r['archetype'] for r in season_arch_data))
        archetype_icons = {
            'Aggro': '⚔️', 'Control': '🛡️', 'Combo': '✨',
            'Midrange': '⚖️', 'Tempo': '💨', 'Ramp': '🌳'
        }
        archetype_colors = {
            'Aggro': '#ef4444', 'Control': '#3b82f6', 'Combo': '#a855f7',
            'Midrange': '#eab308', 'Tempo': '#06b6d4', 'Ramp': '#22c55e'
        }
        
        sorted_seasons = sorted(seasons.keys(), reverse=True)
        
        html += """
        <h3 class="text-lg font-bold text-white mb-3 mt-8">📈 Archetype by Season</h3>
        <p class="text-xs text-gray-400 mb-3">Archetype representation and win rates across seasons (scroll to see history)</p>
        <div class="bg-gray-800/50 rounded-xl p-5 border border-gray-700 max-h-[500px] overflow-y-auto">
            <table class="w-full text-sm">
                <thead class="sticky top-0 bg-gray-800 z-10"><tr class="text-gray-400 text-xs uppercase border-b border-gray-700">
                    <th class="p-2 text-left">Season</th>
        """
        
        for arch in all_archetypes:
            icon = archetype_icons.get(arch, '📦')
            html += f'<th class="p-2 text-center">{icon} {arch}</th>'
        
        html += '<th class="p-2 text-center">Total</th>'
        html += "</tr></thead><tbody>"
        
        for sid in sorted_seasons:
            season_data = seasons[sid]
            total_in_season = sum(v['count'] for v in season_data.values())
            
            html += f'<tr class="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors">'
            html += f'<td class="p-2 font-mono text-purple-400 font-bold">S{sid}</td>'
            
            for arch in all_archetypes:
                if arch in season_data:
                    count = season_data[arch]['count']
                    wins = season_data[arch]['wins']
                    pct = count / total_in_season * 100 if total_in_season > 0 else 0
                    wr = wins / count * 100 if count > 0 else 0
                    color = archetype_colors.get(arch, '#6366f1')
                    
                    # Mini percentage bar
                    html += f"""
                    <td class="p-2 text-center">
                        <div class="flex flex-col items-center">
                            <div class="w-full bg-gray-700/40 rounded-full h-3 mb-0.5 overflow-hidden">
                                <div class="h-full rounded-full" style="width:{pct}%;background:{color}88"></div>
                            </div>
                            <span class="text-xs text-gray-300">{count} <span class="text-gray-500">({pct:.0f}%)</span></span>
                            <span class="text-[10px] {'text-green-400' if wr >= 50 else 'text-red-400'}">{wr:.0f}% WR</span>
                        </div>
                    </td>
                    """
                else:
                    html += '<td class="p-2 text-center text-gray-600">—</td>'
            
            html += f'<td class="p-2 text-center font-mono text-gray-400">{total_in_season}</td>'
            html += '</tr>'
        
        html += "</tbody></table></div>"
    
    return html
@router.get("/api/match-history", response_class=HTMLResponse)
async def get_match_history(request: Request):
    """Render the HTML fragment for recent Bo3 match history logs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.id, m.turns, m.timestamp,
                   d1.name AS deck1_name, d1.id AS d1_id,
                   d2.name AS deck2_name, d2.id AS d2_id,
                   w.name AS winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            LEFT JOIN decks w ON m.winner_id = w.id
            ORDER BY m.timestamp DESC
            LIMIT 30
        ''')
        matches = [dict(row) for row in cursor.fetchall()]
    
    html = ""
    for m in matches:
        winner = m['winner_name'] or 'Draw'
        w_cls = "text-yellow-400" if winner == "Draw" else "text-green-400"
        safe_d1 = html_mod.escape(m['deck1_name'])
        safe_d2 = html_mod.escape(m['deck2_name'])
        safe_winner = html_mod.escape(winner)
        
        html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-700/50 transition-colors cursor-pointer" onclick="window.location.href='/match/{m['id']}'">
            <td class="p-3">
                <a href="/deck/{m['d1_id']}" class="text-blue-400 hover:underline" onclick="event.stopPropagation()">{safe_d1}</a>
                <span class="text-gray-500 mx-1">vs</span>
                <a href="/deck/{m['d2_id']}" class="text-blue-400 hover:underline" onclick="event.stopPropagation()">{safe_d2}</a>
            </td>
            <td class="p-3 font-medium {w_cls}">{safe_winner}</td>
            <td class="p-3 text-sm text-gray-500">T{m['turns']}</td>
            <td class="p-3 text-center">
                <a href="/match/{m['id']}" class="inline-flex items-center gap-1 px-2.5 py-1 bg-indigo-900/40 hover:bg-indigo-900/60 border border-indigo-700/50 rounded-lg text-indigo-300 text-xs font-medium transition-colors" onclick="event.stopPropagation()">
                    📜 View
                </a>
            </td>
        </tr>
        """
    return html
@router.get("/api/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
    """Render the overall league statistics HTML fragment (win rates, Elo averages)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as total FROM decks WHERE active=1')
        total_decks = cursor.fetchone()['total']
        cursor.execute('SELECT COUNT(*) as total FROM matches')
        total_matches = cursor.fetchone()['total']
        cursor.execute("SELECT COUNT(*) as total FROM decks WHERE division='Mythic' AND name NOT LIKE 'BOSS:%'")
        mythic_count = cursor.fetchone()['total']
        cursor.execute('SELECT MAX(season_id) as s FROM matches')
        row = cursor.fetchone()
        current_season = row['s'] if row['s'] else 0
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE winner_id IS NOT NULL')
        decisive = cursor.fetchone()['c']
        cursor.execute('SELECT SUM(play_wins) as p, SUM(draw_wins) as d FROM decks')
        play_draw = cursor.fetchone()
        p_wins = play_draw['p'] or 0
        d_wins = play_draw['d'] or 0
        
        cursor.execute('''
            SELECT 
                SUM(CASE WHEN p1_mulligans = 0 AND winner_id = deck1_id THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans = 0 AND winner_id = deck2_id THEN 1 ELSE 0 END) as w7,
                SUM(CASE WHEN p1_mulligans = 0 THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans = 0 THEN 1 ELSE 0 END) as t7,
                
                SUM(CASE WHEN p1_mulligans = 1 AND winner_id = deck1_id THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans = 1 AND winner_id = deck2_id THEN 1 ELSE 0 END) as w6,
                SUM(CASE WHEN p1_mulligans = 1 THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans = 1 THEN 1 ELSE 0 END) as t6,
                
                SUM(CASE WHEN p1_mulligans >= 2 AND winner_id = deck1_id THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans >= 2 AND winner_id = deck2_id THEN 1 ELSE 0 END) as w5,
                SUM(CASE WHEN p1_mulligans >= 2 THEN 1 ELSE 0 END) + 
                SUM(CASE WHEN p2_mulligans >= 2 THEN 1 ELSE 0 END) as t5
            FROM matches
            WHERE winner_id IS NOT NULL
        ''')
        mulls = cursor.fetchone()
    
    decisive_pct = f"{decisive/total_matches*100:.0f}" if total_matches > 0 else "0"
    total_pd = p_wins + d_wins
    play_winrate = f"{p_wins/total_pd*100:.1f}%" if total_pd > 0 else "N/A"
    
    wr7 = f"{(mulls['w7'] or 0) / max(mulls['t7'] or 1, 1) * 100:.1f}"
    wr6 = f"{(mulls['w6'] or 0) / max(mulls['t6'] or 1, 1) * 100:.1f}"
    wr5 = f"{(mulls['w5'] or 0) / max(mulls['t5'] or 1, 1) * 100:.1f}"
    
    html = f"""
    <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div class="bg-gray-800/80 rounded-xl border border-gray-700 p-4 text-center group cursor-pointer" onclick="window.location.href='/decks?active=1'">
            <div class="text-3xl font-bold text-blue-400 group-hover:text-blue-300 transition-colors">{total_decks}</div>
            <div class="text-sm text-gray-400 mt-1">Active Decks</div>
        </div>
        <div class="bg-gray-800/80 rounded-xl border border-gray-700 p-4 text-center">
            <div class="text-3xl font-bold text-green-400">{total_matches}</div>
            <div class="text-sm text-gray-400 mt-1">Matches Played</div>
        </div>
        <div class="bg-gray-800/80 rounded-xl border border-gray-700 p-4 text-center group cursor-pointer" onclick="window.location.href='/decks?division=Mythic'">
            <div class="text-3xl font-bold text-purple-400 group-hover:text-purple-300 transition-colors">{mythic_count}</div>
            <div class="text-sm text-gray-400 mt-1">Mythic Qualifiers</div>
        </div>
        <div class="bg-gray-800/80 rounded-xl border border-gray-700 p-4 text-center group cursor-pointer" onclick="window.location.href='/season/{current_season}'">
            <div class="text-3xl font-bold text-orange-400 group-hover:text-orange-300 transition-colors">{current_season}</div>
            <div class="text-sm text-gray-400 mt-1">Season</div>
        </div>
        <div class="bg-gray-800/80 rounded-xl border border-gray-700 p-4 text-center">
            <div class="text-3xl font-bold text-teal-400">{play_winrate}</div>
            <div class="text-sm text-gray-400 mt-1">Play Win Rate</div>
        </div>
    </div>
    
    <div class="mt-6 bg-gray-800/80 rounded-xl border border-gray-700 p-4">
        <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-widest mb-4">Mulligan Win Rates (Game 1)</h3>
        <div class="grid grid-cols-3 gap-4 text-center">
            <div>
                <div class="text-2xl font-bold text-blue-400">{wr7}%</div>
                <div class="text-xs text-gray-500">Keep 7</div>
                <div class="text-[10px] text-gray-600">({mulls['w7'] or 0}/{mulls['t7'] or 0})</div>
            </div>
            <div>
                <div class="text-2xl font-bold text-yellow-400">{wr6}%</div>
                <div class="text-xs text-gray-500">Mull to 6</div>
                <div class="text-[10px] text-gray-600">({mulls['w6'] or 0}/{mulls['t6'] or 0})</div>
            </div>
            <div>
                <div class="text-2xl font-bold text-red-500">{wr5}%</div>
                <div class="text-xs text-gray-500">Mull to 5-</div>
                <div class="text-[10px] text-gray-600">({mulls['w5'] or 0}/{mulls['t5'] or 0})</div>
            </div>
        </div>
    </div>
    """
    return html
@router.get("/api/matchups", response_class=HTMLResponse)
async def get_matchups(request: Request):
    """Color vs color matchup win-rate matrix from match history."""
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
            HAVING COUNT(*) >= 10
            ORDER BY COUNT(*) DESC
        ''')
        rows = [dict(r) for r in cursor.fetchall()]
    
    # Build matrix
    all_colors = sorted(set(r['c1'] for r in rows) | set(r['c2'] for r in rows))
    # Limit to top 10 most common
    color_games = {}
    for r in rows:
        color_games[r['c1']] = color_games.get(r['c1'], 0) + r['games']
        color_games[r['c2']] = color_games.get(r['c2'], 0) + r['games']
    top_colors = sorted(color_games.keys(), key=lambda c: color_games[c], reverse=True)[:32]
    
    matrix = {}
    for r in rows:
        if r['c1'] in top_colors and r['c2'] in top_colors:
            key = (r['c1'], r['c2'])
            if key not in matrix:
                matrix[key] = {'wins': 0, 'games': 0}
            matrix[key]['wins'] += r['c1_wins']
            matrix[key]['games'] += r['games']
    
    color_map = {
        'W': '<i class="ms ms-w ms-cost ms-shadow text-base"></i>',
        'U': '<i class="ms ms-u ms-cost ms-shadow text-base"></i>',
        'B': '<i class="ms ms-b ms-cost ms-shadow text-base"></i>',
        'R': '<i class="ms ms-r ms-cost ms-shadow text-base"></i>',
        'G': '<i class="ms ms-g ms-cost ms-shadow text-base"></i>',
        'C': '<i class="ms ms-c ms-cost ms-shadow text-base"></i>'
    }
    
    def color_label(c):
        icons = ''.join(color_map.get(ch, '') for ch in c)
        return f"{icons}<br><span class='text-xs'>{c}</span>"
    
    html = '<h3 class="text-lg font-bold text-white mb-3">🎯 Color Matchup Matrix</h3>'
    html += '<p class="text-sm text-gray-400 mb-4">Win rate of row color vs column (min 10 games)</p>'
    html += '<div class="overflow-x-auto"><table class="text-xs">'
    
    # Header row
    html += '<thead><tr><th class="p-2 text-gray-400 border-b border-gray-700"></th>'
    for c in top_colors:
        html += f'<th class="p-2 text-center text-gray-300 border-b border-gray-700 min-w-[60px]">{color_label(c)}</th>'
    html += '</tr></thead><tbody>'
    
    # Data rows
    for c1 in top_colors:
        html += f'<tr><td class="p-2 font-medium text-gray-200 border-r border-gray-700">{color_label(c1)}</td>'
        for c2 in top_colors:
            if c1 == c2:
                html += '<td class="p-2 text-center bg-gray-800 text-gray-600">—</td>'
            else:
                data = matrix.get((c1, c2))
                if data and data['games'] > 0:
                    wr = data['wins'] / data['games'] * 100
                    if wr >= 55:
                        bg = 'bg-green-900/40 text-green-400'
                    elif wr >= 45:
                        bg = 'bg-gray-800/40 text-gray-300'
                    else:
                        bg = 'bg-red-900/40 text-red-400'
                    html += f'<td class="p-2 text-center font-mono {bg}">{wr:.0f}%</td>'
                else:
                    html += '<td class="p-2 text-center text-gray-700">—</td>'
        html += '</tr>'
    
    html += '</tbody></table></div>'
    return html
@router.get("/matches", response_class=HTMLResponse)
async def view_matches(request: Request):
    """Render the dedicated Match History page for the League."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Fetch detailed match info including deck names and ELO
        cursor.execute('''
            SELECT m.id, m.season_id, m.deck1_id, m.deck2_id, m.winner_id, m.turns, m.log_path,
                   d1.name as d1_name, d1.elo as d1_elo,
                   d2.name as d2_name, d2.elo as d2_elo,
                   w.name as winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            JOIN decks w ON m.winner_id = w.id
            ORDER BY m.id DESC
            LIMIT 50
        ''')
        matches = [dict(row) for row in cursor.fetchall()]
        
    return _get_templates().TemplateResponse("matches.html", {
        "request": request,
        "matches": matches
    })
@router.get("/match/{match_id}/replay", response_class=HTMLResponse)
async def view_replay(request: Request, match_id: int):
    """Render the visual 2D interactive replay for a specific match using parsed event logs."""
    from web.match_parser import parse_match_log
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.log_path, d1.card_list as d1_cards, d2.card_list as d2_cards
            FROM matches m
            LEFT JOIN decks d1 ON m.deck1_id = d1.id
            LEFT JOIN decks d2 ON m.deck2_id = d2.id
            WHERE m.id = %s
        ''', (match_id,))
        row = cursor.fetchone()
        
        if not row or not row['log_path']:
            return HTMLResponse("Log not found for this match", status_code=404)
            
        log_path = row['log_path']
        
        if log_path:
            allowed_logs_dir = os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), 'logs'))
            abs_log_path = os.path.abspath(log_path)
            if not abs_log_path.startswith(allowed_logs_dir):
                return HTMLResponse("Invalid log path", status_code=403)
        
        if not os.path.exists(log_path):
            return HTMLResponse(f"Log file missing: {log_path}", status_code=404)
            
        with open(log_path, 'r') as f:
            content = f.read()
            
        data = parse_match_log(content)
        
        from engine.salt_score import calculate_salt_score
        import json
        
        d1_bracket = 1
        d2_bracket = 1
        try:
            d1_bracket = calculate_salt_score(json.loads(row['d1_cards'] or '{}')).get('bracket', 1)
            d2_bracket = calculate_salt_score(json.loads(row['d2_cards'] or '{}')).get('bracket', 1)
        except Exception as e:
            pass

    return _get_templates().TemplateResponse("replay.html", {
        "request": request,
        "match_data": data,
        "d1_bracket": d1_bracket,
        "d2_bracket": d2_bracket
    })
from engine.engine_config import config as engine_config

@router.get("/api/engine/config")
async def get_engine_config():
    """Return current engine configuration."""
    return engine_config.to_dict()

@router.post("/api/engine/config")
async def update_engine_config(request: Request):
    """Update engine configuration (threading, memory, headless mode)."""
    data = await request.json()
    engine_config.update_from_dict(data)
    return {"status": "ok", "config": engine_config.to_dict()}
@router.get("/api/match/{match_id}")
async def get_match_api(match_id: int):
    """JSON API for match replay viewer — returns structured game log data."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.*, 
                   d1.name AS deck1_name, d1.id AS d1_id,
                   d2.name AS deck2_name, d2.id AS d2_id,
                   w.name AS winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            LEFT JOIN decks w ON m.winner_id = w.id
            WHERE m.id = %s
        ''', (match_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "Match not found"}, status_code=404)
        match = dict(row)
    
    # Parse game log
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
    
    # Structure log into turns for the replay viewer
    turns = []
    current_turn = {"turn": 0, "events": []}
    for line in game_log:
        if line.startswith("T") and ":" in line[:5]:
            # Extract turn number
            try:
                turn_num = int(line.split(":")[0].replace("T", ""))
                if turn_num != current_turn["turn"]:
                    if current_turn["events"]:
                        turns.append(current_turn)
                    current_turn = {"turn": turn_num, "events": []}
            except ValueError:
                pass
        current_turn["events"].append(line)
    if current_turn["events"]:
        turns.append(current_turn)
    
    return {
        "match_id": match_id,
        "deck1": {"id": match.get("d1_id"), "name": match.get("deck1_name")},
        "deck2": {"id": match.get("d2_id"), "name": match.get("deck2_name")},
        "winner": match.get("winner_name"),
        "total_turns": len(turns),
        "turns": turns,
        "raw_log": game_log
    }
