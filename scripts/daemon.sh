#!/bin/bash
# MTG League Daemon — Background service launcher
# Usage:
#   ./scripts/daemon.sh start     Start the league in background
#   ./scripts/daemon.sh stop      Stop the background league
#   ./scripts/daemon.sh restart   Restart
#   ./scripts/daemon.sh status    Check status
#   ./scripts/daemon.sh logs      Tail the log

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
PID_FILE="$BASE_DIR/.league_daemon.pid"
LOG_FILE="$BASE_DIR/data/league_daemon.log"
VENV_DIR="$BASE_DIR/.venv"

start_daemon() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "League daemon already running (PID $PID)"
            return 1
        fi
    fi
    
    echo "🚀 Starting MTG League Daemon..."
    
    # Activate venv
    source "$VENV_DIR/bin/activate" 2>/dev/null || true
    
    # Start the full system
    cd "$BASE_DIR"
    
    # Start league simulation in background
    nohup python -u start_league.py >> "$LOG_FILE" 2>&1 &
    LEAGUE_PID=$!
    echo $LEAGUE_PID > "$PID_FILE"
    
    # Start dashboard
    nohup python -u -m uvicorn web.app:app --host 0.0.0.0 --port 8000 >> "$LOG_FILE" 2>&1 &
    DASH_PID=$!
    echo $DASH_PID >> "$PID_FILE"
    
    echo "✅ League daemon started"
    echo "   League PID: $LEAGUE_PID"
    echo "   Dashboard PID: $DASH_PID"  
    echo "   Dashboard: http://localhost:8000"
    echo "   Logs: $LOG_FILE"
}

stop_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "No daemon PID file found"
        return 1
    fi
    
    echo "🛑 Stopping MTG League Daemon..."
    
    while IFS= read -r PID; do
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null
            echo "  Stopped PID $PID"
        fi
    done < "$PID_FILE"
    
    # Kill any lingering processes
    pkill -f "start_league.py" 2>/dev/null
    pkill -f "uvicorn web.app" 2>/dev/null
    
    rm -f "$PID_FILE"
    echo "✅ Daemon stopped"
}

status_daemon() {
    if [ ! -f "$PID_FILE" ]; then
        echo "❌ Daemon not running"
        return 1
    fi
    
    echo "📊 MTG League Daemon Status:"
    RUNNING=0
    while IFS= read -r PID; do
        if kill -0 "$PID" 2>/dev/null; then
            CMD=$(ps -p "$PID" -o command= 2>/dev/null | head -c 60)
            echo "  ✅ PID $PID: $CMD"
            RUNNING=$((RUNNING+1))
        else
            echo "  ❌ PID $PID: not running"
        fi
    done < "$PID_FILE"
    
    if [ $RUNNING -eq 0 ]; then
        echo "  No processes are running"
        rm -f "$PID_FILE"
    fi
}

case "$1" in
    start)
        start_daemon
        ;;
    stop)
        stop_daemon
        ;;
    restart)
        stop_daemon
        sleep 2
        start_daemon
        ;;
    status)
        status_daemon
        ;;
    logs)
        tail -f "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
