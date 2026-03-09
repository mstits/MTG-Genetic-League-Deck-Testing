"""commander — Commander (EDH) game mode.

Extends the base Game class with Commander-specific rules:
- 4 players (free-for-all)
- 40 starting life
- Command Zone + commander tax (+2 per cast)
- Commander damage tracking (21 = lose)
- 100-card singleton decks

Usage:
    from engine.commander import CommanderGame
    game = CommanderGame([p1, p2, p3, p4])
    game.start_game()
"""

from typing import List, Optional
from engine.game import Game
from engine.player import Player
from engine.card import Card, StackItem
from engine.zone import Zone
import logging

logger = logging.getLogger(__name__)


class CommanderGame(Game):
    """Commander/EDH variant: multiplayer free-for-all with command zone rules."""
    
    def __init__(self, players: List[Player], commanders: List[Card] = None):
        """
        Args:
            players: 2-4 Player objects (EDH supports 2-4, typically 4)
            commanders: List of commander Cards, one per player (same order)
        """
        # EDH life total
        for p in players:
            p.life = 40
        
        super().__init__(players)
        
        self.num_players = len(players)
        self.command_zone = Zone("Command Zone")
        self.eliminated = set()  # Indices of eliminated players
        
        # Set up commanders
        if commanders:
            for i, (player, commander) in enumerate(zip(players, commanders)):
                player.commander = commander
                commander.controller = player
                commander.is_commander = True
                self.command_zone.add(commander)
                # Remove commander from library if present
                if commander in player.library.cards:
                    player.library.cards.remove(commander)
        
        # Commander damage tracking: player_idx → {source_player_idx: damage}
        self.commander_damage = {i: {} for i in range(self.num_players)}
    
    @property
    def active_player(self):
        """Return the player whose turn it currently is."""
        return self.players[self.active_player_index % self.num_players]
    
    @property
    def priority_player(self):
        """Return the player who currently has priority to cast spells/activate abilities."""
        return self.players[self.priority_player_index % self.num_players]
    
    def opponent(self):
        """In multiplayer, return the 'next' opponent (clockwise)."""
        idx = (self.active_player_index + 1) % self.num_players
        while idx in self.eliminated:
            idx = (idx + 1) % self.num_players
        return self.players[idx]
    
    def get_opponents(self, player: Player) -> List[Player]:
        """Get all opponents of a given player (multiplayer)."""
        return [p for i, p in enumerate(self.players)
                if p != player and i not in self.eliminated]
    
    def get_legal_actions(self):
        """Extended legal actions with commander casting from command zone."""
        actions = super().get_legal_actions()
        
        player = self.priority_player
        player_idx = self.players.index(player)
        
        if player_idx in self.eliminated:
            return [{'type': 'pass'}]
        
        # Commander casting from command zone (Rule 903.7)
        is_main = (self.current_phase in ("Main 1", "Main 2") and
                   player == self.active_player)
        
        if player.commander and player.commander in self.command_zone.cards:
            cmd = player.commander
            can_cast = False
            if cmd.is_instant or cmd.has_flash:
                can_cast = True
            elif is_main:
                can_cast = True
            
            if can_cast:
                # Calculate cost with commander tax
                import re
                tax = player.commander_tax
                base_cost = cmd.cost
                if tax > 0:
                    # Add tax to generic cost
                    generic_match = re.search(r'\{(\d+)\}', base_cost)
                    if generic_match:
                        new_generic = int(generic_match.group(1)) + tax
                        taxed_cost = base_cost.replace(
                            '{' + generic_match.group(1) + '}',
                            '{' + str(new_generic) + '}', 1
                        )
                    else:
                        taxed_cost = '{' + str(tax) + '}' + base_cost
                else:
                    taxed_cost = base_cost
                
                if player.can_pay_cost(taxed_cost, self):
                    actions.append({
                        'type': 'cast_spell', 'card': cmd,
                        'from_command_zone': True,
                        'cost_override': taxed_cost
                    })
        
        return actions
    
    def apply_action(self, action: dict):
        """Extended action handling for commander-specific actions."""
        # Handle commander cast from command zone
        if action.get('from_command_zone') and action.get('type') == 'cast_spell':
            card = action['card']
            player = card.controller
            
            # Remove from command zone
            if card in self.command_zone.cards:
                self.command_zone.cards.remove(card)
            
            # Increment tax for next cast
            player.commander_tax += 2
        
        super().apply_action(action)
    
    def check_state_based_actions(self):
        """Extended SBAs for commander rules."""
        super().check_state_based_actions()
        
        # Commander damage check (Rule 903.10a): 21+ commander damage = lose
        for i, player in enumerate(self.players):
            if i in self.eliminated:
                continue
            for src_idx, dmg in self.commander_damage.get(i, {}).items():
                if dmg >= 21:
                    self._eliminate_player(i, f"21+ commander damage from {self.players[src_idx].name}")
        
        # Player elimination check (life <= 0)
        for i, player in enumerate(self.players):
            if i not in self.eliminated and player.life <= 0:
                self._eliminate_player(i, "life total reached 0")
        
        # Check if game is over (only 1 player remaining)
        remaining = [i for i in range(self.num_players) if i not in self.eliminated]
        if len(remaining) <= 1:
            self.game_over = True
            if remaining:
                self.winner = self.players[remaining[0]]
    
    def _eliminate_player(self, player_idx: int, reason: str):
        """Remove a player from the game."""
        if player_idx in self.eliminated:
            return
        self.eliminated.add(player_idx)
        player = self.players[player_idx]
        self.log_event(f"☠️ {player.name} eliminated: {reason}")
        
        # Remove their permanents from battlefield
        their_cards = [c for c in self.battlefield.cards if c.controller == player]
        for card in their_cards:
            self.battlefield.remove(card)
        
        # Return commander to command zone
        if player.commander:
            player.commander.controller = player
            self.command_zone.add(player.commander)
    
    def _handle_commander_death(self, card: Card):
        """When a commander would die, its owner can return it to the command zone.
        
        Rule 903.9a: If a commander would go to graveyard/exile from anywhere,
        its owner may put it into the command zone instead.
        """
        if getattr(card, 'is_commander', False):
            # Always return to command zone (optimal play)
            if card in self.graveyard.cards:
                self.graveyard.cards.remove(card)
            if card in self.exile.cards:
                self.exile.cards.remove(card)
            self.command_zone.add(card)
            self.log_event(f"Commander {card.name} returned to command zone")
            return True
        return False
    
    def advance_phase(self):
        """Override to skip eliminated players' turns."""
        result = super().advance_phase()
        
        # Skip eliminated players
        while self.active_player_index % self.num_players in self.eliminated:
            self.active_player_index += 1
            if self.active_player_index >= self.num_players * 100:
                self.game_over = True
                break
        
        return result
    
    def track_commander_damage(self, source_player_idx: int, target_player_idx: int, 
                                damage: int):
        """Track combat damage dealt by commanders (Rule 903.10a)."""
        if target_player_idx not in self.commander_damage:
            self.commander_damage[target_player_idx] = {}
        existing = self.commander_damage[target_player_idx].get(source_player_idx, 0)
        self.commander_damage[target_player_idx][source_player_idx] = existing + damage


# ═══════════════════════════════════════════════════════════════
# Commander Bracket System (Rule 903 — Bracket Guidelines)
# ═══════════════════════════════════════════════════════════════

# Bracket 1: "Casual/Precon" — No infinite combos, no tutors, no fast mana
# Bracket 2: "Focused" — Some tutors, themed combos, mid-power
# Bracket 3: "Optimized" — Strong combos, efficient tutors, high power
# Bracket 4: "cEDH" — No restrictions, competitive play
BRACKET_THRESHOLDS = {
    1: (0, 15),    # Power score 0-15
    2: (16, 35),   # Power score 16-35
    3: (36, 60),   # Power score 36-60
    4: (61, 100),  # Power score 61+
}

# Cards/patterns that increase bracket score
CEDH_SIGNALS = {
    'infinite_combo': 20,     # Two-card infinite combos
    'tutor': 5,               # Demonic Tutor, Vampiric Tutor, etc.
    'fast_mana': 8,           # Sol Ring, Mana Crypt, Mana Vault
    'mass_land_destruction': 15,  # Armageddon, Ravages of War
    'extra_turns': 10,        # Time Warp, Nexus of Fate
    'stax': 8,                # Winter Orb, Stasis, Rule of Law
    'free_counterspell': 10,  # Force of Will, Pact of Negation
}

# Known fast mana cards
FAST_MANA = {
    'Sol Ring', 'Mana Crypt', 'Mana Vault', 'Chrome Mox', 'Mox Diamond',
    'Jeweled Lotus', 'Lotus Petal', 'Ancient Tomb', 'Grim Monolith',
    'Mox Opal', 'Mox Amber', 'Carpet of Flowers'
}

# Known tutors
TUTORS = {
    'Demonic Tutor', 'Vampiric Tutor', 'Imperial Seal', 'Enlightened Tutor',
    'Mystical Tutor', 'Worldly Tutor', 'Gamble', 'Entomb', 'Buried Alive',
    'Survival of the Fittest', 'Natural Order', 'Tinker'
}

# Known MLD
MASS_LAND_DESTRUCTION = {
    'Armageddon', 'Ravages of War', 'Catastrophe', 'Obliterate',
    'Jokulhaups', 'Decree of Annihilation', 'Sunder'
}

# Known stax pieces
STAX_PIECES = {
    'Winter Orb', 'Stasis', 'Rule of Law', 'Drannith Magistrate',
    'Collector Ouphe', 'Null Rod', 'Trinisphere', 'Sphere of Resistance',
    'Thorn of Amethyst', 'Blood Moon', 'Back to Basics'
}


def classify_bracket(deck_cards: List[Card]) -> dict:
    """Classify a Commander deck into a bracket (1-4).
    
    Returns:
        {bracket: int, score: float, signals: list, reasoning: str}
    """
    score = 0
    signals = []
    
    names = {c.name for c in deck_cards}
    
    # Fast mana detection
    fast_mana_count = len(names & FAST_MANA)
    if fast_mana_count > 0:
        score += fast_mana_count * CEDH_SIGNALS['fast_mana']
        signals.append(f"Fast mana: {fast_mana_count} pieces")
    
    # Tutor detection
    tutor_count = len(names & TUTORS)
    if tutor_count > 0:
        score += tutor_count * CEDH_SIGNALS['tutor']
        signals.append(f"Tutors: {tutor_count}")
    
    # MLD detection
    mld_count = len(names & MASS_LAND_DESTRUCTION)
    if mld_count > 0:
        score += mld_count * CEDH_SIGNALS['mass_land_destruction']
        signals.append(f"MLD: {mld_count}")
    
    # Stax detection
    stax_count = len(names & STAX_PIECES)
    if stax_count > 0:
        score += stax_count * CEDH_SIGNALS['stax']
        signals.append(f"Stax pieces: {stax_count}")
    
    # Extra turn spells
    extra_turn_count = sum(1 for c in deck_cards 
                          if 'extra turn' in c.oracle_text.lower())
    if extra_turn_count > 0:
        score += extra_turn_count * CEDH_SIGNALS['extra_turns']
        signals.append(f"Extra turns: {extra_turn_count}")
    
    # Free counterspells
    free_counters = sum(1 for c in deck_cards
                       if c.is_counter and ('without paying' in c.oracle_text.lower()
                       or 'pay 1 life' in c.oracle_text.lower()))
    if free_counters > 0:
        score += free_counters * CEDH_SIGNALS['free_counterspell']
        signals.append(f"Free counters: {free_counters}")
    
    # Determine bracket
    bracket = 1
    for b, (low, high) in BRACKET_THRESHOLDS.items():
        if low <= score <= high:
            bracket = b
            break
    if score > 60:
        bracket = 4
    
    return {
        'bracket': bracket,
        'score': score,
        'signals': signals,
        'reasoning': f"Power score {score} → Bracket {bracket} "
                    f"({'Casual' if bracket == 1 else 'Focused' if bracket == 2 else 'Optimized' if bracket == 3 else 'cEDH'})"
    }


def enforce_bracket(deck_cards: List[Card], max_bracket: int) -> List[str]:
    """Check if a deck violates bracket constraints.
    
    Returns list of violation descriptions (empty = compliant).
    """
    result = classify_bracket(deck_cards)
    violations = []
    
    if result['bracket'] > max_bracket:
        violations.append(f"Deck is Bracket {result['bracket']} but max allowed is {max_bracket}")
        violations.extend([f"  Signal: {s}" for s in result['signals']])
    
    return violations

