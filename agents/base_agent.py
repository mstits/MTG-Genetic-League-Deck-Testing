"""BaseAgent — Abstract interface for all MTG game agents.

All agents must implement `get_action()` which receives the current game
state and the player they control, and returns an action dict with at
minimum a 'type' key matching one of the legal action types.
"""


class BaseAgent:
    """Abstract base class for game-playing agents.

    Subclasses must implement `get_action()` to return a legal action dict.
    The `get_blockers()` method can be overridden for custom blocking logic.
    """

    def get_action(self, game, player) -> dict:
        """Choose an action for the given player in the current game state.

        Args:
            game:   The Game instance with full board state.
            player: The Player object this agent controls.

        Returns:
            An action dict (e.g. {'type': 'pass'}, {'type': 'play_land', 'card': ...}).
        """
        raise NotImplementedError

    def get_blockers(self, game, player, attackers: list) -> dict:
        """Assign blockers to declared attackers.

        Args:
            game:      The Game instance.
            player:    The defending Player.
            attackers: List of attacking Card objects.

        Returns:
            Dict mapping attacker card ID → list of blocking Card objects.
            Empty dict means no blocks.
        """
        return {}
