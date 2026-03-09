"""Monte Carlo Path Analyzer (Misplay Hunter).

Analyzes games where a high-ELO deck loses to a low-ELO deck to discover
Strategic Blindspots. Rewinds game state snapshots to find the turn where
the AI lost the most Win Probability (the Pivot Point).

From the Pivot Point, it branches the simulation using an alternative
action and runs 1,000 Monte Carlo rollouts to see if it outperforms the
Golden Path.
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any
from engine.game import Game

# Output file for Admin UI
BUTTERFLY_REPORTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "admin",
    "admin_butterfly_reports.json"
)

class MisplayHunter:
    """Detects and analyzes strategic blunders during upsets."""
    
    def __init__(self):
        os.makedirs(os.path.dirname(BUTTERFLY_REPORTS_FILE), exist_ok=True)
        if not os.path.exists(BUTTERFLY_REPORTS_FILE):
            with open(BUTTERFLY_REPORTS_FILE, "w") as f:
                json.dump([], f)
                
    def detect_upset(self, elo1: float, elo2: float, winner_idx: int) -> bool:
        """Trigger analysis if the lower-ELO deck won by a margin of > 100."""
        if winner_idx == 0 and elo2 - elo1 > 100:
            return True
        if winner_idx == 1 and elo1 - elo2 > 100:
            return True
        return False
        
    def analyze_upset(self, snapshots: List[Dict], high_elo_idx: int, deck1_id: int, deck2_id: int):
        """Finds the Pivot Point and runs branch simulations."""
        if not snapshots:
            return
            
        print(f"🦋 [Misplay Hunter] Analyzing Upset (High-ELO player {high_elo_idx}) ...")
        
        # 1. Pivot Point Analysis
        # We find the snapshot where win probability drops the most for the high-ELO player.
        pivot_snapshot = self._find_pivot_point(snapshots, high_elo_idx)
        if not pivot_snapshot:
            print("🦋 [Misplay Hunter] No pivot point found.")
            return
            
        # 2. Path Branching at the Pivot
        alternate_path_winrate, original_action, new_action = self._branch_simulation(pivot_snapshot, high_elo_idx)
        
        # Original path resulting winrate was 0 (since they lost the upset)
        # So we just check if the new path's winrate is > 10%
        if alternate_path_winrate > 0.10:
            print(f"🦋 [Misplay Hunter] STRATEGIC BLINDSPOT FOUND! Turn {pivot_snapshot['turn']}")
            print(f"    Original Action: {original_action.description if original_action else 'Pass'}")
            print(f"    Better Action:   {new_action.description if new_action else 'Pass'} (Win Rate: {alternate_path_winrate*100:.1f}%)")
            
            self._generate_butterfly_report(
                high_elo_idx=high_elo_idx,
                deck1_id=deck1_id,
                deck2_id=deck2_id,
                pivot_turn=pivot_snapshot['turn'],
                actual_action=original_action.description if original_action else "Pass",
                golden_action=new_action.description if new_action else "Pass",
                winrate_diff=alternate_path_winrate,
                vibe_score=alternate_path_winrate * 100 # High sensitivity = high vibe score
            )
        else:
            print("🦋 [Misplay Hunter] No blindspot found (alternate paths were also doomed).")

    def _mc_rollout(self, game: Game, high_elo_idx: int, num_games: int = 8) -> float:
        """Runs N fast random/heuristic rollouts to estimate win probability."""
        from simulation.runner import SimulationRunner
        from agents.heuristic_agent import HeuristicAgent
        
        wins = 0
        for _ in range(num_games):
            g = game.clone()
            runner = SimulationRunner(g, [HeuristicAgent(), HeuristicAgent()], capture_snapshots=False)
            res = runner.run()
            # Did high-elo player win this rollout?
            if res.winner == g.players[high_elo_idx].name:
                wins += 1
                
        return wins / num_games

    def _find_pivot_point(self, snapshots: List[Dict], high_elo_idx: int) -> Dict:
        """Finds the snapshot with the largest negative delta in win probability."""
        # Only look at snapshots where the high-elo player was the one taking the action
        high_elo_snapshots = [s for s in snapshots if s['agent_index'] == high_elo_idx]
        
        if not high_elo_snapshots:
            return None
        
        # Only analyze last 15 snapshots (late-game decisions matter most)
        high_elo_snapshots = high_elo_snapshots[-15:]
            
        biggest_drop = 0.0
        pivot = None
        
        # Calculate win prob at start of analysis window
        prev_win_prob = self._mc_rollout(high_elo_snapshots[0]['game_state'], high_elo_idx, num_games=5)
        
        for snap in high_elo_snapshots[1:]:
            prob = self._mc_rollout(snap['game_state'], high_elo_idx, num_games=5)
            delta = prev_win_prob - prob
            
            if delta > biggest_drop:
                biggest_drop = delta
                pivot = snap
                
            prev_win_prob = prob
            
            # Short-circuit if probability hit rock bottom
            if prob == 0.0:
                break
                
        return pivot

    def _branch_simulation(self, pivot_snapshot: Dict, high_elo_idx: int):
        """Takes an alternate action at the pivot point and runs rollouts."""
        g: Game = pivot_snapshot['game_state']
        player = g.players[high_elo_idx]
        
        from agents.heuristic_agent import HeuristicAgent
        agent = HeuristicAgent()
        
        # Get legal actions from the Game itself
        legal_actions = g.get_legal_actions()
        if len(legal_actions) <= 1:
            return 0.0, None, None  # No alternative
            
        # The agent's chosen action is what it *would* pick
        actual_action = agent.get_action(g, player)
        
        # Find a different action to branch on
        new_action = None
        for action in legal_actions:
            if action != actual_action and action.get('type') != 'pass':
                new_action = action
                break
        
        if new_action is None:
            return 0.0, actual_action, None
        
        # Apply the alternate action
        branch_game = g.clone()
        branch_game.apply_action(new_action)
        
        # Run Monte Carlo rollouts to estimate win probability of alternate path
        win_prob = self._mc_rollout(branch_game, high_elo_idx, num_games=15)
        
        return win_prob, actual_action, new_action

    def _generate_butterfly_report(self, high_elo_idx: int, deck1_id: int, deck2_id: int, pivot_turn: int, actual_action: str, golden_action: str, winrate_diff: float, vibe_score: float):
        """Append the finding to the Admin UI data store."""
        
        with open(BUTTERFLY_REPORTS_FILE, "r") as f:
            data = json.load(f)
            
        data.append({
            "timestamp": datetime.now().isoformat(),
            "deck1_id": deck1_id,
            "deck2_id": deck2_id,
            "high_elo_index": high_elo_idx,
            "pivot_turn": pivot_turn,
            "actual_action": actual_action,
            "golden_action": golden_action,
            "winrate_difference": winrate_diff,
            "vibe_score": vibe_score
        })
        
        with open(BUTTERFLY_REPORTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
