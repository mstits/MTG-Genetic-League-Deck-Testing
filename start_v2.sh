#!/bin/bash
# start_v2.sh — Full MTG League launcher (venv → DB → cards → web → league)
#
# Sets up virtual environment, initializes database, fetches card data,
# starts the web dashboard on :8000, and runs the league simulation.
# Usage: ./start_v2.sh

# Ensure we are in the script directory
cd "$(dirname "$0")"

# Kill any existing processes
echo "Cleaning up old processes..."
pkill -f "uvicorn web.app:app" 2>/dev/null
pkill -f "run_league.py" 2>/dev/null
sleep 1

# 0. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install fastapi uvicorn jinja2 requests
else
    source .venv/bin/activate
fi

export PYTHONPATH=$(pwd)

# 1. Initialize DB (only if missing)
if [ ! -f "data/league.db" ]; then
    echo "Initializing fresh database..."
    python3 data/db.py
fi

# 2. Check if card data exists
if [ ! -f "data/processed_cards.json" ]; then
    echo "Fetching cards first..."
    python3 scripts/fetch_cards.py
fi

# 3. Build tournament-legal card pool (Legacy format)
echo ""
echo "🃏 Building tournament-legal card pool..."
python3 scripts/filter_legal.py legacy

# 4. Save pool metadata
echo ""
echo "📊 Saving card pool metadata..."
python3 -c "
import json, os
from datetime import datetime
legal = json.load(open('data/legal_cards.json'))
meta = {
    'last_updated': datetime.now().isoformat(),
    'format': 'modern',
    'total_cards': len(legal),
}
json.dump(meta, open('data/pool_metadata.json', 'w'), indent=2)
print(f'  {len(legal)} legal cards cataloged')
"

# 5. Import tournament boss decks
echo ""
echo "🏆 Importing tournament boss decks..."
python3 scripts/import_tournament.py
echo ""

# 6. Start Web Server in background
echo "🌐 Starting Web Dashboard on http://localhost:8000"
python3 -m uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload > web.log 2>&1 &
PID_WEB=$!
sleep 2

# 7. Start League Runner (foreground)
echo "🏟️  Starting League Simulation (Bo3 matches)..."
echo "   Features: 14 keywords, ETB effects, dual lands, sideboards, synergy"
echo ""
python3 -u run_league.py

# Cleanup on exit
kill $PID_WEB 2>/dev/null
