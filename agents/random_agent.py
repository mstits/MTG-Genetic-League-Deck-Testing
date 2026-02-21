"""RandomAgent — Agent that selects random legal actions (for testing/baseline)."""

from .base_agent import BaseAgent
import random


class RandomAgent(BaseAgent):
    """Agent that uniformly samples from legal actions.

    Useful as a baseline for evaluating smarter agents — any agent worth
    using should significantly outperform random play.
    """

    def get_action(self, game, player) -> dict:
        """Return a uniformly random legal action."""
        legal_actions = game.get_legal_actions()
        if not legal_actions:
            return {'type': 'pass'}
        return random.choice(legal_actions)
