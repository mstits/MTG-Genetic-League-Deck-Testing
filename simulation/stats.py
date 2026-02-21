"""Stats — Game result dataclass and statistics aggregation.

Provides the GameResult structure used by SimulationRunner to report
outcomes, plus a StatsCollector for printing summary statistics across
multiple games (win rates, average turns).
"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class GameResult:
    """Result of a single simulated game.

    Attributes:
        winner:   Name of the winning player, or None for draws/errors.
        turns:    Number of turns the game lasted.
        outcome:  Result type: "Win", "Draw", or "Error".
        game_log: Full action-by-action log of the game.
    """
    winner: Optional[str]
    turns: int
    outcome: str
    game_log: List[str] = field(default_factory=list)


class StatsCollector:
    """Aggregates multiple GameResult instances and prints summary statistics."""

    def __init__(self):
        self.results: List[GameResult] = []

    def add_result(self, result: GameResult):
        """Add a game result to the collection."""
        self.results.append(result)

    def print_summary(self):
        """Print win rate and average turn count across all collected results."""
        total_games = len(self.results)
        if total_games == 0:
            print("No games run.")
            return

        wins = {}
        total_turns = 0

        for res in self.results:
            winner = res.winner if res.winner else "Draw"
            wins[winner] = wins.get(winner, 0) + 1
            total_turns += res.turns

        print(f"--- Simulation Summary ({total_games} games) ---")
        for winner, count in wins.items():
            print(f"{winner}: {count} ({count/total_games*100:.1f}%)")
        print(f"Average Turns: {total_turns/total_games:.2f}")
