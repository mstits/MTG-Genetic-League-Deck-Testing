"""
The Sovereign Ascendant - Replay Engine

Standardizes the `.sov` JSON-schema for game replays to review AI blunders and novel plays.
"""

import json
import gzip
import os
from typing import Dict, Any, List
from datetime import datetime

class ReplayExporter:
    """Exports structured Game history events into a JSON format for web replay visualization."""
    def __init__(self, output_dir: str = "replays"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.current_replay: Dict[str, Any] = {}

    def init_replay(self, game) -> None:
        """Initializes a new .sov replay record."""
        self.current_replay = {
            "schema_version": "1.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "format": "commander" if len(game.players) > 2 else "standard",
            "players": [
                {
                    "name": p.name,
                    "id": str(id(p)),
                    "deck_hash": getattr(p, "deck_hash", "unknown"),
                    "agent_version": getattr(p, "agent_version", "unknown")
                } for p in game.players
            ],
            "actions": [],
            "game_result": None
        }

    def record_action(self, state: Dict[str, Any], action: Dict[str, Any]) -> None:
        """Records an action and the state delta."""
        self.current_replay["actions"].append({
            "state_vector_subset": state,
            "action": action
        })

    def finalize_replay(self, winner_name: str, total_turns: int) -> str:
        """Compresses and exports the .sov game record."""
        self.current_replay["game_result"] = {
            "winner": winner_name,
            "total_turns": total_turns
        }
        
        filename = f"{self.current_replay['timestamp'].replace(':', '-')}_{winner_name}_win.sov"
        filepath = os.path.join(self.output_dir, filename)
        
        with gzip.open(filepath, 'wt', encoding='utf-8') as f:
            json.dump(self.current_replay, f, indent=2)
            
        return filepath
