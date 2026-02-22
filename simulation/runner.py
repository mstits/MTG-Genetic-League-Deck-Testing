"""SimulationRunner — Executes a single MTG game with agent-driven decisions.

Runs the game loop (agent decides → game applies action) until the game
ends via life loss, mill, or safety limits (50 turns / 500 actions) to
prevent infinite loops from buggy interactions.

Usage:
    runner = SimulationRunner(game, [agent1, agent2])
    result = runner.run()  # Returns GameResult
"""

from engine.game import Game
from simulation.stats import GameResult
import traceback


class SimulationRunner:
    """Drives a Game to completion using two Agent instances.

    Attributes:
        game:   The Game instance to simulate.
        agents: List of 2 BaseAgent instances (one per player).
    """

    def __init__(self, game: Game, agents: list):
        self.game = game
        self.agents = agents

    def run(self) -> GameResult:
        """Execute the game loop until completion or a safety limit is hit.

        Safety limits:
            - 50 turns: prevents ultra-slow stalemate games
            - 500 actions: catches infinite loops from buggy card interactions

        Returns:
            GameResult with winner, turn count, outcome type, and full game log.
        """
        try:
            self.game.start_game()
        except Exception as e:
            return GameResult(winner=None, turns=0, outcome="Error",
                              game_log=[f"Start error: {traceback.format_exc()}"])

        action_count = 0
        max_actions = 500  # Safety valve against infinite loops

        while not self.game.game_over:
            # Turn limit: prevent ultra-long stalemates
            if self.game.turn_count > 50:
                self.game.log_event(f"RESULT: Draw (turn limit reached at T{self.game.turn_count})")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Draw",
                                  game_log=self.game.log)

            # Action limit: catch infinite loops
            if action_count >= max_actions:
                self.game.log_event(f"RESULT: Draw (action limit {max_actions} reached)")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Draw",
                                  game_log=self.game.log)

            try:
                # Get the agent whose priority it is and let them decide
                agent = self.agents[self.game.priority_player_index]
                action = agent.get_action(self.game, self.game.priority_player)
                self.game.apply_action(action)
                action_count += 1
            except Exception as e:
                self.game.log_event(f"ERROR: {traceback.format_exc()}")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Error",
                                  game_log=self.game.log)

        # Determine win condition for reporting
        win_condition = "combat"
        if self.game.winner:
            loser = [p for p in self.game.players if p != self.game.winner]
            if loser:
                loser = loser[0]
                if len(loser.library) == 0:
                    win_condition = "mill"
                elif loser.life <= 0:
                    win_condition = "damage"

        return GameResult(
            winner=self.game.winner.name if self.game.winner else None,
            turns=self.game.turn_count,
            outcome="Win" if self.game.winner else "Draw",
            game_log=self.game.log
        )
