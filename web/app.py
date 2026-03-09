"""Web Dashboard — FastAPI application serving the league UI and REST API.

Pages:
    GET  /                      — Main dashboard with live stats
    GET  /deck/{id}             — Deck detail with matchup spread
    GET  /deck/{id}/lineage     — Evolutionary lineage tree (Mermaid)
    GET  /match/{id}/replay     — Interactive 2D match replay
    GET  /admin                 — Admin portal (engine config, health)

Core API:
    GET  /api/leaderboard       — Ranked decks by ELO and division
    GET  /api/top-cards         — Cards with highest win rates
    GET  /api/meta              — Color/archetype breakdown
    GET  /api/meta-trends       — Historical popularity + win rates by season
    GET  /api/matchup-matrix    — Color vs color win-rate matrix
    GET  /api/export/{deck_id}  — Deck export (Arena/MTGO) with sideboard
    GET  /api/card-coverage     — Card pool play rates across active decks

Analysis API:
    POST /api/test-deck         — Test decklist against top league opponents
    POST /api/mulligan-eval     — Evaluate opening hand with Mulligan AI
    POST /api/salt-score        — Commander salt score and bracket
    POST /api/gauntlet/run      — Test vs historical era Top 8

Streaming:
    WS   /ws/elo                — Real-time ELO update stream
"""

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import os
import json
import re
import html as html_mod  # For escaping DB values in HTML responses
from data.db import get_db_connection, get_top_cards
from starlette.responses import JSONResponse

app = FastAPI(title="MTG Genetic League", description="AI-powered deck evolution dashboard")

# CORS — allow dashboard to be accessed from any origin (restrict for production)
from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://localhost:3000"],  # Next.js dev server
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── WebSocket: Real-Time ELO Streaming ──────────────────────────────────────

_ws_clients: list[WebSocket] = []


@app.websocket("/ws/elo-stream")
async def elo_stream(websocket: WebSocket):
    """Stream live ELO updates as matches complete.

    Clients connect and receive JSON messages:
    {
        "type": "elo_update",
        "deck_id": 42,
        "deck_name": "Burn-R-Gen3",
        "old_elo": 1234.5,
        "new_elo": 1251.2,
        "delta": 16.7,
        "match_id": 789,
        "timestamp": "2026-02-22T14:30:00"
    }
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            # Keep alive — client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.remove(websocket)
    except Exception:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)


async def broadcast_elo_update(data: dict):
    """Push ELO update to all connected WebSocket clients.

    Called by the league manager after each match ELO adjustment.
    """
    disconnected = []
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        _ws_clients.remove(ws)

# Simple in-memory rate limiter for CPU-intensive endpoints
import time as _time
from collections import defaultdict
_rate_limit_store: dict = defaultdict(list)  # IP -> list of timestamps
_RATE_LIMIT_MAX = 3     # Max requests
_RATE_LIMIT_WINDOW = 60  # Per 60 seconds

def _check_rate_limit(client_ip: str) -> bool:
    """Returns True if the request should be allowed, False if rate-limited."""
    now = _time.time()
    # Clean old entries
    _rate_limit_store[client_ip] = [
        t for t in _rate_limit_store[client_ip] if now - t < _RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit_store[client_ip].append(now)
    return True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Import canonical parse_cmc from optimizer (handles hybrid mana)
from optimizer.genetic import parse_cmc

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the main dashboard overview page."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/admin/meta-map")
async def get_meta_map():
    """Returns 2D projected coordinates of deck fingerprints via PCA.
    
    Builds sparse card-count vectors from each deck's card_list in SQLite,
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
        
        # Build card vocabulary from all decks
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
            except:
                cl = {}
            deck_cards.append(cl)
            all_cards.update(cl.keys())
        
        card_vocab = sorted(all_cards)
        card_idx = {name: i for i, name in enumerate(card_vocab)}
        
        # Build feature matrix (deck x card_count)
        X = np.zeros((len(decks), len(card_vocab)), dtype=np.float32)
        for i, cl in enumerate(deck_cards):
            for name, count in cl.items():
                if name in card_idx:
                    X[i, card_idx[name]] = count
        
        # Normalize rows (L2) so deck size doesn't dominate
        norms = np.linalg.norm(X, axis=1, keepdims=True)
        norms[norms == 0] = 1
        X = X / norms
        
        # PCA to 2D
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
        print(f"Meta-Map Error: {e}")
        return {"points": []}

@app.get("/api/leaderboard", response_class=HTMLResponse)
async def get_leaderboard(request: Request):
    """Render the HTML fragment for the ranked deck leaderboard."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, division, wins, losses, draws, elo, colors, card_list 
            FROM decks 
            WHERE active=1 
            ORDER BY elo DESC 
            LIMIT 50
        ''')
        decks = [dict(row) for row in cursor.fetchall()]
        
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
        except:
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
        cache = _get_card_search_cache() or []
        card_pool_dict = {c['name']: c for c in cache} if isinstance(cache, list) else cache
        arch_info = classify_deck(decklist, card_pool_dict)
        arch_emoji = {"Aggro": "🗡️", "Control": "🛡️", "Combo": "⚡", "Midrange": "⚖️"}.get(arch_info['archetype'], "")
        
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
            <td class="p-3 text-center font-bold text-gray-300">🧂 {bracket}</td>
            <td class="p-3"><span class="px-2 py-1 rounded-full text-xs {div_cls}">{safe_div}</span></td>
            <td class="p-3 font-mono font-bold text-white">{d['elo']:.0f}</td>
            <td class="p-3 text-sm text-gray-300">{d['wins']}W-{d['losses']}L<span class="text-gray-500">-{draws}D</span></td>
            <td class="p-3 font-mono text-sm text-gray-300">{wr}</td>
        </tr>
        """
    return html

@app.get("/api/top-cards", response_class=HTMLResponse)
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

@app.get("/api/top-cards-sidebar", response_class=HTMLResponse)
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

@app.get("/api/meta", response_class=HTMLResponse)
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
    return html

@app.get("/api/match-history", response_class=HTMLResponse)
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
                <a href="/deck/{m['d1_id']}" class="text-blue-400 hover:underline">{safe_d1}</a>
                <span class="text-gray-500 mx-1">vs</span>
                <a href="/deck/{m['d2_id']}" class="text-blue-400 hover:underline">{safe_d2}</a>
            </td>
            <td class="p-3 font-medium {w_cls}">{safe_winner}</td>
            <td class="p-3 text-sm text-gray-500">T{m['turns']}</td>
        </tr>
        """
    return html

@app.get("/api/stats", response_class=HTMLResponse)
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


@app.get("/deck/{deck_id}", response_class=HTMLResponse)
async def view_deck(request: Request, deck_id: int):
    """Render the dedicated page detailing a specific genetic deck's composition and history."""
    # Load card pool for CMC data
    import os as _os
    base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    cp_path = _os.path.join(base, 'data', 'legal_cards.json')
    if not _os.path.exists(cp_path):
        cp_path = _os.path.join(base, 'data', 'processed_cards.json')
    card_pool = {}
    if _os.path.exists(cp_path):
        with open(cp_path) as f:
            for c in json.load(f):
                card_pool[c['name']] = c
    
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
            cmc = parse_cmc(data.get('mana_cost', ''))
            bucket = min(cmc, 6)
            curve[bucket] += count
        deck['curve'] = curve
        
        # Compute archetype
        total_cmc = 0
        spell_count = 0
        creature_count = 0
        for name, count in cards.items():
            data = card_pool.get(name, {})
            if 'Land' not in data.get('type_line', name):
                total_cmc += parse_cmc(data.get('mana_cost', '')) * count
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
        
        # Build card info for Scryfall links + keywords
        card_info = {}
        for name in cards.keys():
            data = card_pool.get(name, {})
            scryfall = data.get('scryfall_uri', '')
            if not scryfall:
                import urllib.parse
                scryfall = f"https://scryfall.com/search?q=%21%22{urllib.parse.quote(name)}%22"
            
            # Parse keywords from oracle text for display
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
            }
        
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
        except Exception:
            pass  # Graceful fallback if archetype column missing
        
    return templates.TemplateResponse("deck.html", {
        "request": request, "deck": deck, "matches": matches, 
        "card_info": card_info, "matchup_spread": matchup_spread
    })

@app.get("/match/{match_id}", response_class=HTMLResponse)
async def view_match(request: Request, match_id: int):
    """Render the detailed view of a specific match log."""
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
            return HTMLResponse("<h1>Match Not Found</h1>", status_code=404)
        match = dict(row)
    
    # Prefer full log from file, fall back to DB summary
    game_log = []
    log_path = match.get('log_path', '')
    # Security: validate log_path is within the expected logs directory
    allowed_logs_dir = os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), 'logs'))
    if log_path:
        abs_log_path = os.path.abspath(log_path)
        if abs_log_path.startswith(allowed_logs_dir) and os.path.exists(abs_log_path):
            with open(abs_log_path, 'r') as f:
                game_log = f.read().splitlines()
    
    if not game_log:
        game_log = json.loads(match.get('game_log', '[]') or '[]')
    
    return templates.TemplateResponse("match.html", {
        "request": request, "match": match, "game_log": game_log
    })


# ─── User Deck Test: Paste a decklist, test vs top league decks ───────────

from fastapi.responses import JSONResponse

# Card search cache (loaded once on first request)
_card_search_cache = None

def _get_card_search_cache():
    global _card_search_cache
    if _card_search_cache is not None:
        return _card_search_cache
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cp_path = os.path.join(base, 'data', 'legal_cards.json')
    if not os.path.exists(cp_path):
        cp_path = os.path.join(base, 'data', 'processed_cards.json')
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

@app.get("/api/cards/search")
async def search_cards(q: str = ""):
    """Card name autocomplete for the deck builder."""
    if not q or len(q) < 2:
        return JSONResponse([])
    query = q.lower()
    cache = _get_card_search_cache()
    # Prefix matches first, then substring matches 
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

def _parse_decklist(raw: str) -> dict:
    """Parse a user decklist from various formats.
    Supports:
        4 Lightning Bolt
        4x Lightning Bolt
        Lightning Bolt x4
        4 Lightning Bolt (M20) 123
    """
    cards = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//') or line.lower().startswith('sideboard'):
            continue
        
        # Try "4 Card Name" or "4x Card Name"
        m = re.match(r'^(\d+)x?\s+(.+?)(?:\s*\([\w\d]+\)\s*\d+)?$', line)
        if m:
            count = int(m.group(1))
            name = m.group(2).strip()
            cards[name] = cards.get(name, 0) + count
            continue
        
        # Try "Card Name x4"
        m = re.match(r'^(.+?)\s+x?(\d+)$', line)
        if m:
            name = m.group(1).strip()
            count = int(m.group(2))
            cards[name] = cards.get(name, 0) + count
            continue
        
        # Just a card name (1 copy)
        if len(line) > 2:
            cards[line] = cards.get(line, 0) + 1
    
    return cards


@app.post("/api/test-deck")
async def test_deck(request: Request):
    """Test a user-submitted decklist against top league decks. Returns win rate + matchup breakdown."""
    # Rate limit: CPU-intensive endpoint, max 3 requests/minute per IP
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse(
            {"error": "Rate limit exceeded. Please wait 60 seconds before testing again."},
            status_code=429
        )
    import traceback
    try:
        try:
            body = await request.json()
            raw_decklist = body.get('decklist', '')
        except (ValueError, TypeError):
            form = await request.form()
            raw_decklist = form.get('decklist', '')
        
        if not raw_decklist or not raw_decklist.strip():
            return JSONResponse({"error": "Empty decklist"}, status_code=400)
        
        cards = _parse_decklist(raw_decklist)
        if not cards:
            return JSONResponse({"error": "Could not parse any cards"}, status_code=400)
        
        total_cards = sum(cards.values())
        
        # Load card pool — use legal_cards.json (has basic lands)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cp_path = os.path.join(base, 'data', 'legal_cards.json')
        if not os.path.exists(cp_path):
            cp_path = os.path.join(base, 'data', 'processed_cards.json')
        
        card_pool = {}
        with open(cp_path) as f:
            for c in json.load(f):
                name = c.get('name', '')
                card_pool[name] = c
                if ' // ' in name:
                    front_face = name.split(' // ')[0].strip()
                    if front_face not in card_pool:
                        card_pool[front_face] = c
        
        # Validate cards
        from engine.card_builder import inject_basic_lands
        inject_basic_lands(card_pool)
        
        valid = {}
        invalid = []
        for name, count in cards.items():
            if name in card_pool:
                valid[name] = count
            else:
                invalid.append(name)
        
        if not valid:
            return JSONResponse({"error": "No valid cards found", "invalid_cards": invalid}, status_code=400)
        
        # Build deck
        from engine.card_builder import dict_to_card
        from engine.deck import Deck
        from engine.game import Game
        from engine.player import Player
        from simulation.runner import SimulationRunner
        from agents.heuristic_agent import HeuristicAgent
        
        def make_deck(card_dict):
            deck = Deck()
            for name, count in card_dict.items():
                try:
                    data = card_pool.get(name)
                    if data:
                        card = dict_to_card(data)
                        deck.add_card(card, count)
                except Exception as e:
                    print(f"Skipping corrupt card '{name}': {e}")
                    traceback.print_exc()
            return deck
        
        user_deck = make_deck(valid)
        
        # Get top 10 league decks to test against
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, name, card_list, elo FROM decks 
                WHERE active=1 ORDER BY elo DESC LIMIT 10
            ''')
            opponents = [dict(row) for row in cursor.fetchall()]
        
        # Run matches in parallel for speed
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def run_matchup(opp):
            """Run a Bo3 matchup against one opponent. Thread-safe (each creates its own game)."""
            try:
                opp_cards = json.loads(opp['card_list'])
                if isinstance(opp_cards, list):
                    c = {}
                    for n in opp_cards: c[n] = c.get(n, 0) + 1
                    opp_cards = c
                opp_deck = make_deck(opp_cards)
            except Exception as e:
                print(f"Skipping opponent '{opp['name']}' due to deck error: {e}")
                return None
            
            w, l, d = 0, 0, 0
            turns_list = []
            for _ in range(3):
                try:
                    p1 = Player("UserDeck", user_deck)
                    p2 = Player(opp['name'], opp_deck)
                    game = Game([p1, p2])
                    runner = SimulationRunner(game, [HeuristicAgent(), HeuristicAgent()])
                    result = runner.run()
                    turns_list.append(result.turns)
                    if result.winner == "UserDeck":
                        w += 1
                    elif result.winner == opp['name']:
                        l += 1
                    else:
                        d += 1
                    if w >= 2 or l >= 2:
                        break
                except Exception as e:
                    print(f"Simulation failed vs {opp['name']}: {e}")
                    d += 1
            
            match_result = "Win" if w > l else "Loss" if l > w else "Draw"
            return {
                "opponent": opp['name'],
                "opponent_elo": round(opp['elo']),
                "games": f"{w}-{l}" + (f"-{d}" if d else ""),
                "result": match_result,
                "avg_turns": round(sum(turns_list) / max(len(turns_list), 1), 1)
            }
        
        results = []
        total_wins, total_losses, total_draws = 0, 0, 0
        
        with ThreadPoolExecutor(max_workers=min(len(opponents), 4)) as executor:
            futures = {executor.submit(run_matchup, opp): opp for opp in opponents}
            for future in as_completed(futures):
                matchup = future.result()
                if matchup is None:
                    continue
                results.append(matchup)
                if matchup["result"] == "Win":
                    total_wins += 1
                elif matchup["result"] == "Loss":
                    total_losses += 1
                else:
                    total_draws += 1
        
        # Sort results by opponent ELO (strongest first)
        results.sort(key=lambda r: r['opponent_elo'], reverse=True)
        
        total_played = total_wins + total_losses + total_draws
        win_rate = round(total_wins / max(total_played, 1) * 100, 1)
        
        return JSONResponse({
            "cards_submitted": total_cards,
            "cards_valid": sum(valid.values()),
            "invalid_cards": invalid,
            "win_rate": win_rate,
            "record": f"{total_wins}W-{total_losses}L-{total_draws}D",
            "matchups": results,
            "grade": "S" if win_rate >= 80 else "A" if win_rate >= 60 else "B" if win_rate >= 40 else "C" if win_rate >= 20 else "D"
        })
    except Exception as e:
        with open("crash.log", "a") as f:
            f.write(f"\nCRASH at {request.url.path}:\n")
            traceback.print_exc(file=f)
        return JSONResponse({"error": f"Internal Server Error: {str(e)}"}, status_code=500)


@app.post("/api/flex-test")
async def flex_test(request: Request):
    """Test flex slots against Gauntlet bosses to find mathematically optimal configurations."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse({"error": "Rate limit exceeded. Please wait 60 seconds."}, status_code=429)
        
    try:
        body = await request.json()
        raw_core = body.get('core_decklist', '')
        raw_flex = body.get('flex_pool', '')
    except Exception:
        return JSONResponse({"error": "Invalid JSON mapping"}, status_code=400)
        
    core_cards = _parse_decklist(raw_core)
    flex_pool = list(_parse_decklist(raw_flex).keys())
    
    if sum(core_cards.values()) > 59:
        return JSONResponse({"error": f"Core deck already {sum(core_cards.values())} cards. Must be < 60 to have flex slots."}, status_code=400)
    if not flex_pool:
        return JSONResponse({"error": "Flex pool cannot be empty."}, status_code=400)
        
    from simulation.flex_tester import FlexTester
    try:
        tester = FlexTester(core_deck=core_cards, flex_pool=flex_pool)
        results = tester.run_tests()
        return JSONResponse({"results": results})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": f"Internal Error: {str(e)}"}, status_code=500)


@app.post("/api/mana-calc")
async def mana_calc(request: Request):
    """Analyze decklist mana base using Frank Karsten's hypergeometric math."""
    try:
        body = await request.json()
        raw_decklist = body.get('decklist', '')
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        
    deck_dict = _parse_decklist(raw_decklist)
    if not deck_dict:
        return JSONResponse({"error": "Empty or invalid decklist."}, status_code=400)
        
    # Load card pool
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'legal_cards.json')
    if not os.path.exists(data_path):
        data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'processed_cards.json')
        
    card_pool = {}
    if os.path.exists(data_path):
        with open(data_path, 'r') as f:
            for c in json.load(f):
                card_pool[c['name']] = c
                
    from engine.card_builder import inject_basic_lands
    inject_basic_lands(card_pool)
    
    from utils.hypergeometric import evaluate_deck_mana
    try:
        results = evaluate_deck_mana(deck_dict, card_pool)
        return JSONResponse({"results": results})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": f"Internal Error: {str(e)}"}, status_code=500)


# ─── Matchup Matrix: archetype vs archetype win rates ─────────────────────

@app.get("/api/matchups", response_class=HTMLResponse)
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


@app.get("/api/matchup-matrix")
async def get_matchup_matrix_json():
    """JSON matchup matrix for the React metagame wheel visualization."""
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

    color_games = {}
    for r in rows:
        color_games[r['c1']] = color_games.get(r['c1'], 0) + r['games']
        color_games[r['c2']] = color_games.get(r['c2'], 0) + r['games']
    top_colors = sorted(color_games.keys(), key=lambda c: color_games[c], reverse=True)[:15]

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


# ─── Deck Export: Arena/MTGO format ─────────────────────────────────────

@app.get("/api/export/{deck_id}")
async def export_deck(deck_id: int, format: str = "arena"):
    """Export a deck in Arena or MTGO format, including sideboard."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, card_list FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "Deck not found"}, status_code=404)
        
        # Fetch sideboard history (most common in/out cards vs all matchups)
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
        lines = [f"// {deck['name']}"]
        lines.append("// Exported from MTG Genetic League")
        lines.append("")
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        if sideboard_cards:
            lines.append("")
            lines.append("Sideboard")
            for name in sorted(sideboard_cards.keys()):
                lines.append(f"1 {name}")
        text = "\n".join(lines)
    else:
        lines = [f"// {deck['name']}"]
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        if sideboard_cards:
            lines.append("")
            lines.append("Sideboard")
            for name in sorted(sideboard_cards.keys()):
                lines.append(f"1 {name}")
        text = "\n".join(lines)
    
    return JSONResponse({"deck_name": deck['name'], "format": format, "decklist": text,
                         "sideboard_size": len(sideboard_cards)})

from pydantic import BaseModel
from typing import List

class MulliganRequest(BaseModel):
    deck_id: int
    hand: List[str]
    mulligan_count: int = 0
    meta_archetype: str = "Midrange"

@app.post("/api/mulligan-eval")
async def evaluate_mulligan(req: MulliganRequest):
    """Evaluate an opening hand using the Mulligan AI."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decks WHERE id = %s", (req.deck_id,))
        row = cursor.fetchone()
        
    if not row:
        return JSONResponse({"error": "Deck not found"}, status_code=404)
        
    from engine.deck import Deck
    from engine.card_builder import dict_to_card
    
    deck = Deck()
    cache = _get_card_search_cache()
    pool = {c['name']: c for c in cache}
    
    decklist = json.loads(row['card_list'])
    if isinstance(decklist, list):
        counts = {}
        for n in decklist: counts[n] = counts.get(n, 0) + 1
        decklist = counts
        
    for name, count in decklist.items():
        if name in pool:
            deck.add_card(dict_to_card(pool[name]), count)
            
    deck.db_id = row['id']
    deck.elo = row['elo']
    
    hand_cards = []
    
    if not req.hand:
        # Draw a random 7-card hand
        import random
        deck_list = deck.get_game_deck()
        random.shuffle(deck_list)
        hand_cards = deck_list[:7]
    else:
        for name in req.hand:
            if name in pool:
                hand_cards.append(dict_to_card(pool[name]))
            
    import os
    model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'mulligan_model.npz')
    if not os.path.exists(model_path):
        model_path = None
        
    from agents.mulligan_ai import MulliganAI
    ai = MulliganAI(model_path=model_path)
    
    expected_turn = ai.evaluate_hand(hand_cards, deck)
    heuristic_turn = ai.heuristic_goldfish_turn(hand_cards)
    
    should_mull, explanation = ai.should_mulligan(hand_cards, deck, req.mulligan_count, req.meta_archetype)
    recommendation = "Mulligan" if should_mull else "Keep"
    
    return {
        "expected_win_turn": round(float(expected_turn), 2),
        "heuristic_win_turn": round(float(heuristic_turn), 2),
        "recommendation": recommendation,
        "explanation": explanation,
        "hand": [c.name for c in hand_cards]
    }



# ─── New Drill-Down Routes ─────────────────────────────────────────────

@app.get("/decks", response_class=HTMLResponse)
async def browse_decks(request: Request, page: int = 1, active: int = None, boss: int = None, colors: str = None, division: str = None):
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
            
        # Count total for pagination
        count_query = query.replace("SELECT *", "SELECT COUNT(*) as c")
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()['c']
        
        # Fetch page
        query += " ORDER BY elo DESC LIMIT %s OFFSET %s"
        params.extend([page_size, offset])
        cursor.execute(query, params)
        decks = [dict(r) for r in cursor.fetchall()]
        
    return templates.TemplateResponse("decks.html", {
        "request": request,
        "decks": decks,
        "count": total_count,
        "page": page,
        "next_page": page + 1 if (page * page_size) < total_count else None,
        "prev_page": page - 1 if page > 1 else None,
        "filters": {"active": active, "boss": boss, "colors": colors, "division": division}
    })

@app.get("/season/{season_id}", response_class=HTMLResponse)
async def view_season(request: Request, season_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Season Stats
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE season_id = %s', (season_id,))
        match_count = cursor.fetchone()['c']
        
        if match_count == 0:
            return HTMLResponse(f"<h1>Season {season_id} not found or has no matches</h1>", status_code=404)
        
        # Winner (Most wins in season?) - Heuristic: Deck with most wins recorded in this season's matches
        # This is expensive, so let's just show top 5 decks by win-count in this season
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
        
        # Promotions/Relegations are not stored in DB, so we pass empty lists for now.
        # Future improvement: Store season results in a 'seasons' table.
        
    return templates.TemplateResponse("season.html", {
        "request": request,
        "season": {
            "id": season_id,
            "match_count": match_count,
            "winner": winner,
            "promotions": [],
            "relegations": []
        }
    })


@app.get("/deck/{deck_id}/lineage", response_class=HTMLResponse)
async def view_lineage(request: Request, deck_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Fetch current deck
        cursor.execute('SELECT * FROM decks WHERE id = %s', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("Deck not found", status_code=404)
        current_deck = dict(row)
        
        # 2. Build graph data (Nodes and Edges)
        nodes = {}  # id -> {name, elo, gen, type}
        edges = set() # (from_id, to_id)
        
        # Helper to fetch deck info
        def get_deck_info(d_id):
            cursor.execute('SELECT id, name, elo, generation, parent_ids FROM decks WHERE id = %s', (d_id,))
            return cursor.fetchone()

        # Add current node
        nodes[deck_id] = {
            'name': current_deck['name'], 
            'elo': current_deck['elo'], 
            'gen': current_deck['generation'],
            'type': 'current'
        }
        
        # 3. Traverse Ancestors (Up to 5 generations back)
        queue = [(deck_id, 0)] # (id, depth)
        visited = {deck_id}
        
        import json
        
        while queue:
            curr_id, depth = queue.pop(0)
            if depth >= 5: continue
            
            # Get parents
            row = get_deck_info(curr_id)
            if not row: continue
            
            parents = json.loads(row['parent_ids']) if row['parent_ids'] else []
            if isinstance(parents, int): parents = [parents] # Handle legacy format if any
            
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
                        # Add to queue
                        if p_id not in visited:
                            visited.add(p_id)
                            queue.append((p_id, depth + 1))
                
                edges.add((p_id, curr_id))

        # 4. Traverse Immediate Children (Down 1 generation)
        # Find decks where parent_ids contains deck_id
        # Converting deck_id to string for LIKE query is tricky due to JSON format "[1, 2]"
        # But commonly parent_ids is roughly "[ID]" or "[ID, ID]"
        # Safe wildcard: %deck_id% covers it (e.g. [123] or [123, 456])
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
            nodes[c_id]['type'] = 'child' # Override if existed as ancestor (loop?)
            edges.add((deck_id, c_id))

        # 5. Generate Mermaid String
        graph_def = []
        for n_id, data in nodes.items():
            # Clean name for ID safe string
            safe_id = f"D{n_id}"
            label = f"{data['name']}<br/>Gen {data['gen']} | {data['elo']:.0f} Elo"
            
            # Apply style class
            style = "class"
            if data['type'] == 'current': style += " current"
            elif data['type'] == 'ancestor': style += " ancestor"
            elif data['type'] == 'child': style += " child"
            
            if "BOSS" in data['name']: style = "class boss"
            
            graph_def.append(f'{safe_id}("{label}")')
            graph_def.append(f'class {safe_id} {data["type"]}')
            
            # Click link
            graph_def.append(f'click {safe_id} "/deck/{n_id}" "View Deck"')

        for src, dst in edges:
            graph_def.append(f"D{src} --> D{dst}")

        mermaid_str = "\n".join(graph_def)
            
    return templates.TemplateResponse("lineage.html", {
        "request": request,
        "deck": current_deck,
        "graph_def": mermaid_str
    })


@app.get("/api/deck/{deck_id}/sideboard-guide")
async def get_sideboard_guide(deck_id: int):
    """Retrieve the aggregated sideboarding heuristics for a specific deck."""
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
        
    # Simplify guide to discrete ins/outs
    summary = {}
    
    # Calculate total swaps per opponent to find the most common matchups
    opp_totals = {}
    for opp, swaps in guide.items():
        opp_totals[opp] = sum(s['count'] for s in swaps)
        
    # Get top 6 opponents by sideboarding frequency
    top_opponents = sorted(opp_totals.keys(), key=lambda o: opp_totals[o], reverse=True)[:6]

    for opp in top_opponents:
        swaps = guide[opp]
        cards_in = {}
        cards_out = {}
        for s in swaps:
            cards_in[s['card_in']] = cards_in.get(s['card_in'], 0) + s['count']
            cards_out[s['card_out']] = cards_out.get(s['card_out'], 0) + s['count']
            
        # Top 5 ins and outs
        top_in = sorted(cards_in.items(), key=lambda x: x[1], reverse=True)[:7]
        top_out = sorted(cards_out.items(), key=lambda x: x[1], reverse=True)[:7]
        
        summary[opp] = {
            "in": [{"name": k} for k, v in top_in],
            "out": [{"name": k} for k, v in top_out]
        }
        
    return JSONResponse({"deck_id": deck_id, "guide": summary})


@app.get("/api/meta-trends", response_class=JSONResponse)
async def api_meta_trends():
    """Retrieve historical archetype popularity AND win rates aggregated by season."""
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
    
    # Popularity series
    archetypes = {}
    for r in pop_rows:
        arch = r['archetype']
        if arch not in archetypes:
            archetypes[arch] = {s: 0 for s in seasons}
        archetypes[arch][r['season_id']] = r['deck_count']
        
    series = []
    for arch, counts_by_season in archetypes.items():
        data = [counts_by_season[s] for s in seasons]
        series.append({"name": arch, "data": data})
    
    # Win rate series
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


@app.get("/matches", response_class=HTMLResponse)
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
        
    return templates.TemplateResponse("matches.html", {
        "request": request,
        "matches": matches
    })

@app.get("/match/{match_id}/replay", response_class=HTMLResponse)
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
            
        import sys
        if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from match_parser import parse_match_log
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

    return templates.TemplateResponse("replay.html", {
        "request": request,
        "match_data": data,
        "d1_bracket": d1_bracket,
        "d2_bracket": d2_bracket
    })


# ─── Feature: Engine Room (Resource Controls) ────────────────────────────────

from engine.engine_config import config as engine_config

@app.get("/api/engine/config")
async def get_engine_config():
    """Return current engine configuration."""
    return engine_config.to_dict()

@app.post("/api/engine/config")
async def update_engine_config(request: Request):
    """Update engine configuration (threading, memory, headless mode)."""
    data = await request.json()
    engine_config.update_from_dict(data)
    return {"status": "ok", "config": engine_config.to_dict()}


# ─── Feature: Historical Gauntlet ("Time Machine") ───────────────────────────

from league.historical_gauntlet import get_era_list, run_gauntlet

@app.get("/api/gauntlet/eras")
async def get_gauntlet_eras():
    """List available historical eras for the Time Machine."""
    return {"eras": get_era_list()}

@app.post("/api/gauntlet/run")
async def run_gauntlet_endpoint(request: Request):
    """Run a user deck against a historical era's Top 8."""
    data = await request.json()
    decklist_raw = data.get("decklist", "")
    era_id = data.get("era", "")

    if not decklist_raw or not era_id:
        return JSONResponse({"error": "Both 'decklist' and 'era' required"},
                            status_code=400)

    # Parse decklist from text format
    if isinstance(decklist_raw, str):
        parsed = _parse_decklist(decklist_raw)
    else:
        parsed = decklist_raw  # Already a dict

    if not parsed:
        return JSONResponse({"error": "Could not parse decklist"}, status_code=400)

    result = run_gauntlet(parsed, era_id)
    return result


# ─── Feature: Mutation Heatmaps ──────────────────────────────────────────────

from data.db import get_mutation_heatmap

@app.get("/api/mutations/heatmap")
async def get_heatmap(limit: int = 50):
    """Get top card swaps ranked by average ELO delta."""
    data = get_mutation_heatmap(limit)
    return {"mutations": data, "total": len(data)}


# ─── Feature: Salt Score (Commander Brackets) ────────────────────────────────

from engine.salt_score import calculate_salt_score

@app.post("/api/salt-score")
async def get_salt_score(request: Request):
    """Calculate Commander salt score and bracket for a decklist."""
    data = await request.json()
    decklist_raw = data.get("decklist", "")

    if isinstance(decklist_raw, str):
        parsed = _parse_decklist(decklist_raw)
    else:
        parsed = decklist_raw

    if not parsed:
        return JSONResponse({"error": "Could not parse decklist"}, status_code=400)

    result = calculate_salt_score(parsed)
    return result


# ─── Feature: Hall of Fame ───────────────────────────────────────────────────

from data.db import get_hall_of_fame

@app.get("/admin", response_class=HTMLResponse)
async def admin_portal(request: Request):
    """Main Admin Portal UI."""
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/api/admin/health")
async def get_admin_health():
    """Live Health Check - Rules Coverage %."""
    from engine.rules_sandbox import SCENARIO_REGISTRY
    tested = len(SCENARIO_REGISTRY)
    total = max(tested, 1)  # Use actual registry size as denominator
    coverage = min((tested / total) * 100, 100.0)  # Cap at 100%
    return {
        "coverage_percent": round(coverage, 1),
        "tested_interactions": tested,
        "total_scenarios": total
    }

@app.post("/api/admin/restart")
async def admin_restart():
    """Restart the Sovereign simulation as a background process."""
    import subprocess
    import sys
    
    project_root = os.path.dirname(BASE_DIR)
    venv_python = os.path.join(project_root, '.venv', 'bin', 'python')
    sovereign_script = os.path.join(project_root, 'sovereign.py')
    
    try:
        subprocess.Popen(
            [venv_python, sovereign_script],
            cwd=project_root,
            stdout=open(os.path.join(project_root, 'data', 'sovereign_stdout.log'), 'w'),
            stderr=subprocess.STDOUT,
        )
        return {"status": "ok", "message": "Sovereign simulation restarted in background."}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/admin/reset-elo")
async def admin_reset_elo():
    """Reset all deck ELO ratings to 1200."""
    try:
        from data.db import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE decks SET elo = 1200")
        conn.commit()
        affected = cursor.rowcount
        conn.close()
        return {"status": "ok", "message": f"Reset {affected} decks to ELO 1200."}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/admin/butterfly", response_class=HTMLResponse)
async def butterfly_dashboard(request: Request):
    """Admin UI for viewing Misplay Hunter butterfly maps."""
    return templates.TemplateResponse("butterfly.html", {"request": request})

@app.get("/api/butterfly-reports")
async def get_butterfly_reports():
    """Retrieve all Misplay Hunter reports."""
    from engine.misplay_hunter import BUTTERFLY_REPORTS_FILE
    
    if not os.path.exists(BUTTERFLY_REPORTS_FILE):
        return []
        
    with open(BUTTERFLY_REPORTS_FILE, "r") as f:
        data = json.load(f)
        
    # Enrich with deck names
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for report in data:
            cursor.execute('SELECT name FROM decks WHERE id = %s', (report['deck1_id'],))
            d1 = cursor.fetchone()
            report['deck1_name'] = d1['name'] if d1 else f"Deck {report['deck1_id']}"
            
            cursor.execute('SELECT name FROM decks WHERE id = %s', (report['deck2_id'],))
            d2 = cursor.fetchone()
            report['deck2_name'] = d2['name'] if d2 else f"Deck {report['deck2_id']}"
            
    # Sort newest first
    data.sort(key=lambda x: x['timestamp'], reverse=True)
    return data

@app.get("/api/hall-of-fame")
async def get_hall_of_fame_api(limit: int = 50):
    """Get the all-time greatest evolved decks."""
    inductees = get_hall_of_fame(limit)
    return {"inductees": inductees, "total": len(inductees)}

@app.get("/api/match/{match_id}")
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


# ─── Feature: Card Pool Coverage Report ─────────────────────────────────

@app.get("/api/card-coverage")
async def get_card_coverage(limit: int = 100):
    """Report showing which cards from the pool are actually played in active decks.
    Returns play rates for each card (percent of active decks using it)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Count active decks
        cursor.execute("SELECT COUNT(*) as c FROM decks WHERE active = TRUE")
        total_decks = cursor.fetchone()['c']
        
        if total_decks == 0:
            return {"total_pool": 0, "total_played": 0, "coverage_percent": 0, "cards": []}
        
        # Get all card lists from active decks
        cursor.execute("SELECT card_list FROM decks WHERE active = TRUE")
        rows = cursor.fetchall()
    
    card_deck_count = {}  # card_name -> number of decks using it
    for row in rows:
        card_list = json.loads(row['card_list'])
        if isinstance(card_list, list):
            unique_cards = set(card_list)
        else:
            unique_cards = set(card_list.keys())
        for name in unique_cards:
            card_deck_count[name] = card_deck_count.get(name, 0) + 1
    
    # Get total card pool size
    try:
        cache = _get_card_search_cache()
        total_pool = len(cache)
    except Exception:
        total_pool = len(card_deck_count)
    
    total_played = len(card_deck_count)
    coverage_pct = round(total_played / max(1, total_pool) * 100, 1)
    
    # Sort by play rate descending
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
