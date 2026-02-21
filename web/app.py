"""Web Dashboard — FastAPI application serving the league UI and REST API.

Endpoints:
    GET  /                    — Main dashboard (Jinja2 template)
    GET  /api/leaderboard     — Ranked deck listings with ELO and division
    GET  /api/top-cards       — Cards with highest win rates
    GET  /api/meta            — Color distribution and archetype breakdown
    GET  /api/match-history   — Recent match results with pagination
    GET  /api/matchups        — Head-to-head matchup matrix
    GET  /api/stats           — League-wide statistics
    GET  /api/cards/search    — Card name autocomplete for deck builder
    POST /api/test-deck       — Test a user decklist against top league decks

The dashboard uses Tailwind CSS for styling and vanilla JavaScript for
interactivity (tab switching, deck builder, card search autocomplete).
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import os
import json
import re
from data.db import get_db_connection, get_top_cards
from starlette.responses import JSONResponse

app = FastAPI(title="MTG Genetic League", description="AI-powered deck evolution dashboard")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

def parse_cmc(cost):
    if not cost: return 0
    total = 0
    for g in re.findall(r'\{(\d+)\}', cost): total += int(g)
    total += len(re.findall(r'\{([WUBRGC])\}', cost))
    return total

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/leaderboard", response_class=HTMLResponse)
async def get_leaderboard(request: Request):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, division, wins, losses, draws, elo, colors 
            FROM decks 
            WHERE active=1 
            ORDER BY elo DESC 
            LIMIT 50
        ''')
        decks = [dict(row) for row in cursor.fetchall()]
        
    html = ""
    for i, d in enumerate(decks):
        decisive = d['wins'] + d['losses']
        wr = f"{d['wins']/decisive*100:.0f}%" if decisive > 0 else "—"
        draws = d.get('draws', 0) or 0
        colors = d.get('colors', '') or ''
        
        color_map = {'W': '⬜', 'U': '🔵', 'B': '⚫', 'R': '🔴', 'G': '🟢'}
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
        
        # Archetype from name
        arch = ""
        if "-A-" in d['name']: arch = "🗡️"
        elif "-M-" in d['name']: arch = "⚖️"
        elif "-C-" in d['name']: arch = "🛡️"
        
        # Row background based on rank
        if i < 3:
            row_cls = "border-b border-gray-700 hover:bg-yellow-900/20 cursor-pointer transition-colors"
        else:
            row_cls = "border-b border-gray-700 hover:bg-gray-700/50 cursor-pointer transition-colors"
        
        html += f"""
        <tr class="{row_cls}" onclick="window.location.href='/deck/{d['id']}'">
            <td class="p-3 text-gray-500">{i+1}</td>
            <td class="p-3 {name_cls}">{arch} {d['name']}</td>
            <td class="p-3">{color_badges}</td>
            <td class="p-3"><span class="px-2 py-1 rounded-full text-xs {div_cls}">{d['division']}</span></td>
            <td class="p-3 font-mono font-bold text-white">{d['elo']:.0f}</td>
            <td class="p-3 text-sm text-gray-300">{d['wins']}W-{d['losses']}L<span class="text-gray-500">-{draws}D</span></td>
            <td class="p-3 font-mono text-sm text-gray-300">{wr}</td>
        </tr>
        """
    return html

@app.get("/api/top-cards", response_class=HTMLResponse)
async def get_top_cards_api(request: Request):
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
                    {c['card_name']}
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
            WHERE active=1 AND colors != '' AND name NOT LIKE 'BOSS:%'
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
    
    color_map = {'W': '⚪', 'U': '🔵', 'B': '⚫', 'R': '🔴', 'G': '🟢'}
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
    
    for cs in color_stats:
        if not cs['colors']:
            badges = "🔘"
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
        
        html += f"""
        <tr class="border-b border-gray-700 hover:bg-gray-700/50 transition-colors cursor-pointer" onclick="window.location.href='/match/{m['id']}'">
            <td class="p-3">
                <a href="/deck/{m['d1_id']}" class="text-blue-400 hover:underline">{m['deck1_name']}</a>
                <span class="text-gray-500 mx-1">vs</span>
                <a href="/deck/{m['d2_id']}" class="text-blue-400 hover:underline">{m['deck2_name']}</a>
            </td>
            <td class="p-3 font-medium {w_cls}">{winner}</td>
            <td class="p-3 text-sm text-gray-500">T{m['turns']}</td>
        </tr>
        """
    return html

@app.get("/api/stats", response_class=HTMLResponse)
async def get_stats(request: Request):
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
    
    decisive_pct = f"{decisive/total_matches*100:.0f}" if total_matches > 0 else "0"
    
    html = f"""
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
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
    </div>
    """
    return html


@app.get("/deck/{deck_id}", response_class=HTMLResponse)
async def view_deck(request: Request, deck_id: int):
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
        cursor.execute('SELECT * FROM decks WHERE id = ?', (deck_id,))
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
        
        # Recent matches
        cursor.execute('''
            SELECT m.id as match_id, m.turns, m.timestamp,
                   d1.name AS deck1_name, d2.name AS deck2_name,
                   w.name AS winner_name
            FROM matches m
            JOIN decks d1 ON m.deck1_id = d1.id
            JOIN decks d2 ON m.deck2_id = d2.id
            LEFT JOIN decks w ON m.winner_id = w.id
            WHERE m.deck1_id = ? OR m.deck2_id = ?
            ORDER BY m.timestamp DESC
            LIMIT 15
        ''', (deck_id, deck_id))
        matches = [dict(row) for row in cursor.fetchall()]
        
    return templates.TemplateResponse("deck.html", {
        "request": request, "deck": deck, "matches": matches, "card_info": card_info
    })

@app.get("/match/{match_id}", response_class=HTMLResponse)
async def view_match(request: Request, match_id: int):
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
            WHERE m.id = ?
        ''', (match_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("<h1>Match Not Found</h1>", status_code=404)
        match = dict(row)
    
    # Prefer full log from file, fall back to DB summary
    game_log = []
    log_path = match.get('log_path', '')
    if log_path and os.path.exists(log_path):
        with open(log_path, 'r') as f:
            game_log = f.read().splitlines()
    
    if not game_log:
        game_log = json.loads(match.get('game_log', '[]') or '[]')
    
    return templates.TemplateResponse("match.html", {
        "request": request, "match": match, "game_log": game_log
    })


# ─── User Deck Test: Paste a decklist, test vs top league decks ───────────

from fastapi import Form
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
    import traceback
    try:
        try:
            body = await request.json()
            raw_decklist = body.get('decklist', '')
        except:
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
    
    color_map = {'W': '⬜', 'U': '🔵', 'B': '⚫', 'R': '🔴', 'G': '🟢'}
    
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


# ─── Deck Export: Arena/MTGO format ─────────────────────────────────────

@app.get("/api/export/{deck_id}")
async def export_deck(deck_id: int, format: str = "arena"):
    """Export a deck in Arena or MTGO format."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT name, card_list FROM decks WHERE id = ?', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "Deck not found"}, status_code=404)
    
    deck = dict(row)
    cards = json.loads(deck['card_list'])
    if isinstance(cards, list):
        c = {}
        for n in cards: c[n] = c.get(n, 0) + 1
        cards = c
    
    if format == "arena":
        # MTG Arena format: "4 Lightning Bolt"
        lines = [f"// {deck['name']}"]
        lines.append("// Exported from MTG Genetic League")
        lines.append("")
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        text = "\n".join(lines)
    else:
        # MTGO format: similar but with set codes if available
        lines = [f"// {deck['name']}"]
        for name, count in sorted(cards.items()):
            lines.append(f"{count} {name}")
        text = "\n".join(lines)
    
    return JSONResponse({"deck_name": deck['name'], "format": format, "decklist": text})



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
            query += " AND active = ?"
            params.append(active)
        
        if boss is not None:
            if boss:
                query += " AND name LIKE 'BOSS:%'"
            else:
                query += " AND name NOT LIKE 'BOSS:%'"
        
        if colors:
            query += " AND colors = ?"
            params.append(colors)
        
        if division:
            query += " AND division = ?"
            params.append(division)
            
        # Count total for pagination
        count_query = query.replace("SELECT *", "SELECT COUNT(*) as c")
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()['c']
        
        # Fetch page
        query += " ORDER BY elo DESC LIMIT ? OFFSET ?"
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
        cursor.execute('SELECT COUNT(*) as c FROM matches WHERE season_id = ?', (season_id,))
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
            WHERE m.season_id = ?
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
        cursor.execute('SELECT * FROM decks WHERE id = ?', (deck_id,))
        row = cursor.fetchone()
        if not row:
            return HTMLResponse("Deck not found", status_code=404)
        current_deck = dict(row)
        
        # 2. Build graph data (Nodes and Edges)
        nodes = {}  # id -> {name, elo, gen, type}
        edges = set() # (from_id, to_id)
        
        # Helper to fetch deck info
        def get_deck_info(d_id):
            cursor.execute('SELECT id, name, elo, generation, parent_ids FROM decks WHERE id = ?', (d_id,))
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
        cursor.execute("SELECT id, name, elo, generation FROM decks WHERE parent_ids LIKE ?", (f'%{deck_id}%',))
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


@app.get("/matches", response_class=HTMLResponse)
async def view_matches(request: Request):
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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT log_path FROM matches WHERE id = ?', (match_id,))
        row = cursor.fetchone()
        
        if not row or not row['log_path']:
            return HTMLResponse("Log not found for this match", status_code=404)
            
        log_path = row['log_path']
        
        if not os.path.exists(log_path):
            return HTMLResponse(f"Log file missing: {log_path}", status_code=404)
            
        with open(log_path, 'r') as f:
            content = f.read()
            
        from .match_parser import parse_match_log
        data = parse_match_log(content)
        
    return templates.TemplateResponse("replay.html", {
        "request": request,
        "match_data": data
    })

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

