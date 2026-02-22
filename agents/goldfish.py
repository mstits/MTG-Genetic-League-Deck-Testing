"""GoldfishAgent — Simple "goldfish" testing agent.

A goldfish opponent plays solitaire-style: it curves out creatures,
attacks with everything, and never blocks or interacts with the opponent.
Named after the MTG term "goldfishing" — playing against an imaginary
opponent to test a deck's clock speed.

Decision priority:
    1. Attack with all eligible creatures
    2. Play a land
    3. Cast creatures (curve out)
    4. Cast other spells
    5. Pass
"""

from .base_agent import BaseAgent


class GoldfishAgent(BaseAgent):
    """Agent that plays out cards and attacks with everything, never interacting."""

    def get_action(self, game, player) -> dict:
        """Choose the next action using a simple priority system."""
        legal_actions = game.get_legal_actions()
        if not legal_actions:
            return {'type': 'pass'}

        # 1. Attack with everything (goldfish = no blocking consideration)
        for action in legal_actions:
            if action['type'] == 'declare_attackers':
                attackers = action['candidates']
                return {'type': 'declare_attackers', 'attackers': attackers}

        # 2. Play a land
        for action in legal_actions:
            if action['type'] == 'play_land':
                return action

        # 3. Cast creatures first (curve out for maximum pressure)
        for action in legal_actions:
            if action['type'] == 'cast_spell':
                card = action['card']
                if card.is_creature:
                    return action

        # 4. Cast any remaining spells
        for action in legal_actions:
            if action['type'] == 'cast_spell':
                return action

        # 5. Nothing useful to do
        return {'type': 'pass'}
