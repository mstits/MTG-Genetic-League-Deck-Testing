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
from .layers import LayerEngine
import logging
import re

logger = logging.getLogger(__name__)

class PendingCast:
    """State machine for casting a spell (CR 601.2).
    Tracks the progress of a spell from announcement to cost payment.
    """
    def __init__(self, card: Card, player: Player, from_zone: Zone):
        self.card = card
        self.player = player
        self.from_zone = from_zone
        
        # Casting phases: 'announce' -> 'choices' -> 'targeting' -> 'mana' -> 'pay'
        self.state = 'choices'
        
        # Step B: Choices (CR 601.2b)
        self.mode_index = None
        self.mode_desc = None
        self.x_value = 0
        self.is_kicked = False
        self.is_offspring = False
        self.is_flashback = False
        
        # Step C: Targeting (CR 601.2c)
        self.targets = []
        self.target_types = []
        
        # Step D+E: Costs (CR 601.2f)
        self.locked_cost = card.cost
        self.emerge_sacrifice = None
        self.sacrificed_creatures = []


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
            "Main 1",
            "Begin Combat",        # Rule 507 — priority before attackers
            "Declare Attackers",   # Rule 508 — priority after attackers declared
            "Declare Blockers",    # Rule 509 — priority after blockers declared
            "First Strike Damage", # Rule 510.4 — priority between FS and normal
            "Combat Damage",       # Rule 510 — priority after damage
            "End Combat",          # Rule 511 — priority, cleanup combat state
            "Main 2", "End", "Cleanup"
        ]
        self.current_phase = self.phases[0]
        self.stack = Zone("Stack")
        self.battlefield = Zone("Battlefield")
        self.exile = Zone("Exile")
        
        # Combat State
        self.combat_attackers = []
        self.combat_blockers = {}  # attacker_id -> [list of blocker Cards]
        self._attackers_step_done = False  # Track if active player had chance to declare
        self._blockers_step_done = False   # Track if defending player had chance to block
        
        self.game_over = False
        self.winner = None
        self.resolution_reason = None
        self.log = []
        self._action_count = 0
        
        # Priority tracking for recursive priority (Rule 117.3d)
        self._consecutive_passes = 0
        
        # Stall detection
        self._last_life_change_turn = 0
        
        # Synthetic Scenario Generator (SSG) monitoring
        self.ssg_strict_mode = False
        self._stack_loops = 0
        self._state_hashes = {}
        self.is_draw_loop = False
        
        # Replacement effects registry (Rule 614)
        # Each entry: {'type': 'damage'|'death'|'draw', 'source': Card, 'check': fn, 'apply': fn}
        self.replacement_effects = []
        
        # Register Rest in Peace as a proper replacement effect (Rule 614)
        # instead of monkey-patching graveyard.add — avoids circular refs 
        # and per-call battlefield scans when RIP isn't in play
        self._register_rest_in_peace_replacement()
        
        # Pending Cast state machine (CR 601.2)
        self.pending_cast = None
        
        # Rule 613 Layer Engine
        self.layer_engine = LayerEngine(self)
        
        # Game-level static effect tracking (populated by layer engine)
        self._active_cost_modifiers = []  # Cost modifier static effects
        self._active_restrictions = []    # Restriction/global static effects
        
        # Mulligan tracking for League Stats
        self.mulligan_counts = {p.name: 0 for p in self.players}

    def _fast_cow_clone(self) -> 'Game':
        """Copy-on-Write state cloning for <1ms MCTS performance."""
        import copy
        new_game = copy.copy(self)  # Shallow copy main structure
        
        # Deep copy only the arrays that mutate (Stack, Battlefield arrays)
        # Players are also shallow-copied at the object level, 
        # their zones and hand are copied by reference until modified by the engine
        new_game.players = []
        for p in self.players:
            new_p = copy.copy(p)
            new_p.mana_pool = copy.copy(p.mana_pool)
            
            # Zones
            new_p.hand = copy.copy(p.hand)
            new_p.hand.cards = list(p.hand.cards)
            
            new_p.library = copy.copy(p.library)
            new_p.library.cards = list(p.library.cards)
            
            new_p.graveyard = copy.copy(p.graveyard)
            new_p.graveyard.cards = list(p.graveyard.cards)
            
            new_game.players.append(new_p)
            
        # Battlefield and Stack
        new_game.battlefield = copy.copy(self.battlefield)
        new_game.battlefield.cards = [copy.copy(c) for c in self.battlefield.cards]
        
        new_game.stack = copy.copy(self.stack)
        new_game.stack.cards = list(self.stack.cards)
        
        # Re-link player instances in active/passing states (use indices, not properties)
        new_game.active_player_index = self.active_player_index
        if self.priority_player:
            new_game.priority_player_index = self.players.index(self.priority_player)
            
        # Re-link controllers on cards
        for card in new_game.battlefield.cards:
            if hasattr(card, 'controller') and card.controller in self.players:
                card.controller = new_game.players[self.players.index(card.controller)]
        
        # Clone replacement effects and re-link source references to cloned battlefield cards
        old_to_new = {id(old): new for old, new in zip(self.battlefield.cards, new_game.battlefield.cards)}
        new_game.replacement_effects = []
        for re_eff in self.replacement_effects:
            cloned_eff = dict(re_eff)
            src = re_eff.get('source')
            if src is not None and id(src) in old_to_new:
                cloned_eff['source'] = old_to_new[id(src)]
            new_game.replacement_effects.append(cloned_eff)
                
        return new_game

    def clone(self, determinize_for_player=None) -> 'Game':
        """Deep copy the entire game state for hypothetical evaluation.

        Args:
            determinize_for_player: If provided, implements Information Set MCTS 
                (Partial Observability) by shuffling the opponent's hand and library 
                together and redrawing. Also implements Ensemble Determinization biases.
        """
        import random
        
        # State-snapshot replacing slow deepcopy to massively increase MCTS depth
        new_game = self._fast_cow_clone()
        
        if determinize_for_player is not None:
            for p in new_game.players:
                if p.name != determinize_for_player.name:
                    # Ensemble Determinization: Bias hand sampling based on open mana
                    open_blue = p.mana_pool.get('U', 0)
                    bias_counters = open_blue >= 2
                    
                    # Collect all hidden cards
                    all_hidden = p.hand.cards + p.library.cards
                    
                    if bias_counters:
                        # Find counterspells and front-load them 
                        counters = [c for c in all_hidden if getattr(c, 'is_counter', False)]
                        others = [c for c in all_hidden if not getattr(c, 'is_counter', False)]
                        random.shuffle(others)
                        
                        # High percentage chance they actually have it if holding 2 blue
                        if random.random() < 0.75 and counters:
                            selected_counter = random.choice(counters)
                            counters.remove(selected_counter)
                            others.insert(0, selected_counter)
                            
                        all_hidden = others + counters
                    else:
                        random.shuffle(all_hidden)
                    
                    # Restore hand size
                    hand_size = len(p.hand.cards)
                    p.hand.cards = all_hidden[:hand_size]
                    p.library.cards = all_hidden[hand_size:]
                    
                    # Update zones
                    for c in p.hand.cards:
                        c.zone = p.hand
                    for c in p.library.cards:
                        c.zone = p.library
                        
        return new_game

    def apply_replacement(self, effect_type: str, **kwargs):
        """Apply replacement effects (Rule 614).
        
        Args:
            effect_type: 'damage', 'death', or 'draw'
            **kwargs: Context for the replacement
            
        Returns:
            Modified value (damage amount) or bool (whether replaced)
        """
        # Prune stale effects (source no longer on battlefield)
        self.replacement_effects = [
            r for r in self.replacement_effects 
            if r['source'] in self.battlefield.cards
        ]
        
        if effect_type == 'damage':
            amount = kwargs.get('amount', 0)
            target = kwargs.get('target')
            source = kwargs.get('source')
            for repl in self.replacement_effects:
                if repl['type'] == 'damage' and repl['check'](target, source, amount):
                    amount = repl['apply'](target, source, amount, self)
            return amount
        
        elif effect_type == 'death':
            card = kwargs.get('card')
            for repl in self.replacement_effects:
                if repl['type'] == 'death' and repl['check'](card):
                    repl['apply'](card, self)
                    return True  # Death was replaced
            return False  # Normal death processing
        
        elif effect_type == 'draw':
            player = kwargs.get('player')
            # Check registered replacement effects first
            for repl in self.replacement_effects:
                if repl['type'] == 'draw' and repl['check'](player):
                    repl['apply'](player, self)
                    return True  # Draw was replaced
            
            # Dredge (Rule 702.51): If a card with dredge N is in graveyard,
            # the player may mill N and return it instead of drawing.
            dredge_cards = [
                c for c in player.graveyard.cards 
                if getattr(c, 'has_dredge', False) and getattr(c, 'dredge_count', 0) > 0
            ]
            if dredge_cards and len(player.library.cards) > 0:
                # AI heuristic: Dredge the highest dredge_count card if library is large enough
                best = max(dredge_cards, key=lambda c: c.dredge_count)
                if len(player.library.cards) >= best.dredge_count:
                    # Mill N cards
                    for _ in range(best.dredge_count):
                        milled = player.library.draw()
                        if milled:
                            player.graveyard.add(milled)
                    # Return dredge card to hand
                    player.graveyard.remove(best)
                    player.hand.add(best)
                    self.log_event(f"T{self.turn_count}: {player.name} dredges {best.name} (milled {best.dredge_count})")
                    return True  # Draw was replaced by dredge
            
            return False  # Normal draw
        
        return None

    def register_replacement_effect(self, source, effect_type: str, check_fn, apply_fn):
        """Register a replacement effect from a permanent (Rule 614.1)."""
        self.replacement_effects.append({
            'type': effect_type,
            'source': source,
            'check': check_fn,
            'apply': apply_fn
        })

    def _register_rest_in_peace_replacement(self):
        """Register RIP as a replacement effect: cards that would go to graveyard go to exile instead.
        This replaces the old monkey-patch approach which created circular references."""
        def rip_check(event, game):
            """Check if Rest in Peace is on the battlefield."""
            return any(c.name == "Rest in Peace" for c in game.battlefield.cards)
        
        def rip_apply(event, game):
            """Redirect the card from graveyard to exile."""
            card = event.get('card')
            if card:
                game.exile.add(card)
                return True  # Event was replaced
            return False
        
        self.register_replacement_effect(
            source=None,  # Global effect — no specific source card
            effect_type='graveyard',
            check_fn=rip_check,
            apply_fn=rip_apply
        )

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

    def start_game(self) -> None:
        """Initialize the game: shuffle, draw 7, mulligan, set up turn 1.

        First player skips their draw step (Rule 103.8) and enters Main 1
        directly after Untap and Upkeep.
        """
        for player in self.players:
            player.shuffle_library()
            player.draw_card(7, game=self)
        
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
    
    def _check_mulligan(self, player: Player) -> None:
        """London Mulligan (Rule 103.4) with AI evaluation."""
        try:
            from agents.mulligan_ai import MulliganAI
            mulligan_ai = MulliganAI()
        except ImportError:
            mulligan_ai = None
            
        mulligan_count = 0
        while mulligan_count < 3:
            hand_cards = list(player.hand.cards)
            if not hand_cards:
                break
                
            # Use AI or fallback to basic land-count heuristic
            if mulligan_ai and getattr(player, 'use_mulligan_ai', True):
                meta = getattr(self, 'meta_archetype', 'Midrange')
                should_mull, explanation = mulligan_ai.should_mulligan(hand_cards, player.original_deck, mulligan_count, meta_archetype=meta)
            else:
                land_count = sum(1 for c in hand_cards if c.is_land)
                should_mull = (land_count <= 1 or land_count >= len(hand_cards))
                
            if not should_mull:
                break
                
            mulligan_count += 1
            self.log_event(f"T{self.turn_count}: {player.name} mulligans to {7 - mulligan_count}")
            
            # Mulligan: put hand back, shuffle, draw 7
            for c in hand_cards:
                player.hand.remove(c)
                player.library.add(c)
            player.shuffle_library()
            player.draw_card(7, game=self)
            
            # London Mulligan: Bottom N cards (intelligent card selection)
            new_hand = list(player.hand.cards)
            for _ in range(mulligan_count):
                if not new_hand:
                    break
                # Intelligent bottoming: evaluate which card is least useful
                # for the opening hand. Consider land balance, curve, and
                # immediate playability.
                lands = [c for c in new_hand if c.is_land]
                
                def bottom_priority(c):
                    """Higher = more likely to bottom. Keeps curve-relevant cards."""
                    cmc = Player._parse_cmc(c.cost) if c.cost else 0
                    score = 0
                    
                    # Excess lands: keep 2-3 lands, bottom extras
                    if c.is_land:
                        if len(lands) > 3:
                            score += 10  # Too many lands, bottom one
                        elif len(lands) > 2:
                            score += 3   # Slightly land-heavy
                        else:
                            score -= 20  # Need this land, don't bottom
                        return score
                    
                    # Uncastable early: bottom high-CMC spells when keeping
                    # a post-mull hand (need to curve out with fewer cards)
                    if cmc >= 5:
                        score += 8  # Very expensive, unlikely to cast early
                    elif cmc >= 4:
                        score += 4  # Borderline
                    elif cmc <= 2:
                        score -= 5  # Cheap spells are premium in mulled hands
                    
                    # Haste/ETB: keep for immediate impact
                    if getattr(c, 'has_haste', False):
                        score -= 3
                    if getattr(c, 'etb_effect', None):
                        score -= 2
                    
                    # Removal/interaction: always keep
                    if getattr(c, 'is_removal', False) or getattr(c, 'is_counter', False):
                        score -= 4
                    
                    return score
                
                new_hand.sort(key=bottom_priority, reverse=True)
                bottomed = new_hand.pop(0)  # Highest priority to bottom
                player.hand.remove(bottomed)
                player.library.cards.append(bottomed)  # Bottom of library
                    
        if mulligan_count > 0:
            self.mulligan_counts[player.name] = mulligan_count
            self.log_event(f"T{self.turn_count}: {player.name} kept a hand of {7 - mulligan_count}")
    
    def _log_turn_state(self):
        """Log board state at start of turn for strategic analysis."""
        p0, p1 = self.players[0], self.players[1]
        p0_creatures = [c for c in self.battlefield.cards if c.controller == p0 and c.is_creature]
        p1_creatures = [c for c in self.battlefield.cards if c.controller == p1 and c.is_creature]
        p0_lands = sum(1 for c in self.battlefield.cards if c.controller == p0 and c.is_land)
        p1_lands = sum(1 for c in self.battlefield.cards if c.controller == p1 and c.is_land)
        
        p0_score = p0.life + len(p0.hand.cards)*1.5 + sum((c.power or 0)*1.5 + (c.toughness or 0) for c in self.battlefield.cards if c.controller == p0)
        p1_score = p1.life + len(p1.hand.cards)*1.5 + sum((c.power or 0)*1.5 + (c.toughness or 0) for c in self.battlefield.cards if c.controller == p1)
        total = p0_score + p1_score
        wp = max(0.01, min(0.99, p0_score / total)) if total > 0 else 0.5
        
        self.log_event(f"--- T{self.turn_count} | {p0.name} ({p0.life}hp, {len(p0.hand)}cards, {p0_lands}lands) vs {p1.name} ({p1.life}hp, {len(p1.hand)}cards, {p1_lands}lands) [WP: {wp:.2f}] ---")
        
        if p0_creatures or p1_creatures:
            p0_board = ', '.join(f"{c.name} {c.power}/{c.toughness}" for c in p0_creatures) or 'empty'
            p1_board = ', '.join(f"{c.name} {c.power}/{c.toughness}" for c in p1_creatures) or 'empty'
            self.log_event(f"    Board: {p0.name} [{p0_board}] | {p1.name} [{p1_board}]")
        
        # Log hand contents for both players (visible in post-game replay)
        p0_hand = ', '.join(c.name for c in p0.hand.cards[:10]) or 'empty'
        p1_hand = ', '.join(c.name for c in p1.hand.cards[:10]) or 'empty'
        self.log_event(f"    [HAND {p0.name}: {p0_hand}]")
        self.log_event(f"    [HAND {p1.name}: {p1_hand}]")

    def _reset_all_mana_pools(self):
        """Empty all players' mana pools (Rule 106.4)."""
        for player in self.players:
            player.reset_mana_pool()

    def advance_phase(self) -> bool:
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
            
            # Creatures lose summoning sickness if they've been under this
            # player's control since the start of their most recent turn (Rule 302.6).
            # We mark them with a flag when they enter, and only clear it here
            # if they were already present at the start of this turn.
            for card in self.battlefield.cards:
                if card.controller == self.active_player:
                    if hasattr(card, '_controlled_since_turn_start') and card._controlled_since_turn_start:
                        card.summoning_sickness = False
                    # Mark all current creatures for next turn cycle
                    card._controlled_since_turn_start = True
            
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
            self.active_player.draw_card(1, game=self)
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === BEGIN COMBAT (Rule 507) ===
        # Both players get priority here — tap-down effects, combat triggers
        if self.current_phase == "Begin Combat":
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            return True  # Priority stop
        
        # === DECLARE ATTACKERS (Rule 508) ===
        # First entry: give active player priority to declare attacks
        # Second entry (after priority round): check if none declared and skip
        if self.current_phase == "Declare Attackers":
            if not self._attackers_step_done:
                # First time entering this phase — give priority for attack declaration
                self._attackers_step_done = True
                self.priority_player_index = self.active_player_index
                self._consecutive_passes = 0
                return True  # Priority stop — agent will declare attacks here
            
            # Second entry — attacks have been declared (or not)
            self._attackers_step_done = False  # Reset for next combat
            if not self.combat_attackers:
                # No attackers declared — skip rest of combat
                self.phase_index = self.phases.index("Main 2")
                self.current_phase = "Main 2"
                self.priority_player_index = self.active_player_index
                self._consecutive_passes = 0
                self.check_state_based_actions()
                return not self.game_over
            # Attackers were declared — priority for combat tricks
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            return True  # Priority stop — combat tricks go here
        
        # === DECLARE BLOCKERS (Rule 509) ===
        if self.current_phase == "Declare Blockers":
            if not self.combat_attackers:
                self.phase_index = self.phases.index("Main 2")
                self.current_phase = "Main 2"
                self.priority_player_index = self.active_player_index
                self._consecutive_passes = 0
                self.check_state_based_actions()
                return not self.game_over
            if not self._blockers_step_done:
                # First entry — give defending player priority to declare blocks
                self._blockers_step_done = True
                self.priority_player_index = (self.active_player_index + 1) % 2
                self._consecutive_passes = 0
                return True  # Priority stop — blocks and removal/tricks
            # Second entry — blocks declared, move on
            self._blockers_step_done = False
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            return True  # Priority stop after blocks (for tricks)
        
        # === FIRST STRIKE DAMAGE (Rule 510.4) ===
        if self.current_phase == "First Strike Damage":
            # Only resolve if there are first/double strikers
            first_strikers = [c for c in self.combat_attackers 
                             if c in self.battlefield.cards and (c.has_first_strike or c.has_double_strike)]
            blocker_fs = False
            for blockers_list in self.combat_blockers.values():
                for b in blockers_list:
                    if b in self.battlefield.cards and (b.has_first_strike or b.has_double_strike):
                        blocker_fs = True
                        break
            
            if first_strikers or blocker_fs:
                self._resolve_damage_for(first_strikers, self.defending_player, 
                                        self.active_player, is_first_strike=True)
                self.check_state_based_actions()
                if self.game_over:
                    return False
                # Priority after first strike damage — pump spells go here!
                self.priority_player_index = self.active_player_index
                self._consecutive_passes = 0
                return True  # Priority stop
            # No first strikers — skip to normal damage
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === COMBAT DAMAGE (Rule 510) ===
        if self.current_phase == "Combat Damage":
            # Resolve normal damage (non-first-strikers + double strikers)
            normal_attackers = [c for c in self.combat_attackers 
                              if c in self.battlefield.cards and 
                              (not c.has_first_strike or c.has_double_strike)]
            self._resolve_damage_for(normal_attackers, self.defending_player,
                                    self.active_player, is_first_strike=False)
            self.check_state_based_actions()
            if self.game_over:
                return False
            # Priority after combat damage (Rule 510.4) — finish combat
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            return True  # Priority stop
        
        # === END COMBAT (Rule 511) ===
        if self.current_phase == "End Combat":
            # "Until end of combat" effects end, cleanup combat state
            self.combat_attackers = []
            self.combat_blockers = {}
            self._attackers_step_done = False
            self._blockers_step_done = False
            # Life change tracking
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            # Just advance — no priority stop here usually
            self.phase_index += 1
            self.current_phase = self.phases[self.phase_index]
        
        # === END STEP (Rule 513) ===
        if self.current_phase == "End":
            # Players get priority in end step ("at end of turn" triggers)
            self.priority_player_index = self.active_player_index
            self._consecutive_passes = 0
            return True  # Priority stop — important for end-step plays!
        
        # === CLEANUP STEP (Rule 514) ===
        if self.current_phase == "Cleanup":
            self._do_cleanup()
            self.phase_index += 1  # Will wrap to next turn on next call
            if self.phase_index < len(self.phases):
                self.current_phase = self.phases[self.phase_index]
        
        # Default priority to active player
        if self.current_phase not in ("Declare Blockers",):
            self.priority_player_index = self.active_player_index
        
        # Reset priority pass counter for the new phase
        self._consecutive_passes = 0
        
        self.check_state_based_actions()
        return not self.game_over

    def _do_untap(self) -> None:
        """Untap all permanents controlled by the active player (Rule 502.3)."""
        for card in self.battlefield.cards:
            if card.controller == self.active_player:
                card.tapped = False

    def _fire_upkeep_triggers(self) -> None:
        """Fire 'at the beginning of your upkeep' triggers (Rule 503.1).
        Triggers go on the stack so opponents can respond."""
        active = self.active_player
        
        # === STALL BREAKER ===
        # If no life totals have changed for 15+ turns, drain both players
        # to force a conclusion. Escalates every 3 extra turns.
        stalled_turns = self.turn_count - getattr(self, '_last_life_change_turn', self.turn_count)
        if stalled_turns >= 15:
            drain = 1 + (stalled_turns - 15) // 3
            for player in self.players:
                player.life -= drain
            self._last_life_change_turn = self.turn_count  # Reset to prevent double-drain
            self.log_event(f"T{self.turn_count}: STALL BREAKER — {stalled_turns} turns stalled, draining {drain} from all players")
        
        # === SUSPEND (Rule 702.61) ===
        # Remove a time counter from each suspended card. If last counter removed, cast it.
        for card in list(active.exile.cards):
            if getattr(card, '_suspend_counters', 0) > 0:
                card._suspend_counters -= 1
                self.log_event(f"T{self.turn_count}: Suspend: {card.name} — {card._suspend_counters} time counters remaining")
                if card._suspend_counters == 0:
                    # Cast for free (Rule 702.61a)
                    active.exile.remove(card)
                    card.controller = active
                    card.from_graveyard = False
                    self.stack.add(card)
                    self.log_event(f"T{self.turn_count}: Suspend: {card.name} is cast (0 time counters)!")
        
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
            # Enchantment triggers fire each upkeep too
            if card.controller == active and getattr(card, 'enchantment_trigger', None):
                trigger = StackItem(
                    effect=card.enchantment_trigger,
                    source=card,
                    controller=active,
                    description=f"Enchantment trigger: {card.name}"
                )
                self.stack.cards.append(trigger)
                self.log_event(f"T{self.turn_count}: Enchantment trigger: {card.name}")
        
        # If triggers were added, resolve them immediately (simplified —
        # full implementation would give priority to both players)
        # REMOVED: auto-resolve loop. Triggers stay on stack for agent interaction.
        # The game loop's dual-pass priority system handles resolution.
        # while len(self.stack) > 0:
        #     self._resolve_stack_top()
        #     self.check_state_based_actions()
        #     if self.game_over:
        #         return

    def _do_cleanup(self) -> None:
        """Cleanup step (Rule 514):
        1. Active player discards to max hand size (7)
        2. Remove all damage from permanents
        3. End 'until end of turn' effects
        """
        # 514.1: Discard to hand size — intelligent card selection
        player = self.active_player
        lands_in_play = sum(1 for c in self.battlefield.cards 
                          if c.controller == player and c.is_land)
        
        while len(player.hand) > 7:
            cards = list(player.hand.cards)
            
            def discard_priority(c):
                """Higher = more likely to discard. Keeps high-impact cards."""
                score = 0
                
                # Excess lands: discard if we already have plenty
                if c.is_land:
                    hand_lands = sum(1 for x in cards if x.is_land)
                    if lands_in_play >= 5 and hand_lands > 1:
                        score += 10  # Flooded, discard extra lands
                    elif lands_in_play >= 3 and hand_lands > 2:
                        score += 5   # Enough lands, discard extras
                    else:
                        score -= 5   # Still need lands, keep
                    return score
                
                cmc = Player._parse_cmc(c.cost) if c.cost else 0
                
                # Uncastable cards: discard high-CMC spells we can't play soon
                if cmc > lands_in_play + 2:
                    score += 6  # Way too expensive
                elif cmc > lands_in_play + 1:
                    score += 3  # Marginally too expensive
                
                # Keep removal and interaction
                if getattr(c, 'is_removal', False) or getattr(c, 'is_counter', False):
                    score -= 5
                if getattr(c, 'is_board_wipe', False):
                    score -= 8  # Board wipes are premium
                
                # Keep burn (reach for lethal)
                if getattr(c, 'is_burn', False):
                    score -= 3
                
                # Creature value: power + toughness + keywords
                if c.is_creature:
                    score -= (c.power or 0) + (c.toughness or 0) * 0.3
                    if c.has_flying or c.has_trample: score -= 1
                    if c.etb_effect: score -= 1
                else:
                    # Non-creature spells: base value from CMC
                    score -= cmc * 0.5
                
                return score
            
            worst = max(cards, key=discard_priority)
            player.hand.remove(worst)
            # Madness check (Rule 702.34): if discarded card has madness, cast it
            if getattr(worst, 'madness_cost', '') and player.can_pay_cost(worst.madness_cost, self):
                player.pay_cost(worst.madness_cost, self)
                worst.controller = player
                worst.from_graveyard = False
                self.stack.add(worst)
                self.log_event(f"T{self.turn_count}: Madness! {player.name} casts {worst.name} for {worst.madness_cost} (discarded)")
            else:
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
        """Apply static/continuous effects from permanents on the battlefield (CR 613).
        This delegates entirely to the Rule 613 LayerEngine."""
        effect_sources = [c for c in self.battlefield.cards if hasattr(c, 'static_effect') and c.static_effect]
        tracked_auras = [c for c in self.battlefield.cards if (c.is_aura and hasattr(c, 'enchant_target_ptr')) or (c.is_equipment and hasattr(c, 'equipped_to'))]
        self.layer_engine.apply_layers(effect_sources, tracked_auras)

    def _static_effect_applies(self, source, target, se: dict) -> bool:
        """Helper to determine if a static effect applies to a target based on the filter."""
        effect_filter = se.get('filter', '')
        if effect_filter == 'other_creatures':
            return target.controller == source.controller and target is not source
        elif effect_filter == 'all_creatures':
            return target.controller == source.controller
        elif effect_filter == 'type':
            req_type = se.get('type', '')
            return target.controller == source.controller and target is not source and req_type in target.creature_types
        return False

    def get_legal_actions(self) -> List[dict]:
        if self.game_over:
            return [{'type': 'pass'}]

        player = self.priority_player
        actions = [{'type': 'pass'}]

        # --- CR 601.2 CASTING STATE MACHINE ---
        if self.pending_cast:
            pc = self.pending_cast
            pc_actions = []
            
            if pc.state == 'choices':
                # Step B: Choices
                pc_actions.append({'type': 'done_choices'})
                
                # Modes
                if pc.card.is_modal and pc.mode_index is None:
                    for mode in pc.card.modal_modes:
                        pc_actions.append({'type': 'choose_mode', 'index': mode['index'], 'desc': mode['description']})
                # X value
                if pc.card.has_x_cost and pc.x_value == 0:
                    for x in range(1, min(10, player.available_mana(self) + 1)):
                        pc_actions.append({'type': 'choose_x', 'value': x})
                # Kicker
                if getattr(pc.card, 'kicker_cost', None) and not pc.is_kicked:
                    pc_actions.append({'type': 'choose_kicker'})
                # Offspring
                if getattr(pc.card, 'offspring_cost', None) and not pc.is_offspring:
                    pc_actions.append({'type': 'choose_offspring'})
                # Emerge
                if getattr(pc.card, 'emerge_cost', None) and not pc.emerge_sacrifice:
                    own_creatures = [c for c in self.battlefield.cards if c.controller == player and c.is_creature]
                    for sac in own_creatures:
                        pc_actions.append({'type': 'choose_emerge', 'target': sac})
                # Sacrifice as cost
                if getattr(pc.card, 'requires_creature_sacrifice', False) and not pc.sacrificed_creatures:
                    own_creatures = [c for c in self.battlefield.cards if c.controller == player and c.is_creature]
                    for sac in own_creatures:
                        pc_actions.append({'type': 'choose_sacrifice', 'target': sac})
                        
                return pc_actions
                
            elif pc.state == 'targeting':
                # Step C: Targeting
                pc_actions.append({'type': 'done_targeting'})
                card = pc.card
                
                # Auras
                if card.is_aura:
                    valid = [c for c in self.battlefield.cards if c.is_creature and not c.has_hexproof and not c.is_protected_from(card)]
                    for t in valid:
                        if t not in pc.targets:
                            pc_actions.append({'type': 'declare_target', 'target': t, 'target_type': 'aura'})
                # Removal/Burn
                elif card.is_burn or card.is_removal or card.is_bounce:
                    if card.effect:
                        valid = [c for c in self.battlefield.cards if c.controller != player and c.is_creature and not c.has_hexproof and not c.is_protected_from(card)]
                        for t in valid:
                            if t not in pc.targets:
                                pc_actions.append({'type': 'declare_target', 'target': t, 'target_type': 'creature'})
                        if card.is_burn:
                            opp = self.opponent if hasattr(self, 'opponent') else self.players[(self.players.index(player) + 1) % 2]
                            if opp not in pc.targets:
                                pc_actions.append({'type': 'declare_target', 'target': opp, 'target_type': 'player'})
                # Buff
                elif card.is_buff and card.effect:
                    valid = [c for c in self.battlefield.cards if c.controller == player and c.is_creature]
                    for t in valid:
                        if t not in pc.targets:
                            pc_actions.append({'type': 'declare_target', 'target': t, 'target_type': 'creature'})
                # Counter
                elif card.is_counter and card.effect:
                    stack_items = [s for s in self.stack.cards if s != pc.card]
                    for s in stack_items:
                        if s not in pc.targets:
                            pc_actions.append({'type': 'declare_target', 'target': s, 'target_type': 'spell'})
                
                return pc_actions
                
            elif pc.state == 'mana':
                # Step D: Mana
                for land in self.battlefield.cards:
                    if land.controller == player and land.is_land and not land.tapped:
                        for color in player._land_produces(land):
                            pc_actions.append({'type': 'activate_mana', 'card': land, 'color': color})
                
                # Only offer pay if pool is sufficient
                req = player._parse_mana_requirements(pc.locked_cost)
                if not req or player._check_requirements(req, player.mana_pool):
                    pc_actions.append({'type': 'pay_costs'})
                    
                pc_actions.append({'type': 'cancel_cast'})
                return pc_actions

        # --- NORMAL ACTIONS (No pending cast) ---
        
        # Play Land
        if (self.current_phase in ("Main 1", "Main 2") and 
            player == self.active_player and 
            player.lands_played_this_turn < player.max_lands_per_turn):
            seen_land_names = set()
            for card in player.hand.cards:
                if card.is_land and card.name not in seen_land_names:
                    actions.append({'type': 'play_land', 'card': card})
                    seen_land_names.add(card.name)

        # Mana abilities (CR 605) can be activated anytime player has priority
        for land in self.battlefield.cards:
            if land.controller == player and land.is_land and not land.tapped:
                for color in player._land_produces(land):
                    actions.append({'type': 'activate_mana', 'card': land, 'color': color})

        # Cast Spells (CR 601.2a - Announcement)
        is_main = (self.current_phase in ("Main 1", "Main 2") and 
                   player == self.active_player)

        # Hand casting
        # Pre-compute total available mana (pool + untapped lands)
        available_mana = dict(player.mana_pool)
        for land in self.battlefield.cards:
            if land.controller == player and land.is_land and not land.tapped:
                for color in player._land_produces(land):
                    available_mana[color] = available_mana.get(color, 0) + 1
        
        for card in player.hand.cards:
            if card.is_land:
                continue
            # CR 202.1a: Cards with no mana cost cannot be cast from hand
            # (meld back-faces, suspend-only spells, etc.)
            if not card.cost:
                continue
            can_cast = False
            if card.is_instant or card.has_flash:
                can_cast = True
            elif is_main:
                can_cast = True
            
            # Check mana affordability before offering (prevents cast-cancel loops)
            if can_cast:
                req = player._parse_mana_requirements(card.cost)
                if req and not player._check_requirements(req, available_mana):
                    can_cast = False
            
            if can_cast:
                actions.append({'type': 'announce_cast', 'card': card, 'from_zone': 'Hand'})
                
        # Flashback casting (Rule 702.33)
        for card in player.graveyard.cards:
            if not getattr(card, 'flashback_cost', None):
                continue
            can_cast = False
            if card.is_instant or card.has_flash:
                can_cast = True
            elif is_main:
                can_cast = True
            
            if can_cast:
                actions.append({'type': 'announce_cast', 'card': card, 'from_zone': 'Graveyard'})
        
        # Unearth casting from graveyard (Rule 702.83)
        if is_main:
            for card in player.graveyard.cards:
                if not getattr(card, 'unearth_cost', '') or not card.is_creature:
                    continue
                if player.can_pay_cost(card.unearth_cost, self):
                    actions.append({
                        'type': 'announce_cast', 'card': card, 
                        'from_zone': 'Graveyard', 'is_unearth': True
                    })
        
        # Evoke casting from hand (Rule 702.73) — cheaper alt cost, sacrifice on ETB
        if is_main:
            for card in player.hand.cards:
                if not getattr(card, 'evoke_cost', '') or not card.is_creature:
                    continue
                if player.can_pay_cost(card.evoke_cost, self):
                    actions.append({
                        'type': 'announce_cast', 'card': card,
                        'from_zone': 'Hand', 'is_evoke': True
                    })
        
        # Cycling: discard from hand to draw (Rule 702.28, any time)
        for card in player.hand.cards:
            if card.cycling_cost and player.can_pay_cost(card.cycling_cost, self):
                actions.append({'type': 'cycle', 'card': card})
        
        # Suspend from hand (Rule 702.61): exile with time counters
        if is_main:
            for card in player.hand.cards:
                if not getattr(card, 'has_suspend', False):
                    continue
                # Suspend cards can be exiled from hand for free (they "suspend" themselves)
                # Only if they can't be cast normally OR player chooses to
                suspend_match = re.search(r'suspend (\d+)', card.oracle_text.lower()) if card.oracle_text else None
                if suspend_match:
                    time_counters = int(suspend_match.group(1))
                    actions.append({
                        'type': 'suspend_card', 'card': card,
                        'time_counters': time_counters
                    })
        
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
                    
                    # Mana cost check
                    if ability.get('cost_mana') and not player.can_pay_cost(ability['cost_mana'], self):
                        can_activate = False
                        
                    # Class Leveling checks (Rule 702.146 — sorcery speed only, sequential levels)
                    if ability.get('is_class_level'):
                        if not is_main or player != self.active_player or len(self.stack) > 0:
                            can_activate = False
                        if card.class_level + 1 != ability.get('level_target'):
                            can_activate = False
                    
                    if can_activate:
                        actions.append({
                            'type': 'activate_ability',
                            'card': card,
                            'ability_index': i,
                            'ability': ability
                        })
        
        # Tap Abilities (parsed {T}: effects)
        for card in self.battlefield.cards:
            if card.controller != player:
                continue
            if not getattr(card, 'tap_ability_effect', None):
                continue
            if card.tapped or (card.is_creature and card.summoning_sickness):
                continue
            actions.append({
                'type': 'tap_ability',
                'card': card
            })
        
        # Sacrifice Abilities (parsed sacrifice effects)
        for card in self.battlefield.cards:
            if card.controller != player:
                continue
            if not getattr(card, 'sacrifice_effect', None):
                continue
            if card.is_creature and card.summoning_sickness:
                continue
            actions.append({
                'type': 'sacrifice_ability',
                'card': card
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
        
        # Unblockable (Rule 700 — "can't be blocked")
        if getattr(attacker, 'is_unblockable', False):
            return False
        
        # Flying (Rule 702.9b)
        if attacker.has_flying and not blocker.can_block_flyer:
            return False
        
        # Shadow (Rule 702.27) — shadow can only be blocked by shadow
        if getattr(attacker, 'has_shadow', False) and not getattr(blocker, 'has_shadow', False):
            return False
        if getattr(blocker, 'has_shadow', False) and not getattr(attacker, 'has_shadow', False):
            return False  # Non-shadow can't block shadow
        
        # Fear (Rule 702.35) — can only be blocked by artifact or black creatures
        if getattr(attacker, 'has_fear', False):
            is_artifact = 'Artifact' in blocker.type_line
            is_black = '{B}' in blocker.cost if blocker.cost else False
            if not is_artifact and not is_black:
                return False
        
        # Intimidate (Rule 702.13) — can only be blocked by artifact or same-color creatures
        if getattr(attacker, 'has_intimidate', False):
            is_artifact = 'Artifact' in blocker.type_line
            shares_color = False
            for mana in ['{W}', '{U}', '{B}', '{R}', '{G}']:
                if mana in (attacker.cost or '') and mana in (blocker.cost or ''):
                    shares_color = True
                    break
            if not is_artifact and not shares_color:
                return False
        
        # Skulk (Rule 702.119) — can't be blocked by creature with greater power
        if getattr(attacker, 'has_skulk', False):
            if (blocker.power or 0) > (attacker.power or 0):
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

    def _get_card_by_id(self, card_id: int):
        for p in self.players:
            for c in p.hand.cards:
                if c.id == card_id: return c
            for c in p.graveyard.cards:
                if c.id == card_id: return c
            for c in p.library.cards:
                if c.id == card_id: return c
        for c in self.battlefield.cards:
            if c.id == card_id: return c
        for item in self.stack.cards:
            if getattr(item, 'id', None) == card_id: return item
        return None

    def _resolve_action_references(self, action: dict) -> dict:
        if action['type'] == 'pass': return action
        remapped = dict(action)
        def remap_card(c):
            if hasattr(c, 'id'):
                match = self._get_card_by_id(c.id)
                return match if match else c
            return c
            
        if 'card' in remapped: remapped['card'] = remap_card(remapped['card'])
        if 'vehicle' in remapped: remapped['vehicle'] = remap_card(remapped['vehicle'])
        if 'source' in remapped: remapped['source'] = remap_card(remapped['source'])
        if 'target' in remapped and hasattr(remapped['target'], 'id'):
            remapped['target'] = remap_card(remapped['target'])
        if 'candidates' in remapped:
            remapped['candidates'] = [remap_card(c) for c in remapped['candidates']]
        if 'attackers' in remapped:
            remapped['attackers'] = [remap_card(c) for c in remapped['attackers']]
        if 'blocks' in remapped:
            new_blocks = {}
            for attacker, blockers in remapped['blocks'].items():
                new_blocks[remap_card(attacker)] = [remap_card(b) for b in blockers]
            remapped['blocks'] = new_blocks
        return remapped

    def _hash_exec_state(self):
        """Creates a deterministic hash of the current board, stack, life, and mana layout."""
        state = []
        for p in self.players:
            state.append(f"{p.life}|m:{','.join(f'{k}{v}' for k, v in p.mana_pool.items())}")
        state.append(f"s:{len(self.stack)}")
        state.append(f"b:{','.join(str(c.id) for c in self.battlefield.cards)}")
        return hash('|'.join(state))

    def apply_action(self, action: dict):
        """Execute a player action and update game state.

        Handles all action types: pass (with priority/stack resolution),
        play_land, cast_spell, cycle, declare_attackers, declare_blockers,
        crew, equip, loyalty_ability, activate_ability.

        After casting a spell, priority passes to the opponent (Rule 601).
        """
        # --- Broken-Loop Detection (CR 104.4b) ---
        state_hash = self._hash_exec_state()
        self._state_hashes[state_hash] = self._state_hashes.get(state_hash, 0) + 1
        if self._state_hashes[state_hash] > 30:
            # Infinite loop detected — use mercy rule tie-breaking
            self.game_over = True
            self.is_draw_loop = True
            # Mercy rule: player with more life/board wins
            p1, p2 = self.players
            s1 = sum(1 for c in self.battlefield.cards if c.controller == p1 and c.is_creature) * 5 + p1.life
            s2 = sum(1 for c in self.battlefield.cards if c.controller == p2 and c.is_creature) * 5 + p2.life
            if s1 > s2:
                self.winner = p1
            elif s2 > s1:
                self.winner = p2
            else:
                self.winner = None  # Truly tied
            if self.winner:
                self.log_event(f"RESULT: {self.winner.name} wins (loop broken by board advantage). Turn {self.turn_count}")
            else:
                self.log_event(f"RESULT: Draw (Broken-Loop, tied board). Turn {self.turn_count}")
            return

        action = self._resolve_action_references(action)
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
                self._state_hashes.clear()  # Reset loop detector on phase change
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
            # Madness check on cycling discard (Rule 702.34)
            if getattr(card, 'madness_cost', '') and player.can_pay_cost(card.madness_cost, self):
                player.pay_cost(card.madness_cost, self)
                card.controller = player
                card.from_graveyard = False
                self.stack.add(card)
                self.log_event(f"T{self.turn_count}: {player.name} cycles {card.name}, then casts via Madness for {card.madness_cost}")
            else:
                player.graveyard.add(card)
                self.log_event(f"T{self.turn_count}: {player.name} cycles {card.name} (pay {card.cycling_cost})")
            player.draw_card(1, game=self)
        
        elif action['type'] == 'suspend_card':
            card = action['card']
            time_counters = action.get('time_counters', 3)
            self._consecutive_passes = 0
            player.hand.remove(card)
            card.controller = player
            card._suspend_counters = time_counters
            player.exile.add(card)
            self.log_event(f"T{self.turn_count}: {player.name} suspends {card.name} ({time_counters} time counters)")
        
        elif action['type'] == 'cast_spell':
            # BACKWARD COMPATIBILITY: Legacy cast_spell runs the full Rule 601.2
            # sequence atomically (announce → auto-target → pay → resolve).
            # New code should use announce_cast → done_choices → declare_target → done_targeting.
            card = action['card']
            self._consecutive_passes = 0
            from_zone = 'Hand'
            if action.get('from_graveyard'):
                from_zone = 'Graveyard'
                card.from_graveyard = True
            
            cost = action.get('cost_override', card.cost)
            
            # Step 1: Move from zone
            if from_zone == 'Hand' and card in player.hand.cards:
                player.hand.remove(card)
            elif from_zone == 'Graveyard' and card in player.graveyard.cards:
                player.graveyard.remove(card)
            
            # Step 2: Pay cost (best-effort for legacy tests)
            if cost:
                try:
                    player.pay_cost(cost, self)
                except Exception:
                    pass  # Legacy tests may not set up proper mana
            
            # Step 3: Auto-target using heuristic
            card.controller = player
            opp = self.players[(self.players.index(player) + 1) % 2]
            if card.is_burn or card.is_removal or card.is_bounce:
                # Target opponent's creature if available, else opponent
                opp_creatures = [c for c in self.battlefield.cards 
                                if c.controller == opp and c.is_creature]
                if opp_creatures:
                    card.spell_target = max(opp_creatures, key=lambda c: (c.power or 0))
                elif card.is_burn:
                    card.spell_target = opp
            elif card.is_buff:
                own_creatures = [c for c in self.battlefield.cards 
                                if c.controller == player and c.is_creature]
                if own_creatures:
                    card.spell_target = max(own_creatures, key=lambda c: (c.power or 0))
            elif card.is_counter:
                stack_items = [s for s in self.stack.cards]
                if stack_items:
                    card.spell_target = stack_items[-1]
            
            # Step 4: Put card directly on stack (spells are Cards, not StackItems)
            # Handle kicked flag
            if action.get('kicked'):
                card.was_kicked = True
            self.stack.cards.append(card)
            self.log_event(f"T{self.turn_count}: {player.name} casts {card.name} (legacy)")
            
            # Prowess triggers (Rule 702.107): noncreature spell fires prowess
            if not card.is_creature:
                for perm in self.battlefield.cards:
                    if perm.controller == player and perm.is_creature and getattr(perm, 'has_prowess', False):
                        perm._temp_modifiers = getattr(perm, '_temp_modifiers', [])
                        perm._temp_modifiers.append({'power': 1, 'toughness': 1, 'until': 'end_of_turn'})
                        self.log_event(f"T{self.turn_count}: Prowess: {perm.name} gets +1/+1")
            
            # Cast triggers: "whenever you cast a spell"
            for perm in self.battlefield.cards:
                if perm.controller == player and perm.is_creature and getattr(perm, 'cast_trigger', None):
                    try:
                        perm.cast_trigger(self, perm)
                    except Exception:
                        pass
        
        elif action['type'] == 'announce_cast':
            card = action['card']
            from_zone = action.get('from_zone', 'Hand')
            self._consecutive_passes = 0
            self.pending_cast = PendingCast(card, player, from_zone)
            if from_zone == 'Graveyard':
                self.pending_cast.is_flashback = True
            
            # Evoke (Rule 702.73): use evoke cost, flag for ETB sacrifice
            if action.get('is_evoke') and getattr(card, 'evoke_cost', ''):
                self.pending_cast.locked_cost = card.evoke_cost
                card._was_evoked = True
                self.log_event(f"T{self.turn_count}: {player.name} announces {card.name} (evoke)")
            # Unearth (Rule 702.83): use unearth cost from graveyard
            elif action.get('is_unearth') and getattr(card, 'unearth_cost', ''):
                self.pending_cast.locked_cost = card.unearth_cost
                card._was_unearthed = True
                self.log_event(f"T{self.turn_count}: {player.name} announces {card.name} (unearth)")
            else:
                self.log_event(f"T{self.turn_count}: {player.name} announces {card.name}")

        elif action['type'] in ('choose_mode', 'choose_x', 'choose_kicker', 'choose_offspring', 'choose_emerge', 'choose_sacrifice'):
            pc = self.pending_cast
            if action['type'] == 'choose_mode':
                pc.mode_index = action['index']
                pc.mode_desc = action['desc']
            elif action['type'] == 'choose_x':
                pc.x_value = action['value']
            elif action['type'] == 'choose_kicker':
                pc.is_kicked = True
                pc.locked_cost += pc.card.kicker_cost
            elif action['type'] == 'choose_offspring':
                pc.is_offspring = True
                pc.locked_cost += pc.card.offspring_cost
            elif action['type'] == 'choose_emerge':
                pc.emerge_sacrifice = action['target']
                sac_cmc = Player._parse_cmc(action['target'].cost) if action['target'].cost else 0
                emerge_cmc = Player._parse_cmc(pc.card.emerge_cost) if pc.card.emerge_cost else 0
                reduced = max(0, emerge_cmc - sac_cmc)
                pc.locked_cost = "{" + str(reduced) + "}"
            elif action['type'] == 'choose_sacrifice':
                pc.sacrificed_creatures.append(action['target'])

        elif action['type'] == 'done_choices':
            pc = self.pending_cast
            effective_cost = pc.locked_cost
            if getattr(pc.card, 'has_delve', False) and len(player.graveyard) > 0:
                import re as _re2
                generic_match = _re2.search(r'\{(\d+)\}', effective_cost)
                if generic_match:
                    generic = int(generic_match.group(1))
                    reduction = min(generic, len(player.graveyard))
                    new_generic = generic - reduction
                    effective_cost = effective_cost.replace('{' + str(generic) + '}', '{' + str(new_generic) + '}', 1)
            
            if getattr(pc.card, 'has_affinity', False) and getattr(pc.card, 'affinity_type', None) == 'artifacts':
                import re as _re3
                artifact_count = sum(1 for c in self.battlefield.cards if c.controller == player and 'Artifact' in c.type_line)
                generic_match = _re3.search(r'\{(\d+)\}', effective_cost)
                if generic_match and artifact_count > 0:
                    generic = int(generic_match.group(1))
                    new_generic = max(0, generic - artifact_count)
                    effective_cost = effective_cost.replace('{' + str(generic) + '}', '{' + str(new_generic) + '}', 1)
            
            if getattr(pc.card, 'has_convoke', False):
                import re as _re4
                untapped_creatures = sum(1 for c in self.battlefield.cards if c.controller == player and c.is_creature and not c.tapped)
                generic_match = _re4.search(r'\{(\d+)\}', effective_cost)
                if generic_match and untapped_creatures > 0:
                    generic = int(generic_match.group(1))
                    new_generic = max(0, generic - untapped_creatures)
                    effective_cost = effective_cost.replace('{' + str(generic) + '}', '{' + str(new_generic) + '}', 1)
            
            if getattr(pc.card, 'requires_creature_sacrifice', False):
                for sac in pc.sacrificed_creatures:
                    if sac in self.battlefield.cards:
                        self.battlefield.remove(sac)
                        sac.controller.graveyard.add(sac)
                        self._fire_death_trigger(sac)

            pc.locked_cost = effective_cost
            pc.state = 'targeting'

        elif action['type'] == 'declare_target':
            self.pending_cast.targets.append(action['target'])
            self.pending_cast.target_types.append(action['target_type'])

        elif action['type'] == 'done_targeting':
            self.pending_cast.state = 'mana'

        elif action['type'] == 'activate_mana':
            card = action['card']
            color = action['color']
            card.tapped = True
            player.mana_pool[color] += 1
            self.log_event(f"  {player.name} taps {card.name} for {color}")

        elif action['type'] == 'cancel_cast':
            self.pending_cast = None
            self.log_event(f"  {player.name} cancels casting.")

        elif action['type'] == 'pay_costs':
            pc = self.pending_cast
            card = pc.card
            
            success = player.drain_pool_for_cost(pc.locked_cost)
            if not success:
                self.log_event(f"  Failed to pay {pc.locked_cost}. Cancelling.")
                self.pending_cast = None
                return
                
            if pc.emerge_sacrifice and pc.emerge_sacrifice in self.battlefield.cards:
                sac = pc.emerge_sacrifice
                self.battlefield.remove(sac)
                sac.controller.graveyard.add(sac)
                self._fire_death_trigger(sac)
                self.log_event(f"  Emerge: sacrificed {sac.name}")

            if pc.is_kicked: card.was_kicked = True
            if pc.is_offspring: card.was_offspring_paid = True
            if pc.x_value > 0: card.x_value = pc.x_value
            if pc.mode_index is not None:
                if pc.mode_index < len(card.modal_modes):
                    card.effect = card.modal_modes[pc.mode_index]['effect']
                    card._chosen_mode_desc = pc.mode_desc
                else:
                    # Mode index out of range — use first available or default effect
                    if card.modal_modes:
                        card.effect = card.modal_modes[0]['effect']
                        card._chosen_mode_desc = card.modal_modes[0].get('desc', '')
                    self.log_event(f"  Warning: mode {pc.mode_index} out of range for {card.name} (has {len(card.modal_modes)} modes)")

            kick_label = ' (kicked)' if pc.is_kicked else ''
            os_label = ' (offspring)' if pc.is_offspring else ''
            flash_label = ' (flashback)' if pc.is_flashback else ''
            self.log_event(f"T{self.turn_count}: {player.name} casts {card.name} ({pc.locked_cost}){flash_label}{kick_label}{os_label}")

            if pc.from_zone == 'Graveyard':
                player.graveyard.remove(card)
                card.from_graveyard = True
            else:
                player.hand.remove(card)

            card.controller = player
            
            if card.is_aura and pc.targets:
                card.enchant_target_ptr = pc.targets[0]
                
            if pc.targets:
                card.spell_target = pc.targets[0]
                card.spell_target_type = pc.target_types[0]
                self.log_event(f"  → targeting: {card.spell_target.name if hasattr(card.spell_target, 'name') else card.spell_target}")

            self.stack.add(card)
            
            # Cascade (Rule 702.84): exile cards until finding lower CMC, cast it free
            if getattr(card, 'has_cascade', False):
                def make_cascade_trigger(cascade_card):
                    cascade_cmc = Player._parse_cmc(cascade_card.cost) if cascade_card.cost else 0
                    def cascade_effect(game, source):
                        game._do_cascade(source.controller, cascade_cmc)
                    return cascade_effect
                cascade_trigger = StackItem(
                    effect=make_cascade_trigger(card),
                    source=card,
                    controller=player,
                    description=f"Cascade: {card.name}"
                )
                self.stack.cards.append(cascade_trigger)
                self.log_event(f"T{self.turn_count}: Cascade trigger: {card.name}")
            
            if not card.is_creature:
                for perm in self.battlefield.cards:
                    if perm.controller == player and perm.is_creature and getattr(perm, 'has_prowess', False):
                        if not hasattr(perm, '_temp_modifiers'): perm._temp_modifiers = []
                        perm._temp_modifiers.append({'power': 1, 'toughness': 1})
                        self.log_event(f"  Prowess: {perm.name} gets +1/+1")
            
            # Cast triggers: "whenever you cast a spell"
            for perm in list(self.battlefield.cards):
                if perm.controller == player and perm.is_creature and getattr(perm, 'cast_trigger', None):
                    try:
                        perm.cast_trigger(self, perm)
                    except Exception:
                        pass

            self.priority_player_index = (self.players.index(player) + 1) % 2
            self.pending_cast = None
            
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
            
        elif action['type'] == 'tap_ability':
            card = action['card']
            self._consecutive_passes = 0
            card.tapped = True
            self.log_event(f"T{self.turn_count}: {player.name} activates {card.name} (tap ability)")
            trigger = StackItem(
                effect=card.tap_ability_effect,
                source=card,
                controller=player,
                description=f"Tap ability: {card.name}"
            )
            self.stack.cards.append(trigger)
            self.priority_player_index = (self.players.index(player) + 1) % 2
        
        elif action['type'] == 'sacrifice_ability':
            card = action['card']
            self._consecutive_passes = 0
            self.log_event(f"T{self.turn_count}: {player.name} sacrifices {card.name}")
            if card in self.battlefield.cards:
                self.battlefield.remove(card)
                if not card.is_token:
                    card.controller.graveyard.add(card)
                self._fire_death_trigger(card)
            trigger = StackItem(
                effect=card.sacrifice_effect,
                source=card,
                controller=player,
                description=f"Sacrifice: {card.name}"
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
                
                # === EXALTED (Rule 702.82) ===
                # When exactly one creature attacks, each exalted source gives +1/+1
                if len(self.combat_attackers) == 1:
                    att = self.combat_attackers[0]
                    exalted_count = sum(1 for c in self.battlefield.cards 
                                       if c.controller == player and getattr(c, 'has_exalted', False))
                    if exalted_count > 0:
                        if not hasattr(att, '_temp_modifiers'): att._temp_modifiers = []
                        att._temp_modifiers.append({'power': exalted_count, 'toughness': exalted_count})
                        self.log_event(f"  Exalted x{exalted_count}: {att.name} gets +{exalted_count}/+{exalted_count}")
                
                # === BATTLE CRY (Rule 702.90) ===
                # Each attacker with battle cry gives +1/+0 to all OTHER attackers
                battle_cry_count = sum(1 for a in self.combat_attackers if getattr(a, 'has_battle_cry', False))
                if battle_cry_count > 0:
                    for att in self.combat_attackers:
                        # Each battle cry attacker buffs others (not itself)
                        others_with_bc = sum(1 for a in self.combat_attackers 
                                           if a != att and getattr(a, 'has_battle_cry', False))
                        if others_with_bc > 0:
                            if not hasattr(att, '_temp_modifiers'): att._temp_modifiers = []
                            att._temp_modifiers.append({'power': others_with_bc, 'toughness': 0})
                            self.log_event(f"  Battle Cry x{others_with_bc}: {att.name} gets +{others_with_bc}/+0")
                
                # === PLANESWALKER TARGETING (Rule 508.1a) ===
                # Designate which attackers hit planeswalkers vs the player
                opp = self.defending_player
                opp_pws = [c for c in self.battlefield.cards
                          if c.controller == opp and c.is_planeswalker and c.loyalty > 0]
                
                if opp_pws:
                    # Sort planeswalkers by threat level (highest loyalty first — closest to ultimate)
                    opp_pws.sort(key=lambda pw: pw.loyalty, reverse=True)
                    opp_blockers = [c for c in self.battlefield.cards
                                   if c.controller == opp and c.is_creature and not c.tapped]
                    
                    for att in self.combat_attackers:
                        att._attacking_pw = None  # Default: attack player
                        
                        # Evasive creatures should kill planeswalkers (hard to interact with)
                        if att.has_flying and not any(b.can_block_flyer for b in opp_blockers):
                            # This flyer can't be blocked — send it at the highest-loyalty PW
                            for pw in opp_pws:
                                if pw.loyalty > 0:
                                    att._attacking_pw = pw
                                    self.log_event(f"  {att.name} attacks {pw.name} (loyalty {pw.loyalty})")
                                    break
                        elif att.has_menace and len(opp_blockers) < 2:
                            for pw in opp_pws:
                                if pw.loyalty > 0:
                                    att._attacking_pw = pw
                                    self.log_event(f"  {att.name} attacks {pw.name} (loyalty {pw.loyalty})")
                                    break
                else:
                    # No planeswalkers — clear any stale pointers
                    for att in self.combat_attackers:
                        att._attacking_pw = None
                
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
                    # Broad triggers fire on attack too
                    if getattr(att, 'broad_trigger', None):
                        trigger = StackItem(
                            effect=att.broad_trigger,
                            source=att,
                            controller=att.controller,
                            description=f"Broad trigger: {att.name}"
                        )
                        self.stack.cards.append(trigger)
                        self.log_event(f"T{self.turn_count}: Broad trigger: {att.name}")
            # After declaring attackers, advance the phase.
            # This moves to the post-declaration priority round where
            # both players can respond with combat tricks (Rule 508.1m).
            self._state_hashes.clear()
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
            
            # === FLANKING (Rule 702.24) ===
            # Each attacker with flanking gives -1/-1 to each non-flanking blocker
            for att_id, blocker_list in valid_blocks.items():
                att = None
                for a in self.combat_attackers:
                    if a.id == att_id:
                        att = a
                        break
                if att and getattr(att, 'has_flanking', False):
                    for blk in blocker_list:
                        if not getattr(blk, 'has_flanking', False):
                            if not hasattr(blk, '_temp_modifiers'): blk._temp_modifiers = []
                            blk._temp_modifiers.append({'power': -1, 'toughness': -1})
                            self.log_event(f"  Flanking: {blk.name} gets -1/-1 (blocking {att.name})")
            
            # === BUSHIDO (Rule 702.44) ===
            # When a creature with bushido N is blocked, it gets +N/+N
            for att_id, blocker_list in valid_blocks.items():
                att = None
                for a in self.combat_attackers:
                    if a.id == att_id:
                        att = a
                        break
                if att and getattr(att, 'has_bushido', False) and att.bushido_count > 0:
                    n = att.bushido_count
                    if not hasattr(att, '_temp_modifiers'): att._temp_modifiers = []
                    att._temp_modifiers.append({'power': n, 'toughness': n})
                    self.log_event(f"  Bushido {n}: {att.name} gets +{n}/+{n}")
            
            # === BLOCK TRIGGERS ===
            for att_id, blocker_list in valid_blocks.items():
                for blk in blocker_list:
                    if getattr(blk, 'block_trigger', None):
                        try:
                            blk.block_trigger(self, blk)
                        except Exception:
                            pass
            
            # After declaring blockers, advance the phase.
            # Both players get priority before damage (Rule 509.4).
            self._state_hashes.clear()
            self.advance_phase()
        
        # Turn limit is handled by SimulationRunner (50 turns → draw)

    def resolve_combat_damage(self):
        """Full combat damage resolution — first strike then normal.
        
        Called directly by rules_sandbox.py and test files.
        In the main game loop, advance_phase handles combat damage
        phase-by-phase via _resolve_damage_for, but this method
        provides an all-at-once path for test and sandbox scenarios.
        """
        opponent = self.defending_player
        active = self.active_player
        prev_life_0 = self.players[0].life
        prev_life_1 = self.players[1].life
        
        # === FIRST STRIKE PHASE (Rule 510.4) ===
        first_strikers = [c for c in self.combat_attackers 
                         if c in self.battlefield.cards and (c.has_first_strike or c.has_double_strike)]
        
        if first_strikers:
            self._resolve_damage_for(first_strikers, opponent, active, is_first_strike=True)
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
                        # Infect (Rule 702.89): damage to players = poison counters
                        if getattr(att_card, 'has_infect', False):
                            opponent.poison_counters += power
                            self.log_event(f"T{self.turn_count}: {att_card.name} deals {power} infect to {opponent.name} (poison={opponent.poison_counters})")
                        else:
                            opponent.life -= power
                            opponent.fatal_blow_reason = f"unblocked combat damage from {att_card.name}"
                            self.log_event(f"T{self.turn_count}: {att_card.name} deals {power} to {opponent.name} ({opponent.life} life)")
                        
                        # Toxic (Rule 702.164): add toxic_count poison on combat damage
                        if getattr(att_card, 'has_toxic', False) and att_card.toxic_count > 0:
                            opponent.poison_counters += att_card.toxic_count
                            self.log_event(f"  Toxic {att_card.toxic_count}: {opponent.name} gets {att_card.toxic_count} poison (total={opponent.poison_counters})")
                        
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
                # Feb 2026 Rule Shift: Damage Assignment Order (Rule 510.1c) removed.
                # Attackers distribute damage freely among blockers.
                # We simulate optimal free distribution by sorting blockers by lowest remaining toughness.
                remaining_damage = power
                
                # Sort blockers by strategic kill priority (not just toughness)
                # Kill lifelink/deathtouch first, avoid death triggers when possible
                active_blockers = [b for b in blockers if b in self.battlefield.cards]
                def _blocker_kill_priority(b):
                    """Score blockers for damage assignment order (higher = kill first)."""
                    remaining_hp = (b.toughness or 1) - b.damage_taken
                    score = 1000 - remaining_hp  # Base: ascending toughness (easier kills first)
                    if b.has_lifelink: score += 500      # Kill lifelink ASAP (deny life gain)
                    if b.has_deathtouch: score += 300     # Kill deathtouch (remove future threat)
                    if b.has_first_strike or b.has_double_strike: score += 200
                    if getattr(b, 'static_effect', None): score += 150  # Lords/anthems
                    if getattr(b, 'death_effect', None): score -= 250   # Avoid triggering death effects
                    if getattr(b, 'has_undying', False): score -= 200   # Comes back anyway
                    if getattr(b, 'has_persist', False): score -= 150   # Comes back weaker
                    return score
                active_blockers.sort(key=_blocker_kill_priority, reverse=True)
                
                for blk in active_blockers:
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
                    
                    # Blocker power must be calculated BEFORE attacker damage reduces it via -1/-1 counters
                    blk_power = blk.power or 0
                    
                    # Mark damage on blocker (Rule 120.6)
                    # Infect/Wither/Blight: damage as -1/-1 counters instead (Rule 702.89/79)
                    if (att_card.has_blight or getattr(att_card, 'has_infect', False) or getattr(att_card, 'has_wither', False)) and damage_to_blocker > 0:
                        blk.counters['-1/-1'] = blk.counters.get('-1/-1', 0) + damage_to_blocker
                    else:
                        blk.damage_taken += damage_to_blocker
                    if att_card.has_deathtouch and damage_to_blocker > 0:
                        blk.deathtouch_damaged = True
                    
                    # Lifelink on attacker for damage dealt to blocker
                    if att_card.has_lifelink and damage_to_blocker > 0:
                        active.life += damage_to_blocker
                    
                    remaining_damage -= damage_to_blocker
                    
                    # Blocker damages attacker (only in normal phase, or if blocker has first strike)
                    if not is_first_strike or blk.has_first_strike or blk.has_double_strike:
                        if blk_power > 0:
                            # Protection on attacker prevents blocker damage
                            if not att_card.is_protected_from(blk):
                                if blk.has_blight or getattr(blk, 'has_infect', False) or getattr(blk, 'has_wither', False):
                                    att_card.counters['-1/-1'] = att_card.counters.get('-1/-1', 0) + blk_power
                                else:
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
                    opponent.fatal_blow_reason = f"trample damage from {att_card.name}"
                    self.log_event(f"T{self.turn_count}: {att_card.name} tramples {remaining_damage} to {opponent.name} ({opponent.life})")
                    if att_card.has_lifelink:
                        active.life += remaining_damage
                
                # Combat damage trigger fires whenever creature deals combat damage
                # (Rule 702 — "Whenever ~ deals combat damage" includes damage to blockers)
                total_dealt = power - remaining_damage
                if total_dealt > 0 and att_card.combat_damage_trigger:
                    trigger = StackItem(
                        effect=att_card.combat_damage_trigger,
                        source=att_card,
                        controller=att_card.controller,
                        description=f"Combat damage trigger: {att_card.name}"
                    )
                    self.stack.cards.append(trigger)
                    self.log_event(f"T{self.turn_count}: Combat damage trigger: {att_card.name}")

    def _resolve_stack_top(self):
        """Resolve the top item of the stack (Rule 608.2)."""
        if len(self.stack) == 0:
            self._stack_loops = 0  # Reset on empty stack
            return
            
        self._stack_loops += 1
        if self._stack_loops > 1000 and self.ssg_strict_mode:
            from admin.portal_logger import log_fidelity_crash
            msg = f"Infinite Loop Detected: 1000+ stack resolutions without emptying. Possible Rule 729 draw scenario unbounded."
            log_fidelity_crash("INFINITE_LOOP", "Stack Depth Exceeded", msg, {"stack_size": len(self.stack)})
            raise RuntimeError(msg)
        
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
            card._controlled_since_turn_start = True  # Will clear sickness on next untap
            
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
            target = getattr(card, 'spell_target', None)
            
            # CR 608.2b: Target Legality Check upon resolution (Fizzle)
            if target:
                target_legal = True
                if hasattr(target, 'is_creature') and target.is_creature:
                    if target not in self.battlefield.cards:
                        target_legal = False
                    elif getattr(target, 'has_hexproof', False) and target.controller != card.controller:
                        target_legal = False
                    elif hasattr(target, 'is_protected_from') and target.is_protected_from(card):
                        target_legal = False
                elif isinstance(target, Card):
                    # For counters, target must still be on the stack
                    if target not in self.stack.cards:
                        target_legal = False
                elif target in self.players:
                    pass
                else:
                    # Unknown target type or gone
                    target_legal = False

                if not target_legal:
                    target_name = getattr(target, 'name', str(target))
                    self.log_event(f"  {card.name} fizzles (target {target_name} is illegal)")
                    if card.from_graveyard:
                        self.exile.add(card)
                        card.from_graveyard = False
                    else:
                        (card.controller or self.active_player).graveyard.add(card)
                    self.check_state_based_actions()
                    return

            # Ward enforcement (Rule 702.21): if targeting a ward creature, pay the tax
            if target and hasattr(target, 'ward_cost') and target.ward_cost:
                caster = card.controller or self.active_player
                if caster.can_pay_cost(target.ward_cost, self):
                    caster.pay_cost(target.ward_cost, self)
                    self.log_event(f"  Ward: {caster.name} pays {target.ward_cost} for {target.name}")
                else:
                    # Can't pay ward → spell countered (Rule 702.21b)
                    self.log_event(f"  {card.name} is countered by {target.name}'s ward (can't pay {target.ward_cost})")
                    (card.controller or self.active_player).graveyard.add(card)
                    if card.from_graveyard:
                        self.exile.add(card)
                    self.check_state_based_actions()
                    return
            
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

    # ──── Keyword Action Helpers ────────────────────────────────────

    def create_token(self, controller, name: str = "Token", power: int = 1, 
                     toughness: int = 1, creature_types: str = "", 
                     keywords: dict = None) -> 'Card':
        """Create a token creature on the battlefield (Rule 111).
        
        Args:
            controller: Player who controls the token
            name: Token name (default "Token")
            power: Token power
            toughness: Token toughness
            creature_types: Space-separated creature types
            keywords: Dict of keyword abilities (e.g., {'flying': True, 'haste': True})
        
        Returns:
            The created Card token
        """
        from engine.card import Card
        token = Card(name=name, cost="", type_line=f"Token Creature — {creature_types}",
                    base_power=power, base_toughness=toughness, oracle_text="")
        token.controller = controller
        token.is_token = True
        token.summoning_sickness = True
        
        # Apply keyword abilities
        if keywords:
            if keywords.get('flying'): token.has_flying = True
            if keywords.get('haste'):
                token.has_haste = True
                token.summoning_sickness = False
            if keywords.get('trample'): token.has_trample = True
            if keywords.get('deathtouch'): token.has_deathtouch = True
            if keywords.get('lifelink'): token.has_lifelink = True
            if keywords.get('vigilance'): token.has_vigilance = True
            if keywords.get('first_strike'): token.has_first_strike = True
            if keywords.get('menace'): token.has_menace = True
        
        self.battlefield.add(token)
        self.log_event(f"T{self.turn_count}: {controller.name} creates {power}/{toughness} {name}")
        return token

    def do_surveil(self, player, n: int):
        """Surveil N: look at top N cards, put any into graveyard, rest on top."""
        if n <= 0 or len(player.library) == 0:
            return
        top_cards = []
        for _ in range(min(n, len(player.library))):
            if player.library.cards:
                top_cards.append(player.library.cards.pop(0))
        
        # Heuristic: send excess lands and expensive spells to GY
        keep = []
        mill = []
        for c in top_cards:
            if c.is_land and any(k.is_land for k in keep):
                mill.append(c)  # Extra lands → GY
            elif hasattr(c, 'has_dredge') and c.has_dredge:
                mill.append(c)  # Dredge cards WANT to be in GY
            else:
                keep.append(c)
        
        for c in mill:
            player.graveyard.add(c)
        for c in reversed(keep):
            player.library.cards.insert(0, c)
        
        if mill:
            mill_names = ', '.join(c.name for c in mill)
            self.log_event(f"  Surveil {n}: {player.name} sends {mill_names} to graveyard")
        else:
            self.log_event(f"  Surveil {n}: {player.name} keeps all on top")

    def do_investigate(self, player):
        """Investigate: create a Clue artifact token with 'sacrifice, pay 2: draw'."""
        from engine.card import Card
        clue = Card(name="Clue", cost="", type_line="Token Artifact — Clue",
                   oracle_text="{2}, Sacrifice this artifact: Draw a card.")
        clue.controller = player
        
        def clue_ability(game, card):
            card.controller.draw_card(1)
            game.log_event(f"  Clue: {card.controller.name} draws a card")
        
        clue.activated_abilities = [{
            'cost_tap': False,
            'cost_mana': '{2}',
            'cost_sacrifice': True,
            'effect': clue_ability,
            'description': 'Sacrifice, {2}: Draw a card',
            'is_mana_ability': False
        }]
        self.battlefield.add(clue)
        self.log_event(f"  Investigate: {player.name} creates a Clue token")

    def do_explore(self, creature):
        """Explore: reveal top card, if land → hand, else +1/+1 and may discard."""
        player = creature.controller
        if not player or len(player.library) == 0:
            return
        
        top = player.library.cards.pop(0)
        if top.is_land:
            player.hand.add(top)
            self.log_event(f"  Explore: {creature.name} reveals {top.name} (land → hand)")
        else:
            creature.counters['+1/+1'] = creature.counters.get('+1/+1', 0) + 1
            # Heuristic: keep the card if it's good, discard if low value
            cmc = Player._parse_cmc(top.cost) if top.cost else 0
            if cmc <= 2 or top.is_removal:
                player.library.cards.insert(0, top)  # Keep on top
                self.log_event(f"  Explore: {creature.name} reveals {top.name} (+1/+1 counter, keep on top)")
            else:
                player.graveyard.add(top)
                self.log_event(f"  Explore: {creature.name} reveals {top.name} (+1/+1 counter, discard)")

    def do_proliferate(self, player):
        """Proliferate: add one counter of each type to everything with counters."""
        targets = []
        for card in self.battlefield.cards:
            if card.counters:
                for ctype, count in list(card.counters.items()):
                    if count > 0:
                        card.counters[ctype] = count + 1
                        targets.append(f"{card.name}({ctype})")
        # Also add poison to players with poison
        for p in self.players:
            if getattr(p, 'poison_counters', 0) > 0:
                p.poison_counters += 1
                targets.append(f"{p.name}(poison)")
        
        if targets:
            self.log_event(f"  Proliferate: added counters to {', '.join(targets)}")

    def do_connive(self, creature):
        """Connive: draw a card, discard a card, if nonland discarded → +1/+1."""
        player = creature.controller
        if not player:
            return
        player.draw_card(1)
        if len(player.hand) > 0:
            # Discard worst card (highest CMC land, or weakest spell)
            hand = list(player.hand.cards)
            hand.sort(key=lambda c: (0 if c.is_land else 1, -(getattr(c, 'base_power', 0) or 0)))
            discard = hand[0]
            player.hand.remove(discard)
            player.graveyard.add(discard)
            if not discard.is_land:
                creature.counters['+1/+1'] = creature.counters.get('+1/+1', 0) + 1
                self.log_event(f"  Connive: {creature.name} discards {discard.name} (nonland → +1/+1 counter)")
            else:
                self.log_event(f"  Connive: {creature.name} discards {discard.name} (land)")

    def do_amass(self, player, n: int, army_type: str = 'Zombies'):
        """Amass N: create a 0/0 Army token or put N +1/+1 counters on existing Army."""
        from engine.card import Card
        # Find existing Army token
        army = None
        for card in self.battlefield.cards:
            if card.controller == player and getattr(card, 'is_token', False) and 'Army' in card.type_line:
                army = card
                break
        
        if army:
            army.counters['+1/+1'] = army.counters.get('+1/+1', 0) + n
            self.log_event(f"  Amass {n}: {army.name} grows (+1/+1 counters → {army.counters.get('+1/+1', 0)})")
        else:
            army = Card(name=f"{army_type} Army", cost="", 
                       type_line=f"Token Creature — {army_type} Army",
                       base_power=0, base_toughness=0)
            army.controller = player
            army.summoning_sickness = True
            army.counters['+1/+1'] = n
            self.battlefield.add(army)
            self.log_event(f"  Amass {n}: {player.name} creates a {army_type} Army token with {n} +1/+1 counters")

    def _do_cascade(self, player, source_cmc: int):
        """Cascade: exile cards from library until finding one with lower CMC, cast it free."""
        exiled = []
        found = None
        
        for _ in range(min(50, len(player.library))):
            if not player.library.cards:
                break
            card = player.library.cards.pop(0)
            exiled.append(card)
            
            if not card.is_land and card.cost:
                card_cmc = Player._parse_cmc(card.cost)
                if card_cmc < source_cmc:
                    found = card
                    exiled.remove(card)
                    break
        
        # Put exiled cards on bottom of library in random order
        import random
        random.shuffle(exiled)
        for c in exiled:
            player.library.cards.append(c)
        
        if found:
            # Cast for free (Rule 702.84a)
            found.controller = player
            self.stack.add(found)
            self.log_event(f"  Cascade: {player.name} casts {found.name} (CMC {_P._parse_cmc(found.cost)}) for free!")
        else:
            self.log_event(f"  Cascade: no spell found with CMC < {source_cmc}")

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
            
            # Apply continuous effects FIRST so CDAs (Death's Shadow,
            # Tarmogoyf, etc.) are recalculated before toughness checks.
            # Per CR 704.3, SBAs are checked after any game event, and
            # continuous effects (CR 613) apply at all times.
            self._apply_static_effects()
            
            # 704.5b: Draw from empty library = lose game
            for player in self.players:
                if getattr(player, 'library_empty_draw', False):
                    player.life = 0
                    player.library_empty_draw = False
                    player.fatal_blow_reason = "drawing from an empty library"
                    self.log_event(f"T{self.turn_count}: SBA: {player.name} tried to draw from an empty library!")
                    changes = True
            
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
                    reason = getattr(player, 'fatal_blow_reason', None) or "combat/spell damage"
                    self.resolution_reason = f"{player.name} lost to {reason}."
                    self.log_event(f"RESULT: {player.name} defeated (life={player.life}). Reason: {reason}. Winner: {self.winner.name} on turn {self.turn_count}")
                    return
                # 704.5c: Player with 10+ poison counters loses
                if getattr(player, 'poison_counters', 0) >= 10:
                    self.game_over = True
                    self.winner = self.players[(self.players.index(player) + 1) % 2]
                    self.resolution_reason = f"{player.name} lost to 10+ poison counters."
                    self.log_event(f"RESULT: {player.name} defeated (poison={player.poison_counters}). Winner: {self.winner.name} on turn {self.turn_count}")
                    return
                # Commander 21-point damage rule
                if hasattr(player, 'commander_damage_taken'):
                    if any(dmg >= 21 for dmg in player.commander_damage_taken.values()):
                        self.game_over = True
                        self.winner = self.players[(self.players.index(player) + 1) % 2]
                        self.resolution_reason = f"{player.name} lost to 21+ Commander Damage."
                        self.log_event(f"RESULT: {player.name} defeated (21+ Commander Damage). Winner: {self.winner.name} on turn {self.turn_count}")
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
                    self._fire_death_trigger(card)
                    changes = True
                    continue
                
                # Undying (Rule 702.92): return with +1/+1 if no +1/+1 counters
                if getattr(card, 'has_undying', False) and card.counters.get('+1/+1', 0) == 0:
                    card.counters['+1/+1'] = card.counters.get('+1/+1', 0) + 1
                    card.damage_taken = 0
                    card.deathtouch_damaged = False
                    card.summoning_sickness = True
                    self.battlefield.add(card)
                    self.log_event(f"T{self.turn_count}: Undying: {card.name} returns with +1/+1 counter")
                    changes = True
                    continue
                
                # Persist (Rule 702.78): return with -1/-1 if no -1/-1 counters
                if getattr(card, 'has_persist', False) and card.counters.get('-1/-1', 0) == 0:
                    card.counters['-1/-1'] = card.counters.get('-1/-1', 0) + 1
                    card.damage_taken = 0
                    card.deathtouch_damaged = False
                    card.summoning_sickness = True
                    self.battlefield.add(card)
                    self.log_event(f"T{self.turn_count}: Persist: {card.name} returns with -1/-1 counter")
                    changes = True
                    continue
                
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
        
        
        # Death triggers are now on the stack — they'll be resolved via
        # the normal priority system (agents get to respond).
        # The game loop's dual-pass mechanism handles this.
        
        if self.ssg_strict_mode:
            self._audit_sbas()
            
    def _audit_sbas(self):
        """Strict verification of State-Based Action invariants (Rule 704.5).
        Throws an exception and logs a crash if the engine failed to clean up the state."""
        from admin.portal_logger import log_fidelity_crash
        
        for player in self.players:
            if player.life <= 0 and not self.game_over:
                msg = f"SBA Invariant Violation (704.5a): Player {player.name} has {player.life} life but game_over is False."
                log_fidelity_crash("SBA_704.5a", "SBA Audit Failure", msg, {})
                raise RuntimeError(msg)
            if hasattr(player, 'commander_damage_taken') and any(dmg >= 21 for dmg in player.commander_damage_taken.values()) and not self.game_over:
                msg = f"SBA Invariant Violation (Commander Damage): Player {player.name} has taken 21+ Commander Damage but game_over is False."
                log_fidelity_crash("SBA_Commander", "SBA Audit Failure", msg, {})
                raise RuntimeError(msg)
                
        for card in self.battlefield.cards:
            if card.is_creature and card.toughness <= 0 and not card.has_indestructible:
                # Wait, indestructible doesn't prevent 0 toughness death!
                if card.toughness <= 0:
                    msg = f"SBA Invariant Violation (704.5f): {card.name} is on battlefield with 0 toughness."
                    log_fidelity_crash("SBA_704.5f", "SBA Audit Failure", msg, {"card_id": card.id})
                    raise RuntimeError(msg)
            if card.is_creature and card.damage_taken >= (card.toughness or 1) and not card.has_indestructible:
                msg = f"SBA Invariant Violation (704.5g): {card.name} has {card.damage_taken} damage but {card.toughness} toughness."
                log_fidelity_crash("SBA_704.5g", "SBA Audit Failure", msg, {"card_id": card.id})
                raise RuntimeError(msg)
