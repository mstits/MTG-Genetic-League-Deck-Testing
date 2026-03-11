"""StrategicAgent — Advanced AI with tempo, card advantage, and look-ahead.

Extends HeuristicAgent with three strategic scoring dimensions:

    1. **Tempo** — Mana efficiency. Spending all available mana each turn is
       rewarded; leaving mana unused is penalized.

    2. **Card Advantage (CA)** — Hand size delta. Drawing cards, casting
       cantrips, and 2-for-1 removal all shift the CA score.

    3. **Virtual Card Advantage (VCA)** — Board quality vs quantity. A single
       5/5 is worth more than five 1/1s in VCA terms.

    4. **Look-ahead** — 1-ply search. For each candidate action, clone the game
       state, apply the action, then simulate the opponent's best response.
       The resulting board evaluation penalizes actions the opponent can punish.

Scoring weights are tunable to shift between aggro (high tempo) and control
(high CA) play styles.
"""

import re
from agents.heuristic_agent import HeuristicAgent


# ──── Scoring Weights ────────────────────────────────────────────────────────

TEMPO_WEIGHT = 0.30       # Mana efficiency
CA_WEIGHT = 0.40          # Card advantage (hand size + draw)
VCA_WEIGHT = 0.15         # Virtual card advantage (board quality)
LOOKAHEAD_WEIGHT = 0.15   # Worst-case opponent response


class StrategicAgent(HeuristicAgent):
    """AI agent using tempo, card advantage, and predictive look-ahead.

    Inherits the HeuristicAgent's blocking, attacking, and stack response
    logic, but overrides the main-phase casting decision with a scoring
    system that evaluates every legal action across 4 dimensions.

    Attributes:
        look_ahead_depth: Number of opponent response plies to search (default 1).
    """

    def __init__(self, look_ahead_depth: int = 1, bracket: int = 4):
        super().__init__()
        self.look_ahead_depth = look_ahead_depth
        self.bracket = bracket

    # ──── Board Evaluation ────────────────────────────────────────────────

    @staticmethod
    def _board_power(game, player) -> float:
        """Total 'power score' of creatures a player controls.
        Accounts for keywords and abilities that make creatures more valuable."""
        # Fast path memoization hook
        board_hash = hash(tuple(sorted(c.id for c in game.battlefield.cards if c.controller == player)))
        cache_key = f"_power_cache_{board_hash}"
        if getattr(player, cache_key, None) is not None:
            return getattr(player, cache_key)

        score = 0.0
        for card in game.battlefield.cards:
            if card.controller == player and card.is_creature:
                p = card.power or 0
                t = card.toughness or 0
                base = p + t * 0.5  # Offensive weight > defensive
                if card.has_flying:
                    base += 2
                if card.has_trample:
                    base += 2
                if card.has_menace:
                    base += 2
                if card.has_deathtouch:
                    base += 2
                if card.has_lifelink:
                    base += 1
                if card.has_vigilance:
                    base += 1
                if card.has_first_strike or card.has_double_strike:
                    base += 1.5
                if card.has_hexproof:
                    base += 1
                if card.has_indestructible:
                    base += 3
                # Value ability-bearing creatures higher
                if card.etb_effect: base += 1.0
                if card.death_effect: base += 1.5
                if card.attack_trigger: base += 1.5
                if getattr(card, 'cast_trigger', None): base += 1.0
                if card.combat_damage_trigger: base += 1.0
                if card.static_effect: base += 3.0  # Lords are very valuable
                if getattr(card, 'is_mana_dork', False): base += 1.5
                if card.has_infect or card.has_toxic: base += 2.0
                if getattr(card, 'sacrifice_effect', None): base += 1.0
                if getattr(card, 'tap_ability_effect', None): base += 2.0
                if getattr(card, 'broad_trigger', None): base += 1.0
                if getattr(card, 'has_self_pump', False): base += 0.5
                # Add pump potential — treat as half since it requires mana
                base += getattr(card, 'self_pump_power', 0) * 0.5
                if getattr(card, 'has_protection', False): base += 1.5
                if card.has_ward: base += 1.0
                if card.has_vigilance: base += 0.5
                if card.has_reach: base += 0.5
                if card.has_flash: base += 0.5
                if card.has_prowess: base += 1.0
                score += base
            # Also value non-creature permanents
            elif card.controller == player:
                if card.static_effect: score += 3.0
                if getattr(card, 'equip_bonus', None): score += 1.5
                if getattr(card, 'enchantment_trigger', None): score += 2.0
                if card.upkeep_effect: score += 2.0
                if getattr(card, 'tap_ability_effect', None): score += 2.0
        
        # Lock in computed state cache
        setattr(player, cache_key, score)

        # Value untapped lands loosely as potential interaction
        score += player.available_mana(game) * 0.2
        return score

    @staticmethod
    def _count_available_mana(game, player) -> int:
        """Count untapped mana sources (lands + mana dorks)."""
        count = 0
        for card in game.battlefield.cards:
            if card.controller != player:
                continue
            if card.is_land and not card.tapped:
                count += 1
            elif card.is_creature and not card.tapped and getattr(card, 'produced_mana', None):
                count += 1  # Mana dork
        return count

    @staticmethod
    def _card_cmc(card) -> int:
        """Extract CMC from a card's cost string."""
        if not card.cost:
            return 0
        total = 0
        for g in re.findall(r'\{(\d+)\}', card.cost):
            total += int(g)
        total += len(re.findall(r'\{([WUBRGC])\}', card.cost))
        total += len(re.findall(r'\{[WUBRG]/[WUBRG]\}', card.cost))
        return total

    # ──── Multiplayer Threat Matrix ───────────────────────────────────────

    def _evaluate_threat(self, game, opp) -> float:
        """Calculate Political Leverage / Threat Weight for a specific opponent.
        
        Evaluates board state quality, hand size, and life total relative 
        to the Commander format's danger heuristic.
        """
        board_score = self._board_power(game, opp)
        hand_score = len(opp.hand) * 1.5
        
        # Add Commander tax/damage heuristics if commander format
        cmd_dmg = sum(opp.commander_damage_taken.values()) if hasattr(opp, 'commander_damage_taken') else 0
        
        # Evasion count — evasive creatures are harder to deal with
        evasion = sum(1 for c in game.battlefield.cards 
            if c.controller == opp and c.is_creature and 
            (c.has_flying or c.has_trample or c.has_menace or c.is_unblockable))
        
        # Planeswalker count — repeatable value engines
        pw_count = sum(1 for c in game.battlefield.cards 
            if c.controller == opp and c.is_planeswalker)
        
        # Enchantment/artifact (non-creature) permanents — ongoing value
        noncreature_perms = sum(1 for c in game.battlefield.cards 
            if c.controller == opp and (c.is_enchantment or c.is_artifact) and not c.is_creature)
        
        # Threat = board power + hand size + evasion + planeswalkers + permanents - cmd damage
        return (board_score + hand_score + evasion * 1.5 + pw_count * 3.0 + 
                noncreature_perms * 0.5 + (opp.life * 0.1) - (cmd_dmg * 0.5))

    def _get_primary_opponent(self, game, player) -> 'Player':
        """Returns the highest-threat opponent using the Threat-Weight Matrix."""
        opponents = [p for p in game.players if p != player]
        if not opponents:
            return player  # Fallback if somehow solo
        if len(opponents) == 1:
            return opponents[0]
            
        # In multiplayer, sort by threat weight (Political Leverage)
        # Highest score = primary target for attacks and removal
        opponents.sort(key=lambda o: self._evaluate_threat(game, o), reverse=True)
        return opponents[0]

    # ──── Scoring Functions ───────────────────────────────────────────────

    def _evaluate_tempo(self, game, player, action) -> float:
        """Score mana efficiency: using more of your available mana is better.

        Returns 0.0–1.0 where 1.0 = using all available mana this turn.
        Passing gets 0.0.
        """
        if action['type'] == 'pass':
            return 0.0
        if action['type'] == 'play_land':
            return 0.3  # Lands are free but important

        available = max(self._count_available_mana(game, player), 1)

        if action['type'] == 'announce_cast':
            cmc = self._card_cmc(action['card'])
            return min(cmc / available, 1.0)

        # Crew, equip, activate — moderate tempo value
        if action['type'] == 'tap_ability':
            return 0.5  # Tap abilities are instant-speed value
        if action['type'] == 'sacrifice_ability':
            return 0.6  # Sacrifice usually generates immediate value
        return 0.4

    def _evaluate_card_advantage(self, game, player, action) -> float:
        """Score card advantage impact.

        Positive actions: draw effects, 2-for-1 removal, cantrips.
        Negative: discarding, trading down.
        Returns -1.0 to 1.0.
        """
        if action['type'] == 'pass':
            return 0.0

        if action['type'] not in ('announce_cast', 'tap_ability', 'sacrifice_ability'):
            return 0.1  # Non-spell actions are CA-neutral

        if action['type'] == 'tap_ability':
            card = action['card']
            # Tap abilities that draw or deal damage are CA-positive
            text = (card.oracle_text or '').lower()
            if 'draw' in text: return 0.3
            if 'damage' in text or 'destroy' in text: return 0.2
            return 0.1
        
        if action['type'] == 'sacrifice_ability':
            card = action['card']
            text = (card.oracle_text or '').lower()
            # Sacrifice for draw/damage is CA-positive, but loses a creature
            if 'draw' in text: return 0.3
            if 'damage' in text: return 0.2
            return -0.1  # Losing a creature is CA-negative unless it does something

        card = action['card']
        score = 0.0

        # Cantrips and card draw
        oracle = (card.oracle_text or '').lower()
        if 'draw a card' in oracle:
            score += 0.4
        if 'draw two' in oracle or 'draw 2' in oracle:
            score += 0.7
        if 'draw three' in oracle or 'draw 3' in oracle:
            score += 0.9

        # Removal = trading 1 card for 1 card, but we chose to exchange
        if card.is_removal:
            score += 0.3
        if card.is_board_wipe:
            opp = self._get_primary_opponent(game, player)
            opp_count = sum(1 for c in game.battlefield.cards
                           if c.controller == opp and c.is_creature)
            my_count = sum(1 for c in game.battlefield.cards
                          if c.controller == player and c.is_creature)
            # Board wipe is CA-positive when we're behind
            if opp_count > my_count:
                score += 0.5 + 0.1 * (opp_count - my_count)

        # Creatures that generate value on ETB
        if card.is_creature and card.etb_effect:
            score += 0.3

        # Counter-spells prevent opponent's card from resolving → 1-for-1
        if card.is_counter:
            score += 0.2
        
        # Creatures with death effects = CA-positive (2-for-1 when they die)
        if card.is_creature and card.death_effect:
            score += 0.25
        
        # Discard spells = opponent loses a card → CA-positive
        if card.is_discard:
            score += 0.3
        
        # Flashback in graveyard = free CA (card already spent)
        if card.flashback_cost:
            score += 0.2

        return max(-1.0, min(1.0, score))

    def _evaluate_virtual_card_advantage(self, game, player, action) -> float:
        """Score board quality improvement from this action.

        A high-quality creature (big stats + keywords) improves VCA more
        than a small vanilla creature.
        Returns -10.0 to 1.0.
        """
        if action['type'] == 'pass':
            return 0.0

        if action['type'] == 'announce_cast' and action['card'].is_creature:
            card = action['card']
            
            # CDA survival check: creatures that would immediately die to SBAs
            # get a massively negative score so the agent won't cast them
            cda = getattr(card, 'cda_type', '')
            if cda == 'deaths_shadow':
                effective_toughness = (card.base_toughness or 13) - player.life
                if effective_toughness <= 0:
                    return -10.0  # DOA — don't cast
            elif cda == 'scourge_skyclaves':
                highest_life = max(p.life for p in game.players)
                if 20 - highest_life <= 0:
                    return -10.0
            
            p = card.base_power or 0
            t = card.base_toughness or 0
            keyword_bonus = sum([
                card.has_flying * 0.1,
                card.has_trample * 0.1,
                card.has_deathtouch * 0.15,
                card.has_lifelink * 0.1,
                card.has_hexproof * 0.1,
                card.has_indestructible * 0.2,
                card.has_first_strike * 0.05,
                card.has_double_strike * 0.15,
                card.has_menace * 0.08,
                card.has_ward * 0.08,
                card.has_vigilance * 0.05,
                card.has_haste * 0.1,
                card.has_flash * 0.05,
                card.has_prowess * 0.08,
                card.has_reach * 0.03,
                card.has_defender * -0.1,  # Defenders can't attack
                getattr(card, 'has_protection', False) * 0.12,
            ])
            # Ability bonus
            ability_bonus = sum([
                bool(card.etb_effect) * 0.1,
                bool(card.death_effect) * 0.1,
                bool(card.attack_trigger) * 0.1,
                bool(getattr(card, 'cast_trigger', None)) * 0.05,
                bool(getattr(card, 'tap_ability_effect', None)) * 0.1,
                bool(getattr(card, 'broad_trigger', None)) * 0.05,
                bool(card.static_effect) * 0.15,
                bool(card.combat_damage_trigger) * 0.08,
                bool(getattr(card, 'sacrifice_effect', None)) * 0.05,
                bool(getattr(card, 'has_self_pump', False)) * 0.05,
                bool(card.upkeep_effect) * 0.08,
            ])
            # Normalize: a 5/5 with flying ≈ 1.0
            return min((p + t) / 10.0 + keyword_bonus + ability_bonus, 1.0)
        
        # Tap/sacrifice abilities provide board value
        if action['type'] == 'tap_ability':
            return 0.3
        if action['type'] == 'sacrifice_ability':
            card = action['card']
            text = (card.oracle_text or '').lower()
            if 'draw' in text: return 0.4
            return 0.1

        # Removal improves VCA by degrading opponent's board
        if action['type'] == 'announce_cast' and action['card'].is_removal:
            return 0.4
        
        # Board wipe — high VCA when opponent has more creatures
        if action['type'] == 'announce_cast' and action['card'].is_board_wipe:
            opp = [p for p in game.players if p != player][0]
            my_count = sum(1 for c in game.battlefield.cards if c.controller == player and c.is_creature)
            opp_count = sum(1 for c in game.battlefield.cards if c.controller == opp and c.is_creature)
            if opp_count > my_count:
                return 0.6  # Wipe when behind on board
            return 0.1  # Avoid wiping when ahead
        
        # Enchantments
        if action['type'] == 'announce_cast' and action['card'].is_enchantment:
            card = action['card']
            if card.static_effect: return 0.5
            if getattr(card, 'enchantment_trigger', None): return 0.4
            if card.is_aura:
                my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
                return 0.35 if my_creatures else 0.0
            return 0.2
        
        # Equipment casting
        if action['type'] == 'announce_cast' and action['card'].is_artifact:
            card = action['card']
            if getattr(card, 'equip_bonus', None): return 0.3
            if card.static_effect: return 0.4
            return 0.2
        
        # Equip action
        if action['type'] == 'equip':
            return 0.25
        
        # Burn, draw, counter, buff, discard spells
        if action['type'] == 'announce_cast':
            card = action['card']
            if card.is_burn: return 0.3
            if card.is_draw: return 0.35
            if card.is_counter: return 0.3
            if card.is_buff: return 0.2
            if card.is_discard: return 0.25
            # Kicker bonus: enhanced mode = more value
            kicker_bonus = 0.0
            if getattr(card, 'kicker_cost', '') and getattr(card, 'was_kicked', False):
                kicker_bonus = 0.15
            # Board wipe scales with opponent's creature count
            if card.is_board_wipe:
                opp = [p for p in game.players if p != player][0]
                opp_creatures = sum(1 for c in game.battlefield.cards 
                    if c.controller == opp and c.is_creature)
                return 0.2 + min(opp_creatures * 0.1, 0.5) + kicker_bonus
            # Lifegain — more valuable at low life
            if card.is_lifegain:
                return 0.35 if player.life <= 8 else 0.15
            # Mill — more valuable when opponent library is small
            if card.is_mill:
                opponent = [p for p in game.players if p != player][0]
                return 0.4 if len(opponent.library) <= 20 else 0.15
            # Planeswalker — repeatable value engine
            if card.is_planeswalker:
                return 0.6  # Planeswalkers are very high VCA

        # Tap ability VCA — repeatable effects are high quality
        if action['type'] == 'tap_ability':
            text = (action['card'].oracle_text or '').lower()
            if 'draw' in text: return 0.5
            if 'destroy' in text or 'exile' in text: return 0.5
            if 'create' in text and 'token' in text: return 0.35
            if 'damage' in text: return 0.3
            return 0.2
        
        # Sacrifice ability VCA — one-shot value
        if action['type'] == 'sacrifice_ability':
            text = (action['card'].oracle_text or '').lower()
            if 'draw' in text: return 0.4
            if 'destroy' in text or 'exile' in text: return 0.4
            return 0.15
        
        # Cycling VCA — card quality improvement
        if action['type'] == 'cycle':
            return 0.15  # Modest quality improvement

        return 0.1

    def _evaluate_look_ahead(self, game, player, action) -> float:
        """1-ply look-ahead: simulate our action, then opponent's best response.

        Returns the board evaluation AFTER the opponent responds optimally.
        Higher = better for us.
        """
        if action['type'] == 'pass':
            return 0.0

        try:
            # Clone and apply our action
            sim_game = game.clone()
            sim_player = sim_game.players[game.players.index(player)]
            # Find the same primary opponent in the simulated game
            primary_opp_original = self._get_primary_opponent(game, player)
            try:
                opp_idx = game.players.index(primary_opp_original)
                sim_opp = sim_game.players[opp_idx]
            except ValueError:
                sim_opp = sim_player

            sim_game.apply_action(action)

            # If game ended, evaluate immediately
            if sim_game.game_over:
                if sim_game.winner == sim_player:
                    # Bracket 1-3 (Casual) "Sweaty Play" penalty
                    if self.bracket <= 3 and getattr(sim_game, 'turn_count', 0) < 6:
                        return -5.0  # Strongly penalize winning before turn 6
                    return 1.0
                elif sim_game.winner == sim_opp:
                    return -1.0
                elif sim_game.winner is not None and sim_game.winner != sim_player:
                    # KINGMAKER PENALTY: Action gave the game to a 3rd party!
                    return -10.0
                return 0.0

            # Get opponent's legal actions and find their best response
            opp_actions = sim_game.get_legal_actions()
            if not opp_actions:
                # Opponent can't do anything → great for us
                our_power = self._board_power(sim_game, sim_player)
                their_power = self._board_power(sim_game, sim_opp)
                return min(max((our_power - their_power) / 20.0, -1.0), 1.0)

            # Evaluate board state after each opponent response
            worst_score = 1.0
            for opp_action in opp_actions[:5]:  # Limit for performance
                try:
                    sim2 = sim_game.clone()
                    sim2_player = sim2.players[game.players.index(player)]
                    try:
                        opp_idx = game.players.index(primary_opp_original)
                        sim2_opp = sim2.players[opp_idx]
                    except ValueError:
                        sim2_opp = sim2_player
                    sim2.apply_action(opp_action)

                    our_power = self._board_power(sim2, sim2_player)
                    their_power = self._board_power(sim2, sim2_opp)
                    
                    # Kingmaker check (cEDH): did a secondary opponent just get way stronger than us?
                    kingmaker_penalty = 0.0
                    casual_penalty = 0.0
                    for other_opp in sim2.players:
                        if other_opp != sim2_player and other_opp != sim2_opp:
                            other_power = self._board_power(sim2, other_opp)
                            if other_power > our_power + 10.0:  # Disproportionately large advantage
                                kingmaker_penalty -= 5.0
                                
                            # Threat-Level Management (Brackets 1-3)
                            # Penalize eliminating non-primary threats while primary threat exists
                            if self.bracket <= 3:
                                orig_other_opp = next((p for p in game.players if p.name == other_opp.name), None)
                                if orig_other_opp and orig_other_opp.life > 0 and other_opp.life <= 0:
                                    casual_penalty -= 3.0  # Huge penalty for casual elimination of non-archenemy
                                
                    life_delta = (sim2_player.life - sim2_opp.life) / 20.0
                    hand_delta = (len(sim2_player.hand) - len(sim2_opp.hand)) / 7.0

                    score = (our_power - their_power) / 20.0 + life_delta * 0.3 + hand_delta * 0.2 + kingmaker_penalty + casual_penalty
                    worst_score = min(worst_score, score)
                except Exception:
                    continue

            return max(-1.0, min(1.0, worst_score))

        except Exception:
            return 0.0  # On error, treat as neutral

    # ──── Main Decision Override ──────────────────────────────────────────

    def get_action(self, game, player) -> dict:
        """Score every legal action across 4 dimensions, pick the best.

        Delegates stack responses, blocking, and attacking to the parent
        HeuristicAgent (those are already well-tuned). Only overrides
        main-phase casting decisions where strategic depth matters most.
        """
        legal = game.get_legal_actions()
        if not legal:
            return {'type': 'pass'}

        opp = self._get_primary_opponent(game, player)
        
        # --- PENDING CAST STATE MACHINE (CR 601.2) ---
        if game.pending_cast:
            # Delegate to HeuristicAgent's excellent state machine handler
            return super().get_action(game, player)

        # === Delegate to parent for non-casting decisions ===

        # Stack: counter-spell logic stays with HeuristicAgent
        if len(game.stack) > 0:
            return super().get_action(game, player)

        # Blocking: HeuristicAgent's keyword-aware blocking is excellent
        for action in legal:
            if action['type'] == 'declare_blockers':
                return super().get_action(game, player)

        # Crew vehicles before combat (keep parent logic)
        crew_actions = [a for a in legal if a['type'] == 'crew']
        if crew_actions and game.current_phase == 'Main 1':
            best = max(crew_actions, key=lambda a: (a['vehicle'].power or 0))
            return best

        # Attacking: use parent's smart attacker selection
        for action in legal:
            if action['type'] == 'declare_attackers':
                return super().get_action(game, player)

        # === Strategic scoring for casting decisions ===

        # Always play a land if we can — delegate to parent's smart sequencer
        land_actions = [a for a in legal if a['type'] == 'play_land']
        if land_actions:
            # HeuristicAgent has color-aware T3 land sequencing
            return super().get_action(game, player)

        # Score all castable actions
        castable = [a for a in legal if a['type'] in ('announce_cast', 'activate_ability',
                                                       'equip', 'loyalty_ability', 'cycle',
                                                       'tap_ability', 'sacrifice_ability')]

        if not castable:
            return {'type': 'pass'}

        # Check for instant-speed holdback opportunity
        instants_in_hand = [c for c in player.hand.cards
                           if (c.is_instant or c.has_flash) and
                           player.can_pay_cost(c.cost, game)]
        high_value_instants = [c for c in instants_in_hand
                              if c.is_counter or c.is_removal or c.is_burn]
        holdback_bonus = 0.0
        if high_value_instants and game.current_phase == 'Main 2':
            holdback_bonus = 0.3  # Strong bias toward passing in Main 2 with instants

        # Post-combat sequencing: assess role for Main 1 vs Main 2 creature casting
        role = self._assess_role(game, player, opp)
        
        # Role-aware scoring adjustments
        tempo_adjust = 0.0
        ca_adjust = 0.0
        if role == 'aggro':
            tempo_adjust = 0.2  # Aggro prioritizes tempo — deploy threats fast
        elif role == 'control':
            ca_adjust = 0.15  # Control prioritizes card advantage
            holdback_bonus += 0.1  # Control prefers holding up mana
        
        # Late-game tempo boost — encourage aggressive play to prevent stalls
        if game.turn_count >= 15:
            tempo_adjust += 0.15  # Push toward action in late game

        best_action = None
        best_score = -float('inf')

        for action in castable:
            tempo = self._evaluate_tempo(game, player, action)
            ca = self._evaluate_card_advantage(game, player, action)
            vca = self._evaluate_virtual_card_advantage(game, player, action)

            # Look-ahead is expensive — only do it for high-value spells
            if self.look_ahead_depth > 0 and action['type'] == 'announce_cast':
                la = self._evaluate_look_ahead(game, player, action)
            else:
                la = 0.0

            score = ((tempo + tempo_adjust) * TEMPO_WEIGHT +
                     (ca + ca_adjust) * CA_WEIGHT +
                     vca * VCA_WEIGHT +
                     la * LOOKAHEAD_WEIGHT)

            # POST-COMBAT SEQUENCING: In Main 1, penalize non-priority creatures
            # Priority: haste, mana dorks, ETB creatures (they benefit from Main 1).
            # Others deferred to Main 2. Aggro role exempted — speed > information.
            if (game.current_phase == 'Main 1' and 
                action['type'] == 'announce_cast' and 
                action['card'].is_creature and
                not action['card'].has_haste and
                not getattr(action['card'], 'is_mana_dork', False) and
                not action['card'].etb_effect and
                role != 'aggro'):
                score -= 0.15  # Moderate penalty pushes below pass threshold
            
            # Removal priority boost when facing threats
            if (action['type'] == 'announce_cast' and 
                action['card'].is_removal and not action['card'].is_board_wipe):
                opp_threats = sum(1 for c in game.battlefield.cards 
                    if c.controller == opp and c.is_creature and (c.power or 0) >= 3)
                if opp_threats:
                    score += 0.1 * opp_threats  # More threats = more removal value
            
            # Race-awareness: play to the board state
            my_power = self._board_power(game, player)
            opp_power = self._board_power(game, opp)
            life_diff = player.life - opp.life
            
            if my_power > opp_power and life_diff > 0:
                # Winning the race → keep deploying threats
                if action['type'] == 'announce_cast' and action['card'].is_creature:
                    score += 0.08  # Small boost to stay aggressive
            elif opp_power > my_power + 5 or life_diff < -5:
                # Losing the race → prioritize answers
                if action['type'] == 'announce_cast':
                    card = action['card']
                    if card.is_removal or card.is_counter or card.is_board_wipe:
                        score += 0.12  # Boost defensive plays
                    if card.is_lifegain:
                        score += 0.1  # Lifegain helps stabilize

            if score > best_score:
                best_score = score
                best_action = action

        # Only cast if it's actually worth doing (score > threshold)
        pass_threshold = 0.05 + holdback_bonus
        if best_action and best_score > pass_threshold:
            return best_action

        return {'type': 'pass'}
