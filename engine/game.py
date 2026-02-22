"""Game — Core game loop implementing Magic: The Gathering rules.

Manages the complete game state including turn structure, phase progression,
stack-based spell resolution with dual-pass priority, combat with keyword
awareness, and state-based actions.

Turn structure (Rule 500.1):
    Untap → Upkeep → Draw → Main 1 → Declare Attackers → Declare Blockers
    → Combat Damage → Main 2 → End → Cleanup

Priority system (Rule 117):
    After any player takes an action, the opponent receives priority.
    Both players must pass consecutively for the top stack item to resolve.
    This enables counter-spells, instant-speed responses, and proper
    trigger interaction.
"""

from typing import List
from .player import Player
from .zone import Zone
from .card import Card, StackItem
import logging

logger = logging.getLogger(__name__)


class Game:
    """Complete game state for a two-player Magic: The Gathering match.

    Attributes:
        players:               List of exactly 2 Player objects.
        turn_count:            Current turn number (starts at 1).
        active_player_index:   Index of the player whose turn it is.
        priority_player_index: Index of the player who may act next.
        phases:                Ordered list of phase names for a turn.
        current_phase:         Name of the current phase.
        stack:                 Zone holding spells/abilities awaiting resolution.
        battlefield:           Zone for all permanents in play.
        exile:                 Zone for exiled cards.
        combat_attackers:      List of creatures declared as attackers.
        combat_blockers:       Dict mapping attacker ID → list of blocking creatures.
        game_over:             Whether the game has ended.
        winner:                The winning Player (None if game ongoing or draw).
    """
    def __init__(self, players: List[Player]):
        self.players = players
        self.turn_count = 0
        self.active_player_index = 0
        self.priority_player_index = 0
        self.phase_index = 0
        self.phases = [
            "Untap", "Upkeep", "Draw", 
            "Main 1", "Declare Attackers", "Declare Blockers", "Combat Damage",
            "Main 2", "End", "Cleanup"
        ]
        self.current_phase = self.phases[0]
        self.stack = Zone("Stack")
        self.battlefield = Zone("Battlefield")
        self.exile = Zone("Exile")
        
        # Combat State
        self.combat_attackers = []
        self.combat_blockers = {}  # attacker_id -> [list of blocker Cards]
        
        self.game_over = False
        self.winner = None
        self.log = []
        self._action_count = 0
        
        # Priority tracking for recursive priority (Rule 117.3d)
        self._consecutive_passes = 0
        
        # Stall detection
        self._last_life_change_turn = 0

    @property
    def active_player(self) -> Player:
        return self.players[self.active_player_index]

    @property
    def priority_player(self) -> Player:
        return self.players[self.priority_player_index]

    @property
    def opponent(self) -> Player:
        return self.players[(self.active_player_index + 1) % 2]
        
    @property
    def defending_player(self) -> Player:
        return self.opponent

    def log_event(self, message: str):
        self.log.append(message)

    def start_game(self):
        """Initialize the game: shuffle, draw 7, mulligan, set up turn 1.

        First player skips their draw step (Rule 103.8) and enters Main 1
        directly after Untap and Upkeep.
        """
        for player in self.players:
            player.shuffle_library()
            player.draw_card(7)
        
        # London Mulligan (Rule 103.4b) — simple heuristic
        for player in self.players:
            self._check_mulligan(player)
        
        self.turn_count = 1
        self.active_player_index = 0
        self.priority_player_index = 0
        self._last_life_change_turn = 1
        self.log_event(f"Game started: {self.players[0].name} vs {self.players[1].name}")
        
        # First player: Untap (harmless T1), Upkeep (triggers fire), skip Draw (Rule 103.8)
        self._do_untap()
        self._fire_upkeep_triggers()
        # Drain any T1 upkeep triggers (no creatures exist T1, so this is a no-op,
        # but prevents orphaned stack items)
        while len(self.stack) > 0:
            self._resolve_stack_top()
        # Skip Draw step, go directly to Main 1
        self.phase_index = 3
        self.current_phase = "Main 1"
        self.priority_player_index = 0
        
        # Log starting state
        self._log_turn_state()
    
    def _check_mulligan(self, player):
        """London Mulligan: if 0-1 or 6-7 lands, shuffle back, draw 7, bottom 1."""
        hand_cards = list(player.hand.cards)
        land_count = sum(1 for c in hand_cards if c.is_land)
        
        if land_count <= 1 or land_count >= 6:
            # Mulligan: put hand back, shuffle, draw 7, bottom 1
            for c in hand_cards:
                player.hand.remove(c)
                player.library.add(c)
            player.shuffle_library()
            player.draw_card(7)
            
            # Bottom the worst card
            new_hand = list(player.hand.cards)
            if new_hand:
                # Bottom a land if too many, or lowest-value spell
                new_land_count = sum(1 for c in new_hand if c.is_land)
                if new_land_count >= 5:
                    bottomed = next(c for c in new_hand if c.is_land)
                else:
                    non_lands = [c for c in new_hand if not c.is_land]
                    if non_lands:
                        bottomed = min(non_lands, key=lambda c: (c.base_power or 0) + (c.base_toughness or 0))
                    else:
                        bottomed = new_hand[-1]
                player.hand.remove(bottomed)
                player.library.cards.append(bottomed)  # Bottom of library
                self.log_event(f"{player.name} mulligans to 6 (bottomed 1)")
    
    def _log_turn_state(self):
        """Log board state at start of turn for strategic analysis."""
        p0, p1 = self.players[0], self.players[1]
        p0_creatures = [c for c in self.battlefield.cards if c.controller == p0 and c.is_creature]
        p1_creatures = [c for c in self.battlefield.cards if c.controller == p1 and c.is_creature]
        p0_lands = sum(1 for c in self.battlefield.cards if c.controller == p0 and c.is_land)
        p1_lands = sum(1 for c in self.battlefield.cards if c.controller == p1 and c.is_land)
        
        self.log_event(f"--- T{self.turn_count} | {p0.name} ({p0.life}hp, {len(p0.hand)}cards, {p0_lands}lands) vs {p1.name} ({p1.life}hp, {len(p1.hand)}cards, {p1_lands}lands) ---")
        
        if p0_creatures or p1_creatures:
            p0_board = ', '.join(f"{c.name} {c.power}/{c.toughness}" for c in p0_creatures) or 'empty'
            p1_board = ', '.join(f"{c.name} {c.power}/{c.toughness}" for c in p1_creatures) or 'empty'
            self.log_event(f"    Board: {p0.name} [{p0_board}] | {p1.name} [{p1_board}]")

    def _reset_all_mana_pools(self):
        """Empty all players' mana pools (Rule 106.4)."""
        for player in self.players:
            player.reset_mana_pool()

    def advance_phase(self):
        """Advance to the next phase of the current turn.

        Handles automatic actions for each phase (untap, draw, combat damage,
        cleanup) and returns True if the game should continue, False if it's over.
        When the last phase completes, wraps to the next player's Untap step.
        """
        if self.game_over:
            return False
        
        current = self.current_phase
        
        if current == "Combat Damage":
            # Combat damage already resolved inline when we reach this check;
            # the actual resolution happens at line ~217 when the phase is entered.
            pass
        
        # Reset mana pools between phases (Rule 106.4)
        self._reset_all_mana_pools()
        
        self.phase_index += 1
        
        if self.phase_index >= len(self.phases):
            # End of turn — go to next player's turn
            self.phase_index = 0
            self.turn_count += 1
            self.active_player_index = (self.active_player_index + 1) % 2
            self.active_player.lands_played_this_turn = 0
            
            # Creatures that survived a full turn cycle lose summoning sickness (Rule 302.6)
            # This is correct: creatures you controlled since the start of your most recent turn
            for card in self.battlefield.cards:
                if card.controller == self.active_player:
                    card.summoning_sickness = False
            
            # Log new turn state
            self._log_turn_state()
        
        self.current_phase = self.phases[self.phase_index]
        
        # === UNTAP STEP (Rule 502.3) ===
        if self.current_phase == "Untap":
            self._do_untap()
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === UPKEEP STEP (Rule 503) ===
        if self.current_phase == "Upkeep":
            self._fire_upkeep_triggers()
            # If triggers were pushed to stack, stay in Upkeep for priority
            # (agents will resolve via the normal pass/pass -> resolve_stack_top loop)
            if len(self.stack) > 0:
                return True
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === DRAW STEP (Rule 504) ===
        if self.current_phase == "Draw":
            if len(self.active_player.library) == 0:
                self.game_over = True
                self.winner = self.players[(self.active_player_index + 1) % 2]
                self.log_event(f"RESULT: {self.active_player.name} can't draw. Winner: {self.winner.name}")
                return False
            self.active_player.draw_card()
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === DECLARE BLOCKERS — give priority to defending player ===
        if self.current_phase == "Declare Blockers":
            if not self.combat_attackers:
                self.phase_index = self.phases.index("Main 2")
                self.current_phase = "Main 2"
            else:
                self.priority_player_index = (self.active_player_index + 1) % 2
                return True
        
        # === COMBAT DAMAGE ===
        if self.current_phase == "Combat Damage":
            self.resolve_combat_damage()
            self.phase_index += 1
            if self.phase_index < len(self.phases):
                self.current_phase = self.phases[self.phase_index]
            if self.game_over:
                return False
        
        # === END STEP → CLEANUP (Rule 513 → 514) ===
        if self.current_phase == "End":
            # End step: just advance to Cleanup
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === CLEANUP STEP (Rule 514) ===
        if self.current_phase == "Cleanup":
            self._do_cleanup()
            self.phase_index += 1  # Will wrap to next turn on next call
            if self.phase_index < len(self.phases):
                self.current_phase = self.phases[self.phase_index]
        
        if self.current_phase not in ("Declare Blockers",):
            self.priority_player_index = self.active_player_index
        
        # Reset priority pass counter for the new phase
        self._consecutive_passes = 0
        
        self.check_state_based_actions()
        return not self.game_over

    def _do_untap(self):
        """Untap all permanents controlled by the active player (Rule 502.3)."""
        for card in self.battlefield.cards:
            if card.controller == self.active_player:
                card.tapped = False

    def _fire_upkeep_triggers(self):
        """Fire 'at the beginning of your upkeep' triggers (Rule 503.1).
        Triggers go on the stack so opponents can respond."""
        active = self.active_player
        for card in list(self.battlefield.cards):
            if card.controller == active and hasattr(card, 'upkeep_effect') and card.upkeep_effect:
                # Push trigger onto the stack (Rule 603.3)
                trigger = StackItem(
                    effect=card.upkeep_effect,
                    source=card,
                    controller=active,
                    description=f"Upkeep trigger: {card.name}"
                )
                self.stack.cards.append(trigger)
                self.log_event(f"T{self.turn_count}: Upkeep trigger: {card.name}")
        
        # If triggers were added, resolve them immediately (simplified —
        # full implementation would give priority to both players)
        # REMOVED: auto-resolve loop. Triggers stay on stack for agent interaction.
        # The game loop's dual-pass priority system handles resolution.
        # while len(self.stack) > 0:
        #     self._resolve_stack_top()
        #     self.check_state_based_actions()
        #     if self.game_over:
        #         return

    def _do_cleanup(self):
        """Cleanup step (Rule 514):
        1. Active player discards to max hand size (7)
        2. Remove all damage from permanents
        3. End 'until end of turn' effects
        """
        # 514.1: Discard to hand size
        player = self.active_player
        while len(player.hand) > 7:
            # Discard worst card (lowest CMC non-land, or a land if all lands)
            cards = list(player.hand.cards)
            non_lands = [c for c in cards if not c.is_land]
            if non_lands:
                # Discard lowest-value card
                worst = min(non_lands, key=lambda c: (c.power or 0) + (c.toughness or 0))
            else:
                worst = cards[-1]
            player.hand.remove(worst)
            player.graveyard.add(worst)
            self.log_event(f"T{self.turn_count}: {player.name} discards {worst.name} (hand size)")
        
        # 514.2: Remove all damage marked on permanents + clear temp modifiers
        for card in self.battlefield.cards:
            if card.is_creature:
                card.damage_taken = 0
                card.deathtouch_damaged = False
                card.clear_temp_modifiers()  # Remove "until end of turn" effects (Rule 514.2)
            if card.is_planeswalker:
                card.loyalty_used_this_turn = False  # Reset PW ability usage
            if card.is_vehicle:
                card.is_crewed = False  # Un-crew vehicles at end of turn

    def _apply_static_effects(self):
        """Apply static/continuous effects from permanents on the battlefield (Rule 613).
        This is called after SBAs to keep the board state consistent.
        Static effects use temp modifiers that are recalculated each time."""
        # First, clear all static-applied temp modifiers (tagged with '_static')
        for card in self.battlefield.cards:
            if card.is_creature:
                card._temp_modifiers = [m for m in card._temp_modifiers if not m.get('_static')]
        
        # Then, reapply all static effects from permanents
        for source in self.battlefield.cards:
            if not hasattr(source, 'static_effect') or not source.static_effect:
                continue
            
            se = source.static_effect
            p_buff = se.get('power', 0)
            t_buff = se.get('toughness', 0)
            effect_filter = se.get('filter', '')
            
            for target in self.battlefield.cards:
                if not target.is_creature:
                    continue
                
                apply = False
                
                if effect_filter == 'other_creatures':
                    # "Other creatures you control"
                    if target.controller == source.controller and target is not source:
                        apply = True
                elif effect_filter == 'all_creatures':
                    # "Creatures you control" (including self)
                    if target.controller == source.controller:
                        apply = True
                elif effect_filter == 'type':
                    # Tribal: "other [Type]s you control"
                    req_type = se.get('type', '')
                    if (target.controller == source.controller and target is not source
                            and req_type in target.creature_types):
                        apply = True
                
                if apply:
                    target._temp_modifiers.append({
                        'power': p_buff, 'toughness': t_buff, '_static': True
                    })

    def get_legal_actions(self) -> List[dict]:
        if self.game_over:
            return [{'type': 'pass'}]

        player = self.priority_player
        actions = [{'type': 'pass'}]

        # Play Land
        if (self.current_phase in ("Main 1", "Main 2") and 
            player == self.active_player and 
            player.lands_played_this_turn < player.max_lands_per_turn):
            seen_land_names = set()
            for card in player.hand.cards:
                if card.is_land and card.name not in seen_land_names:
                    actions.append({'type': 'play_land', 'card': card})
                    seen_land_names.add(card.name)

        # Cast Spells
        is_main = (self.current_phase in ("Main 1", "Main 2") and 
                   player == self.active_player)

        for card in player.hand.cards:
            if card.is_land:
                continue
            can_cast = False
            if card.is_instant or card.has_flash:
                can_cast = True
            elif is_main:
                can_cast = True
            
            if can_cast and player.can_pay_cost(card.cost, self):
                # Auras need targets (Rule 303.4a)
                if card.is_aura:
                    # Generic "Enchant creature"
                    # Only generic implemented so far
                    valid_targets = []
                    for potential_target in self.battlefield.cards:
                        if potential_target.is_creature and not potential_target.has_hexproof and not potential_target.is_protected_from(card):
                            valid_targets.append(potential_target)
                    
                    if valid_targets:
                        # Expand actions: one for each target
                        for t in valid_targets:
                            actions.append({'type': 'cast_spell', 'card': card, 'target': t})
                else:
                    # Normal spell
                    actions.append({'type': 'cast_spell', 'card': card})
                    
                    # Kicker variant (Rule 702.32): offer kicked version if can afford
                    if card.kicker_cost:
                        from engine.player import Player
                        total_cost = card.cost + card.kicker_cost  # Concatenate cost strings
                        if player.can_pay_cost(total_cost, self):
                            actions.append({
                                'type': 'cast_spell', 'card': card,
                                'kicked': True, 'cost_override': total_cost
                            })
        
        # Flashback: cast from graveyard (Rule 702.33)
        for card in player.graveyard.cards:
            if not card.flashback_cost:
                continue
            can_cast = False
            if card.is_instant or card.has_flash:
                can_cast = True
            elif is_main:
                can_cast = True
            if can_cast and player.can_pay_cost(card.flashback_cost, self):
                actions.append({
                    'type': 'cast_spell', 'card': card,
                    'from_graveyard': True, 'cost_override': card.flashback_cost
                })
        
        # Cycling: discard from hand to draw (Rule 702.28, any time)
        for card in player.hand.cards:
            if card.cycling_cost and player.can_pay_cost(card.cycling_cost, self):
                actions.append({'type': 'cycle', 'card': card})
        
        # Equip Abilities (Rule 702.6) via Sorcery speed
        if self.current_phase in ("Main 1", "Main 2") and player == self.active_player:
            for card in self.battlefield.cards:
                if (card.controller == player and 
                    hasattr(card, 'equip_cost') and card.equip_cost and 
                    hasattr(card, 'equipped_to')):
                    
                    # Can only equip to own creatures
                    targets = [c for c in self.battlefield.cards 
                              if c.controller == player and c.is_creature and c != card.equipped_to 
                              and not c.has_hexproof and not c.is_protected_from(card)]
                    
                    if targets and player.can_pay_cost(card.equip_cost, self):
                        for t in targets:
                            actions.append({
                                'type': 'equip',
                                'source': card, 
                                'target': t,
                                'cost': card.equip_cost
                            })
        
        # Crew Vehicles (Rule 702.122 — sorcery speed)
        if is_main:
            for card in self.battlefield.cards:
                if (card.controller == player and card.is_vehicle
                    and not card.is_crewed and card.crew_cost > 0):
                    crew_pool = [c for c in self.battlefield.cards
                                if c.controller == player and c.is_creature
                                and not c.tapped]
                    total_power = sum(c.power or 0 for c in crew_pool)
                    if total_power >= card.crew_cost:
                        actions.append({'type': 'crew', 'vehicle': card})
        
        # Loyalty Abilities — Planeswalkers (Rule 606, sorcery speed, 1/turn)
        if is_main:
            for card in self.battlefield.cards:
                if card.controller != player or not card.is_planeswalker:
                    continue
                if card.loyalty_used_this_turn:
                    continue
                for i, ability in enumerate(card.loyalty_abilities):
                    cost = ability['cost']
                    # Can only activate if loyalty + cost >= 0 (can't go below 0)
                    if card.loyalty + cost >= 0:
                        actions.append({
                            'type': 'loyalty_ability',
                            'card': card,
                            'ability_index': i,
                            'cost': cost
                        })

        # Activated Abilities (Rule 602)
        if True:  # Activated abilities can be used at instant speed (Rule 602.1)
            for card in self.battlefield.cards:
                if card.controller != player:
                    continue
                if not hasattr(card, 'activated_abilities'):
                    continue
                for i, ability in enumerate(card.activated_abilities):
                    # Check if ability can be activated
                    can_activate = True
                    
                    # Tap cost: card must be untapped and not have summoning sickness
                    # (unless the ability is a mana ability)
                    if ability.get('cost_tap'):
                        if card.tapped:
                            can_activate = False
                        if card.summoning_sickness and not ability.get('is_mana_ability'):
                            can_activate = False
                    
                    # Sacrifice cost: card must be on battlefield (always True at this point)
                    # Mana cost check
                    if ability.get('cost_mana') and not player.can_pay_cost(ability['cost_mana'], self):
                        can_activate = False
                    
                    if can_activate:
                        actions.append({
                            'type': 'activate_ability',
                            'card': card,
                            'ability_index': i,
                            'ability': ability
                        })
        
        # Declare Attackers (Rule 508.1a)
        if (self.current_phase == "Declare Attackers" and 
            player == self.active_player):
            
            attackers = []
            for card in self.battlefield.cards:
                if (card.controller == player and 
                    card.is_creature and 
                    not card.tapped and 
                    not card.summoning_sickness and
                    card.can_attack):  # Defender check via can_attack (Rule 702.3b)
                    attackers.append(card)
            
            if attackers:
                actions.append({'type': 'declare_attackers', 'candidates': attackers})

        # Declare Blockers (Rule 509.1a)
        if (self.current_phase == "Declare Blockers" and 
            player == self.defending_player and
            self.combat_attackers):
            
            blockers = []
            for card in self.battlefield.cards:
                if (card.controller == player and 
                    card.can_block and 
                    not card.tapped):
                    blockers.append(card)
            
            if blockers:
                actions.append({'type': 'declare_blockers', 'candidates': blockers, 'attackers': self.combat_attackers})

        return actions

    def _can_block(self, attacker: Card, blocker: Card) -> bool:
        """Check if a specific blocker can block an attacker (1-on-1 checks)."""
        if not blocker.is_creature: return False
        
        # Flying (Rule 702.9b)
        if attacker.has_flying and not blocker.can_block_flyer:
            return False
            
        # Protection (Rule 702.16)
        if attacker.is_protected_from(blocker):
            return False
            
        return True

    def _validate_blocking(self, attacker: Card, blockers: List[Card]) -> bool:
        """Check if a group of blockers is a legal assignment for an attacker."""
        if not blockers:
            return True # No blocks is valid (unless "must be blocked")
            
        # Individual legality
        for b in blockers:
            if not self._can_block(attacker, b):
                return False
        
        # Menace (Rule 702.111b)
        if attacker.has_menace and len(blockers) < 2:
            return False
            
        return True

    def apply_action(self, action: dict):
        """Execute a player action and update game state.

        Handles all action types: pass (with priority/stack resolution),
        play_land, cast_spell, cycle, declare_attackers, declare_blockers,
        crew, equip, loyalty_ability, activate_ability.

        After casting a spell, priority passes to the opponent (Rule 601).
        """
        self._action_count += 1
        player = self.priority_player
        
        if action['type'] == 'pass':
            if len(self.stack) > 0:
                # Recursive priority (Rule 117.3d): both players must pass
                # in succession for the top item to resolve
                self._consecutive_passes += 1
                if self._consecutive_passes >= 2:
                    self._resolve_stack_top()
                    self._consecutive_passes = 0
                    # After resolving, active player gets priority (Rule 117.3b)
                    self.priority_player_index = self.active_player_index
                    # If more items on stack, continue the priority loop
                    if len(self.stack) > 0:
                        return  # Let agents respond to the next item
                    # Stack empty — check SBAs
                    self.check_state_based_actions()
                else:
                    # Pass priority to the other player
                    self.priority_player_index = (self.priority_player_index + 1) % 2
            else:
                self._consecutive_passes = 0
                if self.current_phase == "Declare Attackers":
                    self.combat_blockers = {}
                self.advance_phase()
        
        elif action['type'] == 'play_land':
            card = action['card']
            self.log_event(f"T{self.turn_count}: {player.name} plays {card.name}")
            player.play_land(card, self)
            
            # Landfall triggers (Rule 702.52): fire for controlled permanents
            for perm in self.battlefield.cards:
                if perm.controller == player and perm.landfall_effect:
                    trigger = StackItem(
                        effect=perm.landfall_effect,
                        source=perm,
                        controller=player,
                        description=f"Landfall: {perm.name}"
                    )
                    self.stack.cards.append(trigger)
                    self.log_event(f"T{self.turn_count}: Landfall trigger: {perm.name}")
        
        elif action['type'] == 'cycle':
            card = action['card']
            self._consecutive_passes = 0
            player.pay_cost(card.cycling_cost, self)
            player.hand.remove(card)
            player.graveyard.add(card)
            player.draw_card(1)
            self.log_event(f"T{self.turn_count}: {player.name} cycles {card.name} (pay {card.cycling_cost}, draw 1)")
        
        elif action['type'] == 'cast_spell':
            card = action['card']
            self._consecutive_passes = 0  # Reset on new spell
            is_flashback = action.get('from_graveyard', False)
            is_kicked = action.get('kicked', False)
            actual_cost = action.get('cost_override', card.cost)
            
            # Mark card as kicked (Rule 702.32)
            if is_kicked:
                card.was_kicked = True
            
            kick_label = ' (kicked)' if is_kicked else ''
            flash_label = ' (flashback)' if is_flashback else ''
            self.log_event(f"T{self.turn_count}: {player.name} casts {card.name} ({actual_cost}){flash_label}{kick_label}")
            
            # Ward is now checked at resolution time in _resolve_stack_top,
            # not at cast time (the target isn't known here for generic spells)
            
            # Pay cost and move to stack
            player.pay_cost(actual_cost, self)
            if is_flashback:
                player.graveyard.remove(card)
                card.from_graveyard = True  # Mark for exile after resolution
            else:
                player.hand.remove(card)
            card.controller = player
            self.stack.add(card)
            
            # Prowess trigger: noncreature spell cast (Rule 702.107)
            if not card.is_creature:
                for perm in self.battlefield.cards:
                    if perm.controller == player and perm.is_creature and perm.has_prowess:
                        perm._temp_modifiers.append({'power': 1, 'toughness': 1})  # +1/+1 until end of turn
                        self.log_event(f"  Prowess: {perm.name} gets +1/+1")
            
            # Priority passing (Rule 601/608)
            self.priority_player_index = (self.players.index(player) + 1) % 2

            # Handle Aura targeting
            if card.is_aura and 'target' in action:
                card.enchant_target_ptr = action['target']
            
        elif action['type'] == 'crew':
            vehicle = action['vehicle']
            self._consecutive_passes = 0
            # Tap creatures with smallest power first to reach crew cost
            crew_pool = [c for c in self.battlefield.cards
                        if c.controller == player and c.is_creature
                        and not c.tapped]
            crew_pool.sort(key=lambda c: c.power or 0)
            crewed_power = 0
            crewed_names = []
            for c in crew_pool:
                if crewed_power >= vehicle.crew_cost:
                    break
                c.tapped = True
                crewed_power += (c.power or 0)
                crewed_names.append(c.name)
            if crewed_power >= vehicle.crew_cost:
                vehicle.is_crewed = True
                self.log_event(f"T{self.turn_count}: {player.name} crews {vehicle.name} "
                             f"(tapped {', '.join(crewed_names)})")
            
        elif action['type'] == 'equip':
            card = action['source']  # The Equipment
            target = action['target'] # The Creature
            cost = action['cost']
            player.pay_cost(cost, self)
            
            self.log_event(f"T{self.turn_count}: {player.name} equips {card.name} to {target.name}")
            
            # Equip is a sorcery-speed activated ability (uses stack)
            def equip_effect(game, source):
                if target in game.battlefield.cards and source in game.battlefield.cards:
                    # Unattach from old (if any)
                    if source.equipped_to and source.equipped_to in source.equipped_to.attachments:
                        source.equipped_to.attachments.remove(source)
                    
                    # Attach to new
                    source.equipped_to = target
                    target.attachments.append(source)
                    game.log_event(f"Equip resolves: {source.name} attached to {target.name}")
            
            trigger = StackItem(
                effect=equip_effect,
                source=card,
                controller=player,
                description=f"Equip {card.name} to {target.name}"
            )
            self.stack.cards.append(trigger)
            self.priority_player_index = (self.players.index(player) + 1) % 2
            
        elif action['type'] == 'loyalty_ability':
            card = action['card']
            idx = action['ability_index']
            cost = action['cost']
            ability = card.loyalty_abilities[idx]
            self._consecutive_passes = 0
            
            # Adjust loyalty (Rule 606.5)
            card.loyalty += cost
            card.loyalty_used_this_turn = True
            
            cost_label = f"+{cost}" if cost >= 0 else str(cost)
            self.log_event(f"T{self.turn_count}: {player.name} activates {card.name} [{cost_label}]: {ability['description']} (loyalty→{card.loyalty})")
            
            # Push effect onto stack
            trigger = StackItem(
                effect=ability['effect'],
                source=card,
                controller=player,
                description=f"PW: {card.name} [{cost_label}]"
            )
            self.stack.cards.append(trigger)
            self.priority_player_index = (self.players.index(player) + 1) % 2

        elif action['type'] == 'activate_ability':
            card = action['card']
            ability = action['ability']
            self._consecutive_passes = 0
            
            self.log_event(f"T{self.turn_count}: {player.name} activates {card.name}: {ability.get('description', '')}")
            
            # Pay costs
            if ability.get('cost_tap'):
                card.tapped = True
            if ability.get('cost_mana'):
                player.pay_cost(ability['cost_mana'], self)
            if ability.get('cost_sacrifice'):
                if card in self.battlefield.cards:
                    self.battlefield.remove(card)
                    card.controller.graveyard.add(card)
                    # Fire death trigger
                    self._fire_death_trigger(card)
            
            # Mana abilities don't use the stack (Rule 605.3b)
            if ability.get('is_mana_ability'):
                try:
                    ability['effect'](self, card)
                except Exception as e:
                    logger.warning(f"Mana ability error on {card.name}: {e}")
            else:
                # Non-mana abilities go on the stack
                trigger = StackItem(
                    effect=ability['effect'],
                    source=card,
                    controller=player,
                    description=f"Ability: {card.name} — {ability.get('description', '')}"
                )
                self.stack.cards.append(trigger)
                self.priority_player_index = (self.players.index(player) + 1) % 2
            
        elif action['type'] == 'declare_attackers':
            attackers = action.get('attackers', [])
            self.combat_attackers = []
            for card in attackers:
                if card in self.battlefield.cards and not card.tapped:
                    # Vigilance: don't tap (Rule 702.20b)
                    if not card.has_vigilance:
                        card.tapped = True
                    self.combat_attackers.append(card)
            if self.combat_attackers:
                names = ', '.join(f"{c.name}({c.power}/{c.toughness})" for c in self.combat_attackers)
                self.log_event(f"T{self.turn_count}: {player.name} attacks with {names}")
                
                # Fire attack triggers (Rule 508.1a)
                for att in self.combat_attackers:
                    if att.attack_trigger:
                        trigger = StackItem(
                            effect=att.attack_trigger,
                            source=att,
                            controller=att.controller,
                            description=f"Attack trigger: {att.name}"
                        )
                        self.stack.cards.append(trigger)
                        self.log_event(f"T{self.turn_count}: Attack trigger: {att.name}")
            # After attackers declared, advance to Blockers (Rule 508→509)
            self.advance_phase()

        elif action['type'] == 'declare_blockers':
            blocks = action.get('blocks', {})
            
            valid_blocks = {}
            for att_id, blocker_list in blocks.items():
                att = None
                for a in self.combat_attackers:
                    if a.id == att_id:
                        att = a
                        break
                if not att:
                    continue
                
                if not self._validate_blocking(att, blocker_list):
                    self.log_event(f"T{self.turn_count}: Invalid block for {att.name}")
                    continue
                
                valid_blocks[att_id] = blocker_list
            
            self.combat_blockers = valid_blocks
            # After blocks are declared, advance to Combat Damage (Rule 509→510)
            self.advance_phase()
        
        # Turn limit is handled by SimulationRunner (50 turns → draw)

    def resolve_combat_damage(self):
        """Keyword-aware combat: flying, first strike, deathtouch, lifelink, trample, menace.
        Damage is MARKED on creatures (Rule 120.6) and persists until cleanup."""
        opponent = self.defending_player
        active = self.active_player
        prev_life_0 = self.players[0].life
        prev_life_1 = self.players[1].life
        
        # === FIRST STRIKE PHASE (Rule 510.4) ===
        first_strikers = [c for c in self.combat_attackers 
                         if c in self.battlefield.cards and (c.has_first_strike or c.has_double_strike)]
        
        if first_strikers:
            self._resolve_damage_for(first_strikers, opponent, active, is_first_strike=True)
            # Check SBAs after first strike (creatures may die)
            self.check_state_based_actions()
        
        # === NORMAL DAMAGE PHASE ===
        normal_attackers = [c for c in self.combat_attackers 
                          if c in self.battlefield.cards and 
                          (not c.has_first_strike or c.has_double_strike)]
        
        self._resolve_damage_for(normal_attackers, opponent, active, is_first_strike=False)
        
        # Track life changes
        if self.players[0].life != prev_life_0 or self.players[1].life != prev_life_1:
            self._last_life_change_turn = self.turn_count
        
        self.combat_attackers = []
        self.combat_blockers = {}
        self.check_state_based_actions()
    
    def _resolve_damage_for(self, attackers, opponent, active, is_first_strike=False):
        """Resolve damage for a set of attackers.
        Damage is MARKED on creatures (Rule 120.6), not immediately lethal."""
        for att_card in attackers:
            if att_card not in self.battlefield.cards:
                continue
            blockers = self.combat_blockers.get(att_card.id, [])
            # Filter out blockers that already left the battlefield
            blockers = [b for b in blockers if b in self.battlefield.cards]
            power = att_card.power or 0
            
            if not blockers:
                # Unblocked — damage to player or planeswalker (Rule 510.1b)
                if power > 0:
                    # Heuristic: attack PW if opponent has one (simplified)
                    opp_pws = [c for c in self.battlefield.cards
                              if c.controller == opponent and c.is_planeswalker and c.loyalty > 0]
                    target_pw = getattr(att_card, '_attacking_pw', None)
                    if target_pw and target_pw in opp_pws:
                        # Damage removes loyalty (Rule 306.7)
                        target_pw.loyalty -= power
                        self.log_event(f"T{self.turn_count}: {att_card.name} deals {power} to {target_pw.name} (loyalty→{target_pw.loyalty})")
                    else:
                        opponent.life -= power
                        self.log_event(f"T{self.turn_count}: {att_card.name} deals {power} to {opponent.name} ({opponent.life} life)")
                        
                        # Combat damage trigger (deals damage to player)
                        if att_card.combat_damage_trigger:
                            trigger = StackItem(
                                effect=att_card.combat_damage_trigger,
                                source=att_card,
                                controller=att_card.controller,
                                description=f"Combat damage trigger: {att_card.name}"
                            )
                            self.stack.cards.append(trigger)
                            self.log_event(f"T{self.turn_count}: Combat damage trigger: {att_card.name}")
                    # Lifelink (Rule 702.15b)
                    if att_card.has_lifelink:
                        active.life += power
                        self.log_event(f"  Lifelink: {active.name} gains {power} life ({active.life})")
            else:
                # Blocked — attacker divides damage among blockers (Rule 510.1c)
                remaining_damage = power
                for blk in blockers:
                    if blk not in self.battlefield.cards:
                        continue
                    blk_toughness = blk.toughness or 1
                    
                    # Calculate lethal damage for this blocker
                    # Deathtouch: any nonzero damage is lethal (Rule 702.2c)
                    if att_card.has_deathtouch:
                        damage_to_blocker = min(remaining_damage, 1)  # 1 is lethal
                    else:
                        # Assign lethal damage (toughness - existing damage)
                        lethal = max(0, blk_toughness - blk.damage_taken)
                        damage_to_blocker = min(remaining_damage, lethal)
                    
                    # Protection prevents damage (Rule 702.16d)
                    if blk.is_protected_from(att_card):
                        damage_to_blocker = 0
                    
                    # Mark damage on blocker (Rule 120.6)
                    blk.damage_taken += damage_to_blocker
                    if att_card.has_deathtouch and damage_to_blocker > 0:
                        blk.deathtouch_damaged = True
                    
                    # Lifelink on attacker for damage dealt to blocker
                    if att_card.has_lifelink and damage_to_blocker > 0:
                        active.life += damage_to_blocker
                    
                    remaining_damage -= damage_to_blocker
                    
                    # Blocker damages attacker (only in normal phase, or if blocker has first strike)
                    if not is_first_strike or blk.has_first_strike or blk.has_double_strike:
                        blk_power = blk.power or 0
                        if blk_power > 0:
                            # Protection on attacker prevents blocker damage
                            if not att_card.is_protected_from(blk):
                                att_card.damage_taken += blk_power
                                if blk.has_deathtouch:
                                    att_card.deathtouch_damaged = True
                            # Lifelink on blocker
                            if blk.has_lifelink:
                                opponent.life += blk_power
                    
                    if remaining_damage <= 0:
                        break
                
                # Trample: remaining damage goes to opponent (Rule 702.19b)
                if remaining_damage > 0 and att_card.has_trample:
                    opponent.life -= remaining_damage
                    self.log_event(f"T{self.turn_count}: {att_card.name} tramples {remaining_damage} to {opponent.name} ({opponent.life})")
                    if att_card.has_lifelink:
                        active.life += remaining_damage

    def _resolve_stack_top(self):
        """Resolve the top item of the stack (Rule 608.2)."""
        if len(self.stack) == 0:
            return
        
        item = self.stack.cards.pop()
        
        if isinstance(item, StackItem):
            # Triggered/activated ability resolving
            try:
                item.effect(self, item.source)
            except Exception as e:
                logger.warning(f"Ability resolution error ({item.description}): {e}")
            self.check_state_based_actions()
            return
        
        # It's a Card (spell)
        card = item
        
        if card.is_creature:
            card.controller = card.controller or self.active_player
            card.summoning_sickness = not card.has_haste  # Rule 302.6 / 702.10
            card.tapped = False
            card.damage_taken = 0
            card.deathtouch_damaged = False
            
            # Enters-tapped (Rule 305.7 analog for creatures — rare but exists)
            if hasattr(card, 'enters_tapped') and card.enters_tapped:
                card.tapped = True
            
            self.battlefield.add(card)
            
            # Apply +1/+1 counters from ETB (Rule 122)
            # (counters are already set from card.__post_init__)
            
            # Trigger ETB effect — push onto stack (Rule 603.3)
            if card.etb_effect:
                trigger = StackItem(
                    effect=card.etb_effect,
                    source=card,
                    controller=card.controller,
                    description=f"ETB: {card.name}"
                )
                self.stack.cards.append(trigger)
                self.log_event(f"T{self.turn_count}: ETB trigger: {card.name}")
            
            # Kicker effect (Rule 702.32): fire after ETB
            if card.was_kicked and card.kicker_effect:
                kick_trigger = StackItem(
                    effect=card.kicker_effect,
                    source=card,
                    controller=card.controller,
                    description=f"Kicker: {card.name}"
                )
                self.stack.cards.append(kick_trigger)
                self.log_event(f"T{self.turn_count}: Kicker trigger: {card.name}")
                
        elif card.is_instant or card.is_sorcery:
            # Track life BEFORE effect resolves to detect changes
            life_0_before = self.players[0].life
            life_1_before = self.players[1].life
            if card.effect:
                try:
                    card.effect(self, card)
                except Exception as e:
                    logger.warning(f"Spell resolution error ({card.name}): {e}")
            
            # Flashback: exile instead of graveyard (Rule 702.33a)
            if card.from_graveyard:
                self.log_event(f"T{self.turn_count}: {card.name} exiled (flashback)")
                card.from_graveyard = False
                self.exile.add(card)
            else:
                (card.controller or self.active_player).graveyard.add(card)
            
            if self.players[0].life != life_0_before or self.players[1].life != life_1_before:
                self._last_life_change_turn = self.turn_count
            
            # Scry after spell resolution
            if card.scry_amount > 0:
                (card.controller or self.active_player).scry(card.scry_amount)
                self.log_event(f"T{self.turn_count}: {card.controller.name} scries {card.scry_amount}")
            
            # Kicker effect for instants/sorceries
            if card.was_kicked and card.kicker_effect:
                try:
                    card.kicker_effect(self, card)
                except Exception as e:
                    logger.warning(f"Kicker effect error ({card.name}): {e}")

        elif card.is_aura:
            # Aura resolution (Rule 303.4)
            target = getattr(card, 'enchant_target_ptr', None)
            if target and target in self.battlefield.cards and not target.has_hexproof and not target.is_protected_from(card):
                card.controller = card.controller or self.active_player
                self.battlefield.add(card)
                
                card.enchanted_to = target
                target.attachments.append(card)
                card.enchant_target_ptr = None # Clear pointer
                self.log_event(f"T{self.turn_count}: {card.name} enters attached to {target.name}")
            else:
                # Fizzle if target illegal/gone (Rule 608.3a) -> Graveyard
                (card.controller or self.active_player).graveyard.add(card)
                self.log_event(f"T{self.turn_count}: {card.name} fizzles (no legal target)")

        else:
            # Artifacts (non-Equipment/Aura), Enchantments (non-Aura), Planeswalkers
            card.controller = card.controller or self.active_player
            self.battlefield.add(card)
            
            # Planeswalker enters with starting loyalty (Rule 306.5b)
            if card.is_planeswalker and card.loyalty > 0:
                self.log_event(f"T{self.turn_count}: {card.name} enters with {card.loyalty} loyalty")
            
            # Enters-tapped
            if hasattr(card, 'enters_tapped') and card.enters_tapped:
                card.tapped = True
            
            # ETB for non-creatures too
            if card.etb_effect:
                trigger = StackItem(
                    effect=card.etb_effect,
                    source=card,
                    controller=card.controller,
                    description=f"ETB: {card.name}"
                )
                self.stack.cards.append(trigger)
                self.log_event(f"T{self.turn_count}: ETB trigger: {card.name}")

        self.check_state_based_actions()

    def resolve_stack(self):
        """Legacy method: resolve the entire stack (used for backward compat).
        New code uses _resolve_stack_top for one-at-a-time resolution."""
        while len(self.stack) > 0:
            self._resolve_stack_top()
            if self.game_over:
                return

    def _fire_death_trigger(self, card):
        """Fire death trigger if the card has one (Rule 700.4)."""
        if hasattr(card, 'death_effect') and card.death_effect:
            trigger = StackItem(
                effect=card.death_effect,
                source=card,
                controller=card.controller,
                description=f"Death: {card.name}"
            )
            self.stack.cards.append(trigger)
            self.log_event(f"T{self.turn_count}: Death trigger: {card.name}")

    def check_state_based_actions(self):
        """Check all state-based actions (Rule 704.5).
        Repeat until no more SBAs are performed."""
        changes = True
        while changes:
            changes = False
            
            # 704.5n: Equipment attached to an illegal object becomes unattached
            # 704.5m: Aura attached to an illegal object is put into graveyard
            for card in list(self.battlefield.cards):
                # Equipment cleanup
                if hasattr(card, 'equipped_to') and card.equipped_to:
                    creature = card.equipped_to
                    if creature not in self.battlefield.cards:
                        # Creature left battlefield -> unattach
                        card.equipped_to = None
                        if card in creature.attachments:
                            creature.attachments.remove(card)
                        changes = True
                        self.log_event(f"T{self.turn_count}: SBA: {card.name} unattached (creature gone)")
                    elif creature.is_protected_from(card):
                        # Protection -> unattach
                        card.equipped_to = None
                        if card in creature.attachments:
                            creature.attachments.remove(card)
                        changes = True
                        self.log_event(f"T{self.turn_count}: SBA: {card.name} unattached (protection)")
                
                # Aura cleanup
                if card.is_aura and card.enchanted_to:
                    permanent = card.enchanted_to
                    should_die = False
                    
                    if permanent not in self.battlefield.cards:
                        should_die = True
                    elif permanent.is_protected_from(card):
                        should_die = True
                    elif card.enchant_target_type == "creature" and not permanent.is_creature:
                        should_die = True
                        
                    if should_die:
                        self.battlefield.remove(card)
                        card.controller.graveyard.add(card)
                        changes = True
                        self.log_event(f"T{self.turn_count}: SBA: {card.name} (Aura) dies (illegal attachment)")

            # 704.5j: Planeswalker with 0 or less loyalty → graveyard
            for card in list(self.battlefield.cards):
                if card.is_planeswalker and card.loyalty <= 0:
                    self.battlefield.remove(card)
                    card.controller.graveyard.add(card)
                    self.log_event(f"T{self.turn_count}: SBA: {card.name} dies (loyalty={card.loyalty})")
                    changes = True
            
            # 704.5a: Player with 0 or less life loses
            for player in self.players:
                if player.life <= 0:
                    self.game_over = True
                    self.winner = self.players[(self.players.index(player) + 1) % 2]
                    self.log_event(f"RESULT: {player.name} defeated (life={player.life}). Winner: {self.winner.name} on turn {self.turn_count}")
                    return
            
            # Collect creatures to destroy
            to_destroy = []
            
            for card in list(self.battlefield.cards):
                if not card.is_creature:
                    continue
                
                toughness = card.toughness or 0
                
                # 704.5f: Creature with toughness 0 or less dies
                if toughness <= 0:
                    to_destroy.append(card)
                    continue
                
                # 704.5g: Creature with damage >= toughness is destroyed
                if card.damage_taken >= toughness:
                    to_destroy.append(card)
                    continue
                
                # 704.5h: Creature dealt deathtouch damage is destroyed
                if card.deathtouch_damaged:
                    to_destroy.append(card)
                    continue
            
            # Destroy all at once (Rule 704.3 — simultaneous)
            for card in to_destroy:
                if card not in self.battlefield.cards:
                    continue
                # 704.5f: 0 toughness kills even indestructible creatures
                # 704.5g/h: damage/deathtouch — indestructible prevents these
                toughness = card.toughness or 0
                is_zero_toughness = toughness <= 0
                if not is_zero_toughness and card.has_indestructible:
                    continue  # Indestructible prevents damage-based death, not 0-toughness
                self.battlefield.remove(card)
                if card.is_token:
                    # Tokens cease to exist when they leave the battlefield (Rule 111.7)
                    self.log_event(f"T{self.turn_count}: SBA: {card.name} (token) dies")
                else:
                    card.controller.graveyard.add(card)
                    self.log_event(f"T{self.turn_count}: SBA: {card.name} dies (dmg={card.damage_taken}, tou={card.toughness})")
                # Fire death trigger (Rule 700.4)
                self._fire_death_trigger(card)
                changes = True
            
            # 704.5j: Legend rule — if 2+ legendary permanents with same name
            # controlled by same player, keep newest (last added)
            legends = {}
            for card in self.battlefield.cards:
                if card.is_legendary:
                    key = (card.name, id(card.controller))
                    if key not in legends:
                        legends[key] = []
                    legends[key].append(card)
            
            for key, legend_list in legends.items():
                if len(legend_list) > 1:
                    # Keep the last one (most recently played), destroy others
                    for old_legend in legend_list[:-1]:
                        if old_legend in self.battlefield.cards:
                            self.battlefield.remove(old_legend)
                            if not old_legend.is_token:
                                old_legend.controller.graveyard.add(old_legend)
                            self.log_event(f"T{self.turn_count}: Legend rule: {old_legend.name} destroyed")
                            changes = True
        
        # After SBAs stabilize, apply static effects (Rule 613)
        self._apply_static_effects()
        
        # Death triggers are now on the stack — they'll be resolved via
        # the normal priority system (agents get to respond).
        # The game loop's dual-pass mechanism handles this.
