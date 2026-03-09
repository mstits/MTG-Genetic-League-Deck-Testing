"""Custom error types for the MTG game engine.

Separates expected game-ending conditions from genuine code bugs:
- GameStateError: expected situations (illegal state, rule conflict)
- CardInteractionError: card-specific interaction failures
- SimulationBudgetError: error budget exceeded for a season

Regular Python exceptions (TypeError, KeyError, AttributeError) indicate
real bugs in the engine and should NOT be caught by the runner in strict mode.
"""


class GameStateError(Exception):
    """Expected game-ending condition — illegal state or rule conflict.

    These are caught and recorded as Error outcomes but are NOT code bugs.
    Examples:
        - A card references a zone that doesn't exist
        - A mandatory action cannot be performed
        - A game state invariant is violated (e.g., negative toughness after SBA)
    """
    pass


class CardInteractionError(GameStateError):
    """A specific card's effect or ability failed during resolution.

    Subclass of GameStateError because card interactions failing is an
    expected category — cards have complex oracle text that may not be
    fully implemented.

    Examples:
        - An ETB trigger targets something that no longer exists
        - A modal spell's chosen mode can't be applied
        - Equipment trying to attach to an invalid target
    """
    pass


class SimulationBudgetError(Exception):
    """Raised when the error budget for a session is exceeded.

    This triggers an alert that something systemic may be wrong —
    too many games are ending in errors, suggesting a bug in the engine
    or a problematic card interaction.
    """

    def __init__(self, error_count: int, threshold: int, error_summary: dict):
        self.error_count = error_count
        self.threshold = threshold
        self.error_summary = error_summary
        super().__init__(
            f"Error budget exceeded: {error_count}/{threshold} games ended in errors. "
            f"Top error types: {error_summary}"
        )
