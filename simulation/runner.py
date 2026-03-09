"""SimulationRunner — Executes a single MTG game with agent-driven decisions.

Runs the game loop (agent decides → game applies action) until the game
ends via life loss, mill, or safety limits (configured in EngineConfig) to
prevent infinite loops from buggy interactions.

Usage:
    runner = SimulationRunner(game, [agent1, agent2])
    result = runner.run()  # Returns GameResult
"""

from engine.game import Game
from engine.format_validator import FormatValidator, LegalityError
from engine.engine_config import config
from engine.errors import GameStateError, CardInteractionError
from simulation.stats import GameResult
import logging
import traceback

logger = logging.getLogger(__name__)

# Module-level error budget tracking (shared across a season)
_error_counts: dict[str, int] = {}
_total_errors: int = 0


def reset_error_budget() -> None:
    """Reset error counters at the start of each season."""
    global _error_counts, _total_errors
    _error_counts = {}
    _total_errors = 0


def get_error_budget_status() -> dict:
    """Return current error budget status for monitoring."""
    return {
        "total_errors": _total_errors,
        "threshold": config.error_budget_threshold,
        "error_types": dict(_error_counts),
        "budget_exceeded": _total_errors > config.error_budget_threshold,
    }


def _record_error(error: Exception) -> None:
    """Track an error outcome for budget monitoring."""
    global _total_errors
    _total_errors += 1
    error_type = type(error).__name__
    _error_counts[error_type] = _error_counts.get(error_type, 0) + 1
    if _total_errors == config.error_budget_threshold:
        logger.warning(
            "⚠️  Error budget threshold reached (%d errors). Top types: %s",
            _total_errors, dict(_error_counts)
        )


# Genuine code bugs that should NOT be silently caught
_CODE_BUG_TYPES = (TypeError, KeyError, AttributeError, IndexError, NameError, UnboundLocalError)


class SimulationRunner:
    """Drives a Game to completion using two Agent instances.

    Attributes:
        game:        The Game instance to simulate.
        agents:      List of 2 BaseAgent instances (one per player).
        format_name: Optional format for legality validation (e.g. "modern").
        card_pool:   Card pool data for format validation (required if format_name set).
    """

    def __init__(self, game: Game, agents: list,
                 format_name: str = None, card_pool: list = None,
                 capture_snapshots: bool = False):
        self.game = game
        self.agents = agents
        self.format_name = format_name
        self.card_pool = card_pool
        self.capture_snapshots = capture_snapshots
        self.snapshots = []
        self._last_snapshot_turn = -1  # Track when we last took a snapshot

    def run(self) -> GameResult:
        """Execute the game loop until completion or a safety limit is hit.

        Safety limits:
            - Configurable turns: prevents ultra-slow stalemate games
            - Configurable actions: catches infinite loops from buggy card interactions

        Raises:
            LegalityError: If format_name is set and a deck is not legal.

        Returns:
            GameResult with winner, turn count, outcome type, and full game log.
        """
        # Optional pre-game format validation
        if self.format_name and self.card_pool:
            validator = FormatValidator(self.card_pool, self.format_name)
            for player in self.game.players:
                decklist = {}
                for card in player.library:
                    decklist[card.name] = decklist.get(card.name, 0) + 1
                validator.validate(decklist)

        try:
            self.game.start_game()
        except LegalityError:
            raise  # Let legality errors propagate
        except GameStateError as e:
            _record_error(e)
            return GameResult(winner=None, turns=0, outcome="Error",
                              game_log=[f"Start error (game state): {e}"],
                              mulligan_counts={},
                              error_type=type(e).__name__)
        except _CODE_BUG_TYPES as e:
            _record_error(e)
            if config.strict_errors:
                raise  # Re-raise genuine bugs in strict mode
            logger.error("Genuine bug in start_game: %s", traceback.format_exc())
            return GameResult(winner=None, turns=0, outcome="Error",
                              game_log=[f"Start error (BUG): {type(e).__name__}: {e}"],
                              mulligan_counts={},
                              error_type=type(e).__name__)
        except Exception as e:
            _record_error(e)
            return GameResult(winner=None, turns=0, outcome="Error",
                              game_log=[f"Start error: {e}"],
                              mulligan_counts={},
                              error_type=type(e).__name__)

        action_count = 0

        while not self.game.game_over:
            # Turn limit: prevent ultra-long stalemates
            if self.game.turn_count > config.max_turns:
                winner = self._mercy_rule_winner()
                if winner:
                    self.game.log_event(f"RESULT: {winner.name} wins by board advantage (T{self.game.turn_count} limit)")
                    return GameResult(winner=winner.name, turns=self.game.turn_count, outcome="Win",
                                      resolution_reason="turn limit reached (board advantage)",
                                      game_log=self.game.log,
                                      mulligan_counts=getattr(self.game, 'mulligan_counts', {}))
                self.game.log_event(f"RESULT: Draw (turn limit at T{self.game.turn_count}, tied board)")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Draw",
                                  resolution_reason="turn limit reached (tied board)",
                                  game_log=self.game.log,
                                  mulligan_counts=getattr(self.game, 'mulligan_counts', {}))

            # Action limit: catch infinite loops
            if action_count >= config.max_actions:
                winner = self._mercy_rule_winner()
                if winner:
                    self.game.log_event(f"RESULT: {winner.name} wins by board advantage (action limit)")
                    return GameResult(winner=winner.name, turns=self.game.turn_count, outcome="Win",
                                      resolution_reason="action limit reached (board advantage)",
                                      game_log=self.game.log,
                                      mulligan_counts=getattr(self.game, 'mulligan_counts', {}))
                self.game.log_event(f"RESULT: Draw (action limit, tied board)")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Draw",
                                  resolution_reason="action limit reached (tied board)",
                                  game_log=self.game.log,
                                  mulligan_counts=getattr(self.game, 'mulligan_counts', {}))

            try:
                # Get the agent whose priority it is and let them decide
                agent_idx = self.game.priority_player_index
                agent = self.agents[agent_idx]
                
                # Capture snapshot at turn boundaries (not every action)
                # This keeps ~20 snapshots per game instead of ~200+
                if self.capture_snapshots and self.game.turn_count != self._last_snapshot_turn:
                    self._last_snapshot_turn = self.game.turn_count
                    self.snapshots.append({
                        "turn": self.game.turn_count,
                        "action_count": action_count,
                        "agent_index": agent_idx,
                        "game_state": self.game.clone()
                    })
                    
                action = agent.get_action(self.game, self.game.priority_player)
                self.game.apply_action(action)
                action_count += 1
            except GameStateError as e:
                # Expected game-ending condition — log and record as Error
                _record_error(e)
                self.game.log_event(f"GAME STATE ERROR: {e}")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Error",
                                  resolution_reason=f"game state error: {type(e).__name__}",
                                  game_log=self.game.log, snapshots=self.snapshots,
                                  mulligan_counts=getattr(self.game, 'mulligan_counts', {}),
                                  error_type=type(e).__name__)
            except _CODE_BUG_TYPES as e:
                # Genuine code bug — re-raise in strict mode, otherwise log prominently
                _record_error(e)
                if config.strict_errors:
                    raise
                logger.error("BUG in game loop (T%d, action %d): %s\n%s",
                            self.game.turn_count, action_count,
                            e, traceback.format_exc())
                self.game.log_event(f"BUG: {type(e).__name__}: {e}")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Error",
                                  resolution_reason=f"code bug: {type(e).__name__}",
                                  game_log=self.game.log, snapshots=self.snapshots,
                                  mulligan_counts=getattr(self.game, 'mulligan_counts', {}),
                                  error_type=type(e).__name__)
            except Exception as e:
                _record_error(e)
                self.game.log_event(f"ERROR: {type(e).__name__}: {e}")
                return GameResult(winner=None, turns=self.game.turn_count, outcome="Error",
                                  resolution_reason="simulation error",
                                  game_log=self.game.log, snapshots=self.snapshots,
                                  mulligan_counts=getattr(self.game, 'mulligan_counts', {}),
                                  error_type=type(e).__name__)

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
            resolution_reason=getattr(self.game, 'resolution_reason', "Unknown"),
            game_log=self.game.log,
            snapshots=self.snapshots,
            mulligan_counts=getattr(self.game, 'mulligan_counts', {})
        )

    def _mercy_rule_winner(self):
        """Determine winner by board advantage when game hits a safety limit.
        
        T8: Improved scoring to be fair to all archetypes:
            creature_count * 5 + total_power * 3 + life * 1.5
            + hand_size * 3 + non_creature_permanents * 3
            + planeswalker_count * 8 + library_remaining * 0.3
            + graveyard_size * 0.2 (reanimator value)
        The player with the higher score wins. Returns None only if truly tied.
        """
        p1, p2 = self.game.players
        
        def score(player):
            """Calculate heuristic strength of a player's board state to evaluate tied games."""
            creatures = [c for c in self.game.battlefield.cards 
                        if c.controller == player and c.is_creature]
            non_creatures = [c for c in self.game.battlefield.cards 
                           if c.controller == player and not c.is_creature and not c.is_land]
            planeswalkers = [c for c in self.game.battlefield.cards
                           if c.controller == player and c.is_planeswalker]
            total_power = sum(max(0, c.power or 0) for c in creatures)
            hand_size = len(player.hand) if hasattr(player, 'hand') else 0
            library_size = len(player.library) if hasattr(player, 'library') else 0
            graveyard_size = len(player.graveyard) if hasattr(player, 'graveyard') else 0
            return (len(creatures) * 5 + total_power * 3 + player.life * 1.5 +
                    hand_size * 3 + len(non_creatures) * 3 + len(planeswalkers) * 8 +
                    library_size * 0.3 + graveyard_size * 0.2)
        
        s1, s2 = score(p1), score(p2)
        if s1 > s2:
            return p1
        elif s2 > s1:
            return p2
        # Truly tied — use life as final tiebreaker
        if p1.life > p2.life:
            return p1
        elif p2.life > p1.life:
            return p2
        return None  # Truly tied
