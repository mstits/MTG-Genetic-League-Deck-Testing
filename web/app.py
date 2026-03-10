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
import logging
import html as html_mod  # For escaping DB values in HTML responses
from data.db import get_db_connection, get_top_cards
from starlette.responses import JSONResponse

app = FastAPI(title="MTG Genetic League", description="AI-powered deck evolution dashboard")

logger = logging.getLogger(__name__)

# CORS — restricted to known origins (production-safe)
from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:3000",
        os.getenv("CORS_ORIGIN", ""),
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── Route Modules ────────────────────────────────────────────────────────────
# Extracted from this monolith into logical groups (see web/routes/)
from web.routes.admin import router as admin_router
from web.routes.meta import router as meta_router
from web.routes.simulation import router as simulation_router
from web.routes.decks import router as decks_router
from web.routes.views import router as views_router
app.include_router(admin_router)
app.include_router(meta_router)
app.include_router(simulation_router)
app.include_router(decks_router)
app.include_router(views_router)


@app.get("/health", response_class=JSONResponse)
async def health_check():
    """Health check endpoint for container orchestration (k8s probes).

    Returns:
        JSON with service status, DB connectivity, and cache state.
        200 if healthy, 503 if DB is unreachable.
    """
    status = {"service": "ok", "db": "unknown", "card_pool_cached": _card_pool_cache is not None}
    http_code = 200
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            status["db"] = "connected"
    except Exception as e:
        logger.warning("Health check: DB unreachable: %s", e)
        status["db"] = "unreachable"
        status["service"] = "degraded"
        http_code = 503
    return JSONResponse(status, status_code=http_code)

# ─── Module-Level Card Pool Cache ─────────────────────────────────────────────
_card_pool_cache = None

def _get_card_pool():
    """Load card pool once and cache at module level. Thread-safe via GIL."""
    global _card_pool_cache
    if _card_pool_cache is not None:
        return _card_pool_cache
    
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
    
    from engine.card_builder import inject_basic_lands
    inject_basic_lands(card_pool)
    
    _card_pool_cache = card_pool
    logger.info("Card pool cached: %d cards", len(card_pool))
    return card_pool


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
    except Exception as e:
        logger.debug("WebSocket connection error: %s", e)
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
        except Exception as e:
            logger.debug("WebSocket send failed, marking for disconnect: %s", e)
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

# / — now in web/routes/views.py

# /api/admin/meta-map — now in web/routes/views.py

# /api/leaderboard — now in web/routes/views.py

# /api/top-cards — now in web/routes/views.py

# /api/top-cards-sidebar — now in web/routes/views.py

# /api/meta — now in web/routes/views.py

# /api/match-history — now in web/routes/views.py

# /api/stats — now in web/routes/views.py

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

# /api/cards/search — now in web/routes/decks.py


# ─── Decklist parser — canonical version in web/helpers.py ────────────────────
from web.helpers import parse_decklist as _parse_decklist

# ─── Simulation endpoints (test-deck, flex-test, mana-calc) ──────────────────
# Now in web/routes/simulation.py


# ─── Matchup Matrix: archetype vs archetype win rates ─────────────────────

# /api/matchups — now in web/routes/views.py

# /matches — now in web/routes/views.py

# /match/{match_id}/replay — now in web/routes/views.py

from engine.engine_config import config as engine_config

# /api/engine/config — now in web/routes/views.py

# /api/engine/config — now in web/routes/views.py

from data.db import get_hall_of_fame

# ─── Admin endpoints — now in web/routes/admin.py ─────────────────────────────

# /api/match/{match_id} — now in web/routes/views.py

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
