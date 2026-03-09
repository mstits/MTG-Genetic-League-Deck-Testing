"""HeuristicAgent — Smart game-playing agent with threat assessment.

Implements a priority-based decision system that evaluates board state,
opponent threats, and card synergies to make intelligent plays.

Decision priority (in get_action):
    0. Stack responses — counter big threats, board wipes, removal
    1. Declare blockers (keyword-aware: flying, menace, deathtouch, protection)
    2. Crew vehicles before combat
    3. Declare attackers (evasion-aware, holds back blockers when behind)
    4. Play land
    5. Cast spells in priority order:
       A. Lethal burn  B. Board wipes  C. Removal  D. Mill
       E. Creatures (scored by keywords)  F. Planeswalkers  G. Abilities
       H. Buffs  I. Proliferate  J. Other spells  K. Cycling
    6. Pass
"""

from .base_agent import BaseAgent


class HeuristicAgent(BaseAgent):
    """AI agent using hand-tuned heuristics for competitive play.

    Makes decisions based on board state analysis: creature power comparisons,
    life total racing, keyword evaluation, and threat assessment. Handles
    stack interaction (counter-spells) and keyword-aware combat (flying,
    menace, deathtouch, trample, protection, gang-blocking).
    """

    def __init__(self, name: str = "HeuristicAgent", playstyle: str = None):
        super().__init__(name)
        self.playstyle = playstyle

    # ── T1: Instant-Speed Hold-Up Intelligence ──────────────────────
    def _should_hold_mana(self, game, player, role):
        """Decide if we should pass to hold mana open for instant-speed interaction.
        
        Returns True if holding mana is more valuable than casting sorcery-speed.
        Aggro decks are exempted — they always want to deploy threats.
        """
        if role == 'aggro':
            return False
        if game.turn_count <= 2:
            return False  # T1-2: always deploy
        
        instants_in_hand = [c for c in player.hand.cards
                           if (c.is_instant or c.has_flash) and
                           player.can_pay_cost(c.cost, game)]
        if not instants_in_hand:
            return False
        
        high_value = any(c.is_counter or c.is_removal or c.is_burn for c in instants_in_hand)
        if not high_value:
            return False

        # In Main 2, always hold if we have interaction
        if game.current_phase == 'Main 2':
            return True

        # In Main 1, hold if the best sorcery-speed play is low value
        if game.current_phase == 'Main 1':
            castable = [c for c in player.hand.cards
                       if not (c.is_instant or c.has_flash) and not c.is_land
                       and player.can_pay_cost(c.cost, game)]
            if not castable:
                return True  # Nothing to cast anyway, hold mana
            # If best sorcery play is a small creature with no ETB, hold
            best_sorcery = max(castable, key=lambda c: (c.power or 0) + (3 if c.etb_effect else 0) + (2 if c.has_haste else 0))
            sorcery_value = (best_sorcery.power or 0) + (3 if best_sorcery.etb_effect else 0) + (2 if best_sorcery.has_haste else 0)
            if sorcery_value < 4:
                return True  # Low-value sorcery play, better to hold
        return False

    # ── T5: Damage Clock / Racing Awareness ─────────────────────────
    def _calculate_clock(self, game, player, opp):
        """Calculate damage clocks: how many turns to kill each player.
        
        Returns (my_clock, opp_clock) where lower = faster kill.
        """
        import math
        my_creatures = [c for c in game.battlefield.cards
                       if c.controller == player and c.is_creature and not c.tapped]
        opp_creatures = [c for c in game.battlefield.cards
                        if c.controller == opp and c.is_creature and not c.tapped]
        
        my_attack_power = sum(max(0, c.power or 0) for c in my_creatures)
        opp_attack_power = sum(max(0, c.power or 0) for c in opp_creatures)
        
        my_clock = math.ceil(opp.life / max(my_attack_power, 1))  # turns to kill opp
        opp_clock = math.ceil(player.life / max(opp_attack_power, 1))  # turns opp kills us
        
        return my_clock, opp_clock

    # ── T3: Artifact Synergy Awareness ─────────────────────────────
    def _count_artifacts(self, game, player):
        """Count non-land artifacts controlled by player on the battlefield."""
        return sum(1 for c in game.battlefield.cards
                   if c.controller == player and 'Artifact' in (c.type_line or '')
                   and not c.is_land)

    def _assess_role(self, game, player, opp):
        """Determine our strategic role: 'aggro', 'control', or 'midrange'.
        
        Per Mike Flores' "Who's the Beatdown?" — identifying your role
        is the single most important strategic decision in Magic.
        
        T5 fix: considers hand composition — burn-heavy = aggro,
        counter-heavy = control, regardless of board state.
        """
        if getattr(self, 'playstyle', None):
            return self.playstyle
        
        # Hand composition can force a role regardless of board state
        burn_in_hand = sum(1 for c in player.hand.cards if c.is_burn)
        counters_in_hand = sum(1 for c in player.hand.cards if c.is_counter)
        
        # 3+ burn spells targeting player = Burn archetype (always aggro)
        if burn_in_hand >= 3:
            return 'aggro'
        # 3+ counterspells = Control (hold up interaction)
        if counters_in_hand >= 3:
            return 'control'
        
        my_creatures = [c for c in game.battlefield.cards 
                       if c.controller == player and c.is_creature]
        opp_creatures = [c for c in game.battlefield.cards 
                        if c.controller == opp and c.is_creature]
        
        my_power = sum(max(0, c.power or 0) for c in my_creatures)
        opp_power = sum(max(0, c.power or 0) for c in opp_creatures)
        
        # Deck speed: average CMC of spells in hand
        from engine.player import Player
        hand_cmcs = [Player._parse_cmc(c.cost) for c in player.hand.cards if c.cost and not c.is_land]
        avg_hand_cmc = sum(hand_cmcs) / max(len(hand_cmcs), 1)
        
        # Scoring: positive = aggro, negative = control
        role_score = 0
        
        # Board position
        if my_power > opp_power + 3: role_score += 2
        elif my_power > opp_power: role_score += 1
        elif opp_power > my_power + 3: role_score -= 2
        elif opp_power > my_power: role_score -= 1
        
        # Life differential (behind on life = need to be aggro)
        if player.life < opp.life - 5: role_score += 1
        elif player.life > opp.life + 5: role_score -= 1
        
        # Deck speed
        if avg_hand_cmc < 2.0: role_score += 1  # Low curve = aggro
        elif avg_hand_cmc > 3.5: role_score -= 1  # High curve = control
        
        # Interaction in hand (counters, removal = control)
        interaction = sum(1 for c in player.hand.cards 
                        if c.is_counter or c.is_removal or c.is_board_wipe)
        if interaction >= 2: role_score -= 1
        
        # Evasive creature advantage (flyers, menace, unblockable)
        my_evasive = sum(1 for c in my_creatures 
                        if c.has_flying or c.has_menace or c.is_unblockable or 
                        c.has_shadow or c.has_skulk)
        opp_evasive = sum(1 for c in opp_creatures 
                         if c.has_flying or c.has_menace or c.is_unblockable)
        if my_evasive > opp_evasive + 1: role_score += 1
        
        # Burn spells in hand = aggro (can close out games)
        if burn_in_hand >= 2: role_score += 1
        
        # Creature count advantage
        if len(my_creatures) > len(opp_creatures) + 2: role_score += 1
        elif len(opp_creatures) > len(my_creatures) + 2: role_score -= 1
        
        # Late game: shift toward aggro to close out (avoid draw by turn limit)
        if game.turn_count >= 12: role_score += 1
        if game.turn_count >= 18: role_score += 1
        
        if role_score >= 2: return 'aggro'
        elif role_score <= -1: return 'control'
        return 'midrange'

    def _evaluate_hidden_interaction(self, game, opp):
        """Estimate the probability of opponent interaction based on open mana."""
        score = 0
        opp_lands = [c for c in game.battlefield.cards if c.controller == opp and c.is_land and not getattr(c, 'is_tapped', False)]
        
        open_u = sum(1 for c in opp_lands if 'Island' in getattr(c, 'type_line', '') or 'U' in getattr(c, 'produces', getattr(c, 'colors', [])))
        open_b = sum(1 for c in opp_lands if 'Swamp' in getattr(c, 'type_line', '') or 'B' in getattr(c, 'produces', getattr(c, 'colors', [])))
        open_r = sum(1 for c in opp_lands if 'Mountain' in getattr(c, 'type_line', '') or 'R' in getattr(c, 'produces', getattr(c, 'colors', [])))
        open_w = sum(1 for c in opp_lands if 'Plains' in getattr(c, 'type_line', '') or 'W' in getattr(c, 'produces', getattr(c, 'colors', [])))
        
        # Counterspell risk: UU or 1U up
        if open_u >= 2: score += 5
        elif open_u >= 1: score += 2
        
        # Removal/Trick risk
        if open_b >= 1 or open_r >= 1 or open_w >= 1:
            if len(opp_lands) >= 2: score += 3
            else: score += 1
            
        return score

    def get_action(self, game, player) -> dict:
        """Choose the best action using the priority-based heuristic system."""
        legal = game.get_legal_actions()
        if not legal: return {'type': 'pass'}

        # Calculate opponent from player parameter
        opp = game.players[(game.players.index(player) + 1) % 2]
        
        # ── T9: Richer Win Probability Heuristic ─────────────────────
        my_board = [c for c in game.battlefield.cards if c.controller == player]
        opp_board = [c for c in game.battlefield.cards if c.controller == opp]
        
        my_score = player.life
        my_score += len(player.hand.cards) * 2.0  # Hand size = options
        my_score += sum((c.power or 0) * 1.5 + (c.toughness or 0) for c in my_board if c.is_creature)
        # Evasive power counts extra (guaranteed damage)
        my_score += sum((c.power or 0) * 0.5 for c in my_board if c.is_creature and (c.has_flying or c.is_unblockable or c.has_menace))
        # Board wipe in hand is a potential complete swing
        if any(c.is_board_wipe for c in player.hand.cards):
            my_score += 8.0
        # Interaction in hand adds safety
        my_score += sum(1.5 for c in player.hand.cards if c.is_counter or c.is_removal)
        
        opp_score = opp.life
        opp_score += len(opp.hand.cards) * 2.0
        opp_score += sum((c.power or 0) * 1.5 + (c.toughness or 0) for c in opp_board if c.is_creature)
        opp_score += sum((c.power or 0) * 0.5 for c in opp_board if c.is_creature and (c.has_flying or c.is_unblockable or c.has_menace))
        
        total_score = my_score + opp_score
        wp = my_score / total_score if total_score > 0 else 0.5
        
        self.desperation_mode = (wp < 0.20)
        
        if self.desperation_mode and not getattr(self, '_logged_desperation_this_turn', False):
            game.log_event(f"  → {player.name} enters DESPERATION MODE (WP: {wp:.2f})")
            self._logged_desperation_this_turn = True
        elif not self.desperation_mode:
            self._logged_desperation_this_turn = False

        # --- PENDING CAST STATE MACHINE (CR 601.2) ---
        if game.pending_cast:
            pc = game.pending_cast
            card = pc.card
            
            if pc.state == 'choices':
                kick_actions = [a for a in legal if a['type'] == 'choose_kicker']
                if kick_actions and player.can_pay_cost(card.cost + card.kicker_cost, game):
                    return kick_actions[0]
                
                os_actions = [a for a in legal if a['type'] == 'choose_offspring']
                if os_actions and player.can_pay_cost(card.cost + card.offspring_cost, game):
                    return os_actions[0]
                
                x_actions = [a for a in legal if a['type'] == 'choose_x']
                if x_actions:
                    max_x = max(a['value'] for a in x_actions)
                    return next(a for a in x_actions if a['value'] == max_x)
                    
                mode_actions = [a for a in legal if a['type'] == 'choose_mode']
                if mode_actions:
                    return mode_actions[0]
                    
                sac_actions = [a for a in legal if a['type'] in ('choose_sacrifice', 'choose_emerge')]
                if sac_actions:
                    sac_actions.sort(key=lambda a: (a['target'].power or 0) + (a['target'].toughness or 0))
                    return sac_actions[0]
                
                return next(a for a in legal if a['type'] == 'done_choices')
                
            elif pc.state == 'targeting':
                target_actions = [a for a in legal if a['type'] == 'declare_target']
                if target_actions:
                    if card.is_removal or card.is_bounce:
                        target_actions.sort(key=lambda a: getattr(a['target'], 'power', 0) if hasattr(a['target'], 'power') else 0, reverse=True)
                        game.log_event(f"  → Targeting {getattr(target_actions[0]['target'], 'name', target_actions[0]['target'])} for removal")
                        return target_actions[0]
                    elif card.is_burn:
                        if opp.life <= 5:
                            player_t = [a for a in target_actions if a['target'] == opp]
                            if player_t: return player_t[0]
                        target_actions.sort(key=lambda a: getattr(a['target'], 'power', 0) if hasattr(a['target'], 'power') else 0, reverse=True)
                        return target_actions[0]
                    elif card.is_buff or card.is_aura:
                        target_actions.sort(key=lambda a: getattr(a['target'], 'power', 0) if hasattr(a['target'], 'power') else 0, reverse=True)
                        return target_actions[0]
                    else:
                        return target_actions[0]
                return next(a for a in legal if a['type'] == 'done_targeting')
                
            elif pc.state == 'mana':
                pay_action = [a for a in legal if a['type'] == 'pay_costs']
                if pay_action:
                    return pay_action[0]
                mana_actions = [a for a in legal if a['type'] == 'activate_mana']
                if mana_actions:
                    return mana_actions[0]
                cancel = [a for a in legal if a['type'] == 'cancel_cast']
                if cancel: return cancel[0]

        # === STACK RESPONSE: When opponent cast a spell, we get priority ===
        if len(game.stack) > 0:
            # Check if WE have instants to respond with
            instants = [a for a in legal if a['type'] == 'announce_cast' and 
                       (a['card'].is_instant or a['card'].has_flash)]
            
            # Counter their spell?
            counters = [a for a in instants if a['card'].is_counter]
            if counters:
                target = game.stack.cards[-1]
                # Only counter actual spells, not triggered abilities
                from engine.card import StackItem
                if not isinstance(target, StackItem):
                    # Counter high-value targets
                    if target.is_creature and (target.base_power or 0) >= 3:
                        game.log_event(f"  → {player.name} responds: countering {target.name} (big threat)")
                        return counters[0]
                    if target.is_board_wipe:
                        game.log_event(f"  → {player.name} responds: countering {target.name} (board wipe!)")
                        return counters[0]
                    if target.is_removal:
                        game.log_event(f"  → {player.name} responds: countering {target.name} (removal)")
                        return counters[0]
                    if target.is_planeswalker:
                        game.log_event(f"  → {player.name} responds: countering {target.name} (planeswalker)")
                        return counters[0]
                    if target.is_burn and player.life <= 10:
                        game.log_event(f"  → {player.name} responds: countering {target.name} (burn at low life)")
                        return counters[0]
            
            # No meaningful response — pass to let stack resolve
            
            # Instant-speed removal: kill a threatening creature
            removal_instants = [a for a in instants if a['card'].is_removal]
            if removal_instants:
                opp_creatures = [c for c in game.battlefield.cards 
                                if c.controller != player and c.is_creature]
                threats = [c for c in opp_creatures if (c.power or 0) >= 3]
                if threats:
                    game.log_event(f"  → {player.name} responds: instant removal on threat")
                    return removal_instants[0]
            
            # Instant burn: calculate exact lethal across all available burn
            burn_instants = [a for a in instants if a['card'].is_burn]
            if burn_instants:
                total_burn = sum(getattr(a['card'], 'damage_amount', 2) for a in burn_instants)
                if total_burn >= opp.life:
                    game.log_event(f"  → {player.name} responds: burn for lethal ({total_burn} dmg vs {opp.life} life)")
                    return burn_instants[0]
                # Also fire burn at low life for pressure
                if opp.life <= 8:
                    game.log_event(f"  → {player.name} responds: burn for pressure ({opp.life} life)")
                    return burn_instants[0]
            
            # Combat trick: buff during combat with lethal math
            if game.current_phase in ('Declare Attackers', 'Declare Blockers', 
                                       'First Strike Damage', 'Combat Damage',
                                       'Attackers', 'Blockers'):
                buff_instants = [a for a in instants if a['card'].is_buff]
                if buff_instants:
                    # Check if buff would push attackers past lethal
                    my_attackers = [c for c in game.combat_attackers 
                                   if c.controller == player] if game.combat_attackers else []
                    attack_power = sum(max(0, c.power or 0) for c in my_attackers)
                    buff_power = getattr(buff_instants[0]['card'], 'etb_power', 2)
                    if attack_power + buff_power >= opp.life:
                        game.log_event(f"  → {player.name} responds: lethal combat trick ({attack_power}+{buff_power} >= {opp.life})")
                        return buff_instants[0]
                    # Otherwise still play buff if we have attacking creatures
                    if my_attackers:
                        game.log_event(f"  → {player.name} responds: combat trick")
                        return buff_instants[0]
            
            # Flash creatures: deploy as surprise blockers or at opponent's end step
            flash_creatures = [a for a in instants if a['card'].is_creature and a['card'].has_flash]
            if flash_creatures:
                # Sort by power — deploy biggest impact first
                flash_creatures.sort(key=lambda a: (a['card'].base_power or 0), reverse=True)
                # During combat — surprise blocker
                if game.current_phase in ('Declare Attackers', 'Declare Blockers',
                                           'Attackers', 'Blockers', 'Begin Combat'):
                    game.log_event(f"  → {player.name} responds: flash creature {flash_creatures[0]['card'].name} as blocker")
                    return flash_creatures[0]
                # Deploy during any opponent turn phase that isn't their main
                is_opp_turn = game.active_player_index != game.players.index(player)
                if is_opp_turn:
                    game.log_event(f"  → {player.name} responds: end-step flash {flash_creatures[0]['card'].name}")
                    return flash_creatures[0]
            
            # Instant-speed tap abilities during opponent's end step
            if game.current_phase in ('End', 'Cleanup'):
                tap_actions = [a for a in legal if a['type'] == 'tap_ability']
                if tap_actions:
                    for ta in tap_actions:
                        text = (ta['card'].oracle_text or '').lower()
                        if 'draw' in text or 'damage' in text or 'create' in text or 'search' in text:
                            game.log_event(f"  → {player.name} responds: end-step tap/search {ta['card'].name}")
                            return ta
            
            return {'type': 'pass'}

        # 1. Blockers (If defending)
        for action in legal:
            if action['type'] == 'declare_blockers':
                blocks = self._calculate_blocks(game, player, action['candidates'], action['attackers'])
                if blocks:
                    game.log_event(f"  → {player.name} blocking strategy: {len(blocks)} assignments")
                return {'type': 'declare_blockers', 'blocks': blocks}

        # 2. Crew Vehicles BEFORE combat (so they can attack)
        crew_actions = [a for a in legal if a['type'] == 'crew']
        if crew_actions and game.current_phase == 'Main 1':
            best = max(crew_actions, key=lambda a: (a['vehicle'].power or 0))
            game.log_event(f"  → {player.name}: crewing {best['vehicle'].name}")
            return best

        # 3. Attack (smart — leave back valuable blockers, consider evasion)
        for action in legal:
            if action['type'] == 'declare_attackers':
                interaction_risk = self._evaluate_hidden_interaction(game, opp)
                attackers = self._choose_attackers(game, player, action['candidates'], opp)
                
                # Probabilistic "Playing Around Tricks": if opponent has open mana indicating removal/tricks
                if interaction_risk >= 3:
                     # Filter out non-evasive attacks if we're not definitively winning the race
                     # This reduces blindly swinging into Settle the Wreckage or open removal.
                     safe_attackers = []
                     for a in attackers:
                         if a.has_flying or a.has_menace or a.has_trample or a.has_indestructible or a.has_ward or getattr(a, 'has_protection', False):
                             safe_attackers.append(a)
                         elif player.life > opp.life + 5 or (a.power or 0) >= 4:
                             safe_attackers.append(a) # Big threat or far ahead, force the trick
                     # Only commit safe attackers if there's high trick risk
                     # unless there's only 1 blocker and we can overwhelm
                     opp_blockers = sum(1 for c in game.battlefield.cards if c.controller == opp and c.is_creature and not getattr(c, 'is_tapped', False))
                     if opp_blockers > 0 and len(attackers) <= opp_blockers + 1:
                         attackers = safe_attackers

                return {'type': 'declare_attackers', 'attackers': attackers}
        
        # ── T3: Smart Land Sequencing ────────────────────────────────
        land_actions = [a for a in legal if a['type'] == 'play_land']
        if land_actions:
            if len(land_actions) == 1:
                return land_actions[0]
            
            import re as _re
            from engine.player import Player
            
            # Gather hand spells and their color requirements + CMC
            hand_spells = [c for c in player.hand.cards if c.cost and not c.is_land]
            
            # Current lands in play and what they produce
            in_play_colors = set()
            lands_in_play = 0
            for card in game.battlefield.cards:
                if card.controller == player and card.is_land:
                    lands_in_play += 1
                    for c in getattr(card, 'produces', []):
                        in_play_colors.add(c)
            
            next_turn_mana = lands_in_play + 1  # After playing this land
            
            # Find spells castable next turn (CMC <= next_turn_mana)
            next_turn_spells = [c for c in hand_spells 
                               if Player._parse_cmc(c.cost) <= next_turn_mana]
            
            # Check if we have an immediate play this turn
            has_immediate_play = any(
                Player._parse_cmc(c.cost) <= lands_in_play 
                for c in hand_spells if not c.is_land
            )
            
            def land_score(action):
                land = action['card']
                produces = set(getattr(land, 'produces', []))
                is_tapped = getattr(land, 'enters_tapped', False)
                score = 0
                
                # How many next-turn spells does this land enable?
                for spell in next_turn_spells:
                    needed = set(_re.findall(r'\{([WUBRG])\}', spell.cost))
                    available = in_play_colors | produces
                    if needed <= available:
                        score += 3  # This land enables casting this spell
                    elif needed & produces:  # Partially helps
                        score += 1
                
                # Missing color bonus: if this fills a gap
                needed_colors = set()
                for spell in hand_spells:
                    for c in _re.findall(r'\{([WUBRG])\}', spell.cost):
                        needed_colors.add(c)
                missing = needed_colors - in_play_colors
                if produces & missing:
                    score += 5  # Fills a missing color — high priority
                
                # Tapped vs untapped logic
                if is_tapped:
                    if has_immediate_play:
                        score -= 3  # We have something to cast NOW, untapped is better
                    else:
                        score += 1  # No immediate play, tapped land is "free"
                else:
                    if has_immediate_play:
                        score += 2  # Untapped land lets us cast now
                
                return score
            
            best_land = max(land_actions, key=land_score)
            return best_land

        # ── T1: Consolidated Instant-Speed Hold-Up ──────────────────
        if self._should_hold_mana(game, player, role):
            game.log_event(f"  → {player.name}: holding mana open for instant-speed interaction")
            return {'type': 'pass'}

        # === POST-COMBAT SEQUENCING ===
        # Pro-level play: cast non-haste creatures in Main 2 (after combat)
        # to deny the opponent blocking information. Only cast haste creatures
        # and pre-combat setup (removal, buffs) in Main 1.
        role = self._assess_role(game, player, opp)
        interaction_risk = self._evaluate_hidden_interaction(game, opp)
        available_mana = player.available_mana(game) if hasattr(player, 'available_mana') else 0

        # === PROBABILISTIC PLAY: BAITING ===
        # If Counterspell risk is high (e.g. they hold UU), sequence a low-value spell first to draw it out.
        if interaction_risk >= 5 and available_mana >= 3 and game.current_phase in ('Main 1', 'Main 2'):
            affordable_spells = [a for a in legal if a['type'] == 'announce_cast' 
                                 and getattr(player, 'can_pay_cost', lambda x,y: True)(a['card'].cost, game)]
            if len(affordable_spells) >= 2:
                from engine.player import Player
                # Sort spells purely by cost (cheapest first)
                affordable_spells.sort(key=lambda a: Player._parse_cmc(a['card'].cost or ''))
                cheapest = affordable_spells[0]
                expensive = affordable_spells[-1]
                
                c_cmc = Player._parse_cmc(cheapest['card'].cost or '')
                e_cmc = Player._parse_cmc(expensive['card'].cost or '')
                
                # If we can afford both, and one is significantly cheaper/less impact, cast the bait first
                if cheapest != expensive and c_cmc + e_cmc <= available_mana:
                    if not cheapest['card'].is_burn: # Don't bait with burn if going for face
                        game.log_event(f"  → {player.name} [PROBABILISTIC]: Baiting potential counterspell with {cheapest['card'].name} first.")
                        return cheapest

        # 5. Cast Spells Priority System

        # A. Lethal Burn?
        burn_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_burn]
        for action in burn_spells:
            if opp.life <= 5: 
                game.log_event(f"  → {player.name}: going for lethal burn ({opp.name} at {opp.life}hp)")
                return action

        # A2. Emergency lifegain — when at critically low life
        if player.life <= 5:
            lifegain_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_lifegain]
            if lifegain_spells:
                game.log_event(f"  → {player.name}: emergency lifegain ({player.life}hp)")
                return lifegain_spells[0]

        # B. Board wipes when outnumbered, outpowered, or facing lethal
        wipe_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_board_wipe]
        if wipe_spells:
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
            opp_power = sum(max(0, c.power or 0) for c in opp_creatures)
            my_power = sum(max(0, c.power or 0) for c in my_creatures)
            my_indestructible = sum(1 for c in my_creatures if c.has_indestructible)
            
            should_wipe = False
            # Outnumbered or outpowered
            if len(opp_creatures) >= len(my_creatures) + 2 or opp_power >= my_power + 5:
                should_wipe = True
            # Facing lethal: opponent can kill us on their next attack
            if opp_power >= player.life and len(opp_creatures) > 0:
                should_wipe = True
            # We have indestructible creatures that survive — asymmetric wipe!
            if my_indestructible > 0 and len(opp_creatures) >= 2:
                should_wipe = True
            
            if should_wipe:
                game.log_event(f"  → {player.name}: board wipe (our:{my_power}pw vs opp:{opp_power}pw, life:{player.life})")
                return wipe_spells[0]

        # C. Removal (If opponent has threats — includes fight, bounce)
        removal_spells = [a for a in legal if a['type'] == 'announce_cast' and 
                         (a['card'].is_removal or a['card'].is_fight or a['card'].is_bounce) 
                         and not a['card'].is_board_wipe]
        if removal_spells:
            # Collect ALL opponent permanents as potential targets (not just creatures)
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            opp_noncreatures = [c for c in game.battlefield.cards 
                               if c.controller == opp and not c.is_creature and not c.is_land
                               and ('Enchantment' in (c.type_line or '') or 
                                    'Artifact' in (c.type_line or '') or 
                                    c.is_planeswalker)]
            all_targets = opp_creatures + opp_noncreatures
            if all_targets:
                # Filter out hexproof targets
                valid_targets = [c for c in all_targets if not c.has_hexproof]
                # Filter out indestructible for destroy-based removal
                spell = removal_spells[0]['card']
                if not getattr(spell, 'is_exile_removal', False):
                    valid_targets = [c for c in valid_targets if not c.has_indestructible]
                if valid_targets:
                    # Score threats by comprehensive value — works for ALL permanent types
                    def threat_score(c):
                        """Score opponent threats for removal targeting — unified for all permanents."""
                        s = 0
                        # Creature combat stats
                        if c.is_creature:
                            s = (c.power or 0) + (c.toughness or 0) * 0.3
                            if c.has_flying: s += 1.5
                            if c.has_trample: s += 1
                            if c.has_lifelink: s += 1
                            if c.has_deathtouch: s += 1
                            if c.has_first_strike or c.has_double_strike: s += 1
                            if c.has_hexproof: s += 1
                            if c.has_menace: s += 1
                            if c.etb_effect: s += 0.5
                            if c.combat_damage_trigger: s += 1
                            if getattr(c, 'has_self_pump', False): s += 0.5
                            s += getattr(c, 'self_pump_power', 0) * 0.3
                            if getattr(c, 'sacrifice_effect', None): s += 1
                            if getattr(c, 'enchantment_trigger', None): s += 1
                            if c.attack_trigger: s += 3.0 + (c.power or 0) * 0.5
                            # Mana dorks — HIGH priority early
                            if getattr(c, 'is_mana_dork', False):
                                s += 8.0 if game.turn_count <= 3 else 2.0
                        
                        # NON-CREATURE PERMANENTS — enchantments, artifacts, planeswalkers
                        if not c.is_creature:
                            # Base value for non-creature permanents
                            s = 3.0  # Higher base than a vanilla 2/2
                            
                            # Planeswalkers are high-priority removal targets
                            if c.is_planeswalker:
                                s += 8.0
                                loyalty = getattr(c, 'loyalty', 0)
                                s += loyalty * 0.5  # Higher loyalty = closer to ultimate
                        
                        # Universal scoring — works for all permanent types
                        # Static effects are #1 removal priority (value engines)
                        if c.static_effect:
                            s += 5.0
                        # Tap abilities with draw are devastating
                        tap_text = (getattr(c, 'tap_ability_effect', None) or '')
                        if isinstance(tap_text, dict):
                            tap_text = str(tap_text)
                        if tap_text and 'draw' in str(tap_text).lower():
                            s += 6.0
                        elif getattr(c, 'tap_ability_effect', None):
                            s += 2.0
                        # Upkeep effects = repeatable value engines
                        if c.upkeep_effect: s += 3.0
                        # Enchantment triggers
                        if getattr(c, 'enchantment_trigger', None): s += 3.0
                        
                        # Role-based adjustments
                        if role == 'control':
                            if c.upkeep_effect: s += 2.0
                            if c.is_creature and (c.power or 0) >= 4: s += 2.0
                        elif role == 'aggro':
                            if c.is_creature:
                                s += (c.toughness or 0) * 0.5
                                if c.has_lifelink: s += 3.0
                                if c.has_first_strike: s += 1.0
                        
                        # Early-game toughness bonus
                        if c.is_creature and game.turn_count <= 4:
                            s += (c.toughness or 0) * 0.3
                        
                        # In Main 1, prioritize killing high-toughness blockers
                        if c.is_creature and game.current_phase == 'Main 1' and role != 'control':
                            s += (c.toughness or 0) * 0.2
                        return s
                    best_threat = max(valid_targets, key=threat_score)
                    # Multi-spell sequencing: pick cheapest effective removal
                    from engine.player import Player
                    available_mana = player.available_mana(game) if hasattr(player, 'available_mana') else 0
                    affordable_removal = [a for a in removal_spells 
                                         if player.can_pay_cost(a['card'].cost, game)]
                    if affordable_removal:
                        # --- INSTANT-SPEED HOLD UP ---
                        if game.current_phase in ('Main 1', 'Main 2') and player == game.active_player:
                            affordable_removal = [a for a in affordable_removal if 
                                not (a['card'].is_instant or a['card'].has_flash) or
                                (role == 'aggro' and game.current_phase == 'Main 1') or
                                threat_score(best_threat) >= 6.0
                            ]
                        
                        if affordable_removal:
                            other_castables = [a for a in legal if a['type'] == 'announce_cast' 
                                              and a not in affordable_removal
                                              and a['card'].cost]
                            def removal_value(a):
                                """Score removal spell value based on cost efficiency and effect type."""
                                r_cmc = Player._parse_cmc(a['card'].cost) if a['card'].cost else 0
                                remaining = available_mana - r_cmc
                                followup_bonus = 0
                                for other in other_castables:
                                    other_cmc = Player._parse_cmc(other['card'].cost) if other['card'].cost else 0
                                    if other_cmc <= remaining and other_cmc > 0:
                                        followup_bonus = max(followup_bonus, 2.0)
                                        break
                                base = 1.0
                                if getattr(a['card'], 'is_exile_removal', False): base = 1.5
                                return base + followup_bonus
                            
                            best_removal = max(affordable_removal, key=removal_value)
                            game.log_event(f"  → {player.name}: announcing removal for {best_threat.name}")
                            return best_removal

        # C2. Discard — strongest in early game when opponent has a full hand
        discard_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_discard]
        if discard_spells and game.turn_count <= 5 and len(opp.hand) >= 3:
            # RESTRICT targeted discard to Precombat_Main so we know their combat tricks
            if game.current_phase == 'Main 1':
                game.log_event(f"  → {player.name}: targeted discard on Main 1 ({opp.name} has {len(opp.hand)} cards)")
                return discard_spells[0]

        # D. Mill spells when opponent's library is low

        mill_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_mill]
        if mill_spells:
            if len(opp.library) <= 20:  # Mill is effective when library is small
                game.log_event(f"  → {player.name}: milling {opp.name} ({len(opp.library)} cards left)")
                return mill_spells[0]

        # E. Creatures — prefer evasion, mana efficiency
        creatures = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_creature]
        if creatures:
            # POST-COMBAT SEQUENCING: In Main 1, only cast haste creatures,
            # mana dorks (T1-T3 ramp), or ETB creatures (pre-combat value).
            # Defer other creatures to Main 2 (deny info to opponent).
            if game.current_phase == 'Main 1':
                main1_priority = [a for a in creatures if 
                    a['card'].has_haste or  # Can attack this turn
                    getattr(a['card'], 'is_mana_dork', False) or  # Ramp ASAP
                    a['card'].etb_effect  # Pre-combat ETB effects
                ]
                if main1_priority:
                    creatures = main1_priority
                elif role != 'aggro':
                    # Non-aggro decks defer creatures to Main 2
                    creatures = []
                # Aggro role: cast everything ASAP (speed > information)
            
            if creatures:
                available_mana = player.available_mana(game) if hasattr(player, 'available_mana') else 0
                
                def creature_priority(action):
                    """Evaluate creature value for casting based on stats, keywords, and mana curve fit."""
                    c = action['card']
                    
                    # CDA survival check: skip creatures that will immediately die
                    # to state-based actions (0 or negative toughness from CDAs)
                    cda = getattr(c, 'cda_type', '')
                    if cda == 'deaths_shadow':
                        # 13/13, gets -X/-X where X = your life
                        effective_toughness = (c.base_toughness or 13) - player.life
                        if effective_toughness <= 0:
                            return -999  # DOA — don't cast
                    elif cda == 'scourge_skyclaves':
                        # P/T = 20 - highest life total
                        highest_life = max(p.life for p in game.players)
                        if 20 - highest_life <= 0:
                            return -999
                    
                    score = (c.power or 0)
                    if c.has_flying: score += 2
                    if c.has_haste: score += 2
                    if c.has_lifelink: score += 1.5
                    if c.has_deathtouch: score += 1.5
                    if c.has_trample: score += 1
                    if c.has_first_strike or c.has_double_strike: score += 1.5
                    if c.etb_effect: score += 2
                    if c.has_menace: score += 1
                    if c.has_prowess: score += 1.5
                    if c.has_drawback: score -= 3
                    if hasattr(c, 'static_effect') and c.static_effect: score += 3
                    if hasattr(c, 'death_effect') and c.death_effect: score += 1.5
                    if c.has_ward: score += 1
                    if c.has_vigilance: score += 0.5
                    if c.has_flash: score += 1
                    if c.has_indestructible: score += 2
                    if getattr(c, 'has_protection', False): score += 1.5
                    if c.has_hexproof: score += 1
                    if getattr(c, 'tap_ability_effect', None): score += 1.5
                    if getattr(c, 'sacrifice_effect', None): score += 1
                    if getattr(c, 'has_self_pump', False): score += 0.5
                    score += getattr(c, 'self_pump_power', 0) * 0.5
                    if getattr(c, 'broad_trigger', None): score += 1
                    if c.attack_trigger: score += 1.5
                    if c.combat_damage_trigger: score += 1
                    if c.upkeep_effect: score += 1.5
                    if getattr(c, 'enchantment_trigger', None): score += 1
                    if c.landfall_effect: score += 1
                    if c.has_undying or c.has_persist: score += 1
                    
                    # Artifact synergy — boost artifact creatures when
                    # deck has artifact payoffs on board or in hand
                    if 'Artifact' in (c.type_line or ''):
                        artifact_count = self._count_artifacts(game, player)
                        # Affinity for artifacts: lower effective cost
                        if getattr(c, 'has_affinity', False):
                            score += min(artifact_count, 4) * 1.5
                        # Metalcraft bonus (3+ artifacts)
                        if artifact_count >= 2:  # Will be 3 after deploying this
                            score += 2.0
                        # Generic artifact synergy: other artifacts on board
                        if artifact_count >= 1:
                            score += 1.0
                    
                    if role == 'aggro':
                        if c.has_haste: score += 3.0
                        if c.has_trample: score += 2.0
                        if c.is_unblockable: score += 2.0
                    elif role == 'control':
                        if c.has_lifelink: score += 2.0
                        if c.etb_effect: score += 2.0
                        score += (c.toughness or 0) * 0.5
                    
                    actual_cost = c.cost
                    if actual_cost and available_mana > 0:
                        from engine.player import Player
                        spell_cmc = Player._parse_cmc(actual_cost)
                        if spell_cmc == available_mana: score += 3.0
                        elif spell_cmc == available_mana - 1: score += 1.5
                        elif available_mana > 3 and spell_cmc <= 1 and len(creatures) > 1: score -= 1.0
                        # Kicker bonus: prefer casting with kicker when affordable
                        if getattr(c, 'kicker_cost', ''):
                            kicker_cmc = Player._parse_cmc(c.kicker_cost)
                            if spell_cmc + kicker_cmc <= available_mana:
                                score += 2.0  # Strong bonus for kicking
                    
                    return score
                creatures.sort(key=creature_priority, reverse=True)
                # Filter out DOA creatures (scored -999)
                creatures = [a for a in creatures if creature_priority(a) > -900]
                if creatures:
                    chosen = creatures[0]['card']
                    game.log_event(f"  → {player.name}: deploying {chosen.name} ({chosen.power}/{chosen.toughness})")
                    return creatures[0]
        
        # E2. Artifact spells — deploy before creatures if deck has artifact synergies
        # 0-CMC artifacts (Ornithopter, Mox) should be deployed ASAP for Affinity/Metalcraft
        artifact_spells = [a for a in legal if a['type'] == 'announce_cast' and 
                          not a['card'].is_creature and 'Artifact' in (a['card'].type_line or '')]
        if artifact_spells:
            from engine.player import Player
            artifact_count = self._count_artifacts(game, player)
            # Prioritize 0-CMC artifacts and Equipment
            def artifact_score(a):
                c = a['card']
                s = 2.0  # Base artifact value
                cmc = Player._parse_cmc(c.cost) if c.cost else 0
                if cmc == 0: s += 5.0  # Free deployment = always good
                if 'Equipment' in (c.type_line or ''): s += 3.0
                if c.etb_effect: s += 2.0
                if c.static_effect: s += 3.0
                # Synergy with existing artifacts
                if artifact_count >= 2: s += 2.0  # Metalcraft territory
                return s
            artifact_spells.sort(key=artifact_score, reverse=True)
            best = artifact_spells[0]
            if artifact_score(best) > 3.0:  # Only if it's actually valuable
                game.log_event(f"  → {player.name}: deploying artifact {best['card'].name}")
                return best
        
        # F. Loyalty Abilities (Planeswalkers)
        loyalty_actions = [a for a in legal if a['type'] == 'loyalty_ability']
        if loyalty_actions:
            plus_abilities = [a for a in loyalty_actions if a['ability']['cost'] > 0]
            minus_abilities = sorted(
                [a for a in loyalty_actions if a['ability']['cost'] < 0],
                key=lambda a: a['ability']['cost']  # Most negative first (biggest effect)
            )
            zero_abilities = [a for a in loyalty_actions if a['ability']['cost'] == 0]
            
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            pw = loyalty_actions[0]['card']
            pw_loyalty = getattr(pw, 'loyalty_counters', 0) or 0
            
            # Ultimate: if we can afford a big minus ability, use it
            big_minus = [a for a in minus_abilities if abs(a['ability']['cost']) >= 5]
            if big_minus and pw_loyalty >= abs(big_minus[0]['ability']['cost']):
                return big_minus[0]
            
            # Minus to remove threats — but only if loyalty is healthy enough
            if minus_abilities and opp_creatures and pw_loyalty > 3:
                opp_threat = sum(max(0, c.power or 0) for c in opp_creatures)
                if opp_threat >= 4:  # Meaningful threats on board
                    return minus_abilities[0]
            
            # Plus to build loyalty toward ultimate
            if plus_abilities:
                return plus_abilities[0]
            
            # Zero abilities as alternative
            if zero_abilities:
                return zero_abilities[0]
            
            # Fallback: minus if plus isn't available
            if minus_abilities:
                return minus_abilities[0]

        # G. Activated Abilities (tap/sacrifice)
        abilities = [a for a in legal if a['type'] == 'activate_ability']
        if abilities:
            damage_abilities = [a for a in abilities if 'damage' in a.get('ability', {}).get('description', '').lower()]
            if damage_abilities and opp.life <= 10:
                game.log_event(f"  → {player.name}: activating {damage_abilities[0]['card'].name}")
                return damage_abilities[0]
            draw_abilities = [a for a in abilities if 'draw' in a.get('ability', {}).get('description', '').lower()]
            if draw_abilities: return draw_abilities[0]
            non_sac = [a for a in abilities if not a.get('ability', {}).get('cost_sacrifice')]
            if non_sac: return non_sac[0]
        
        # G2. Tap Abilities ({T}: effects) — context-aware activation
        tap_actions = [a for a in legal if a['type'] == 'tap_ability']
        if tap_actions:
            # High-value tap abilities: always activate
            for ta in tap_actions:
                text = (ta['card'].oracle_text or '').lower()
                if 'draw' in text or 'destroy' in text or 'exile' in text:
                    game.log_event(f"  → {player.name}: tap ability on {ta['card'].name}")
                    return ta
            # Medium-value tap abilities: context-dependent
            for ta in tap_actions:
                text = (ta['card'].oracle_text or '').lower()
                if 'damage' in text and opp.life <= 10:
                    game.log_event(f"  → {player.name}: tap for damage on {ta['card'].name}")
                    return ta
                if ('gain' in text and 'life' in text) and player.life <= 8:
                    game.log_event(f"  → {player.name}: tap for lifegain on {ta['card'].name}")
                    return ta
                if 'create' in text and 'token' in text:
                    game.log_event(f"  → {player.name}: tap for token on {ta['card'].name}")
                    return ta
                if 'counter' in text and 'put' in text:
                    game.log_event(f"  → {player.name}: tap for counter on {ta['card'].name}")
                    return ta
                if 'scry' in text or 'look at' in text:
                    game.log_event(f"  → {player.name}: tap for scry on {ta['card'].name}")
                    return ta
                # Defer fetchlands strictly to End Step of opponent unless mana is desperately needed
                if 'search' in text and ta['card'].is_land:
                    available_mana = player.available_mana(game) if hasattr(player, 'available_mana') else 0
                    if available_mana == 0 or (game.current_phase == 'End' and game.active_player_index != game.players.index(player)):
                         game.log_event(f"  → {player.name}: popping fetchland {ta['card'].name}")
                         return ta
            # Low-value tap abilities — activate in Main 2 (after combat)
            if game.current_phase in ('Main 2',):
                return tap_actions[0]
        
        # G3. Sacrifice Abilities — board-state aware
        sac_actions = [a for a in legal if a['type'] == 'sacrifice_ability']
        if sac_actions:
            # Defer Sacrifice fetchlands as well
            for sa in sac_actions:
                text = (sa['card'].oracle_text or '').lower()
                if 'search' in text and sa['card'].is_land:
                    available_mana = player.available_mana(game) if hasattr(player, 'available_mana') else 0
                    if available_mana == 0 or (game.current_phase == 'End' and game.active_player_index != game.players.index(player)):
                        game.log_event(f"  → {player.name}: cracking fetchland {sa['card'].name}")
                        return sa
            
            # Activate non-fetch sac abilities
            non_fetch_sac_actions = [a for a in sac_actions if not ('search' in (a['card'].oracle_text or '').lower() and a['card'].is_land)]
            if non_fetch_sac_actions:
                my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
                for sa in non_fetch_sac_actions:
                    text = (sa['card'].oracle_text or '').lower()
                    # Always sacrifice for draw (card advantage is king)
                    if 'draw' in text:
                        game.log_event(f"  → {player.name}: sacrificing {sa['card'].name} for draw")
                        return sa
                    # Sacrifice for damage/destroy when finishing opponent
                    if ('damage' in text or 'destroy' in text) and opp.life <= 10:
                        game.log_event(f"  → {player.name}: sacrificing {sa['card'].name} for effect")
                        return sa
                    # Sacrifice for tokens/create — if we have enough creatures
                    if ('create' in text and 'token' in text) and len(my_creatures) >= 3:
                        game.log_event(f"  → {player.name}: sacrificing {sa['card'].name} for tokens")
                        return sa
                    # Sacrifice for exile effect — high value removal
                    if 'exile' in text:
                        game.log_event(f"  → {player.name}: sacrificing {sa['card'].name} for exile")
                        return sa

        # H. Buff spells — only if we have creatures
        buff_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_buff]
        if buff_spells:
            my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
            if my_creatures:
                return buff_spells[0]

        # H2. Enchantments & Auras
        enchant_spells = [a for a in legal if a['type'] == 'announce_cast' and 
                         a['card'].is_enchantment and not a['card'].is_buff]
        if enchant_spells:
            my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
            # Cast auras if we have creatures to attach to
            aura_spells = [a for a in enchant_spells if a['card'].is_aura]
            if aura_spells and my_creatures:
                game.log_event(f"  → {player.name}: casting aura {aura_spells[0]['card'].name}")
                return aura_spells[0]
            # Cast non-aura enchantments with effects
            for a in enchant_spells:
                if not a['card'].is_aura:
                    return a

        # H3. Equip — attach equipment to best unequipped creature
        equip_actions = [a for a in legal if a['type'] == 'equip']
        if equip_actions:
            # Pick best equip target: prefer evasive creatures (flying/trample/menace)
            def equip_score(action):
                """Score equip targets, prioritizing creatures with evasion or combat keywords."""
                target = action.get('target')
                if not target: return 0
                s = (target.power or 0)
                if target.has_flying: s += 3
                if target.has_trample: s += 2
                if target.has_menace: s += 2
                if target.has_double_strike: s += 3
                if target.has_deathtouch: s += 1
                if target.has_lifelink: s += 1
                return s
            best_equip = max(equip_actions, key=equip_score)
            equip_name = best_equip.get('source', best_equip.get('card', None))
            equip_name = equip_name.name if equip_name else '?'
            game.log_event(f"  → {player.name}: equipping {equip_name}")
            return best_equip

        # I. Proliferate — when we have countered permanents
        prolif_spells = [a for a in legal if a['type'] == 'announce_cast' and a['card'].is_proliferate]
        if prolif_spells:
            countered = [c for c in game.battlefield.cards if c.controller == player and c.counters]
            if countered:
                return prolif_spells[0]

        # ── T1: Second hold-up checkpoint (before other spells) ──────
        # Already handled by _should_hold_mana() above, removed duplicate

        # J. Draw / Other spells — prioritize by type
        other_spells = [a for a in legal if a['type'] == 'announce_cast']
        if other_spells:
            # Prioritize draw spells (card advantage), but ONLY cast sorcery-speed draw
            # on our turn. Hold instant-speed draw for the opponent's end step.
            draw_spells = [a for a in other_spells if a['card'].is_draw]
            if draw_spells:
                if game.current_phase in ('Main 1', 'Main 2') and player == game.active_player:
                    sorcery_draw = [a for a in draw_spells if not (a['card'].is_instant or a['card'].has_flash)]
                    if sorcery_draw:
                        return sorcery_draw[0]
                else:
                    return draw_spells[0]
            # Planeswalkers are high priority — repeatable value
            pw_spells = [a for a in other_spells if a['card'].is_planeswalker]
            if pw_spells:
                return pw_spells[0]
            # Lifegain when low on life
            if player.life <= 10:
                lg_spells = [a for a in other_spells if a['card'].is_lifegain]
                if lg_spells:
                    return lg_spells[0]
            # Otherwise cast the best curve-fit spell
            from engine.player import Player
            available_mana = sum(1 for c in game.battlefield.cards 
                               if c.controller == player and c.is_land and not c.tapped)
            def spell_value(a):
                """Score non-creature spells based on mana efficiency and card type impact."""
                c = a['card']
                cmc = Player._parse_cmc(c.cost) if c.cost else 0
                # Prefer spells that use all our mana
                mana_efficiency = 3.0 if cmc == available_mana else 1.5 if cmc == available_mana - 1 else 0
                # Card type bonuses
                bonus = 0
                if c.is_creature: bonus += (c.power or 0) + (c.toughness or 0) * 0.3
                if c.is_removal: bonus += 3
                if c.etb_effect: bonus += 1
                return mana_efficiency + bonus
            other_spells.sort(key=spell_value, reverse=True)
            return other_spells[0]

        # K. Cycling — intelligent filtering
        cycle_actions = [a for a in legal if a['type'] == 'cycle']
        if cycle_actions:
            lands_in_play = sum(1 for c in game.battlefield.cards if c.controller == player and c.is_land)
            from engine.player import Player
            hand_cmcs = [Player._parse_cmc(c.cost) for c in player.hand.cards if c.cost and not c.is_land]
            uncastable = sum(1 for cmc in hand_cmcs if cmc > lands_in_play + 1)
            
            should_cycle = False
            # Mana-flooded: too many lands, need action
            if lands_in_play >= 5 or len(player.hand) >= 5:
                should_cycle = True
            # Mana-stuck: can't cast anything with current mana
            elif uncastable >= 2 and lands_in_play < 4:
                should_cycle = True
            # Late game: need to find answers
            elif game.turn_count >= 8 and len(player.hand) <= 2:
                should_cycle = True
            
            if should_cycle:
                game.log_event(f"  → {player.name}: cycling {cycle_actions[0]['card'].name} (filtering)")
                return cycle_actions[0]

        return {'type': 'pass'}
    
    def _choose_attackers(self, game, player, candidates, opp):
        """Aggressive attacking — in MTG, attacking is almost always correct.
        
        Key principles:
        - Attack by default. Only hold back when there's a clear reason not to.
        - The longer the game goes, the more aggressively we attack (stall-breaker).
        - Lifelink creatures should always attack (damage races in their favor).
        - Evasive creatures always attack.
        - Hold back at most 1 blocker when behind on board.
        """
        opp_creatures = [c for c in game.battlefield.cards 
                        if c.controller != player and c.is_creature and not c.tapped]
        my_creatures = candidates[:]
        
        if not my_creatures:
            return []

        # === ALPHA-STRIKE DETECTION ===
        total_power = sum(max(0, (c.power or 0) + getattr(c, 'self_pump_power', 0)) for c in my_creatures)
        
        # Calculate guaranteed (unblockable) damage
        guaranteed_damage = 0
        for c in my_creatures:
            power = (c.power or 0) + getattr(c, 'self_pump_power', 0)
            if power <= 0:
                continue
            if c.is_unblockable or c.has_shadow:
                guaranteed_damage += power
            elif c.has_flying and not any(getattr(b, 'can_block_flyer', False) for b in opp_creatures):
                guaranteed_damage += power
            elif c.has_menace and len(opp_creatures) < 2:
                guaranteed_damage += power
            elif hasattr(c, 'has_protection_from') and c.has_protection_from:
                guaranteed_damage += power
            elif c.has_trample and opp_creatures:
                best_blocker_tough = max(b.toughness or 0 for b in opp_creatures)
                guaranteed_damage += max(0, power - best_blocker_tough)
        
        # If guaranteed damage is lethal — ALL IN
        if guaranteed_damage >= opp.life:
            game.log_event(f"  → {player.name}: 💀 ALPHA STRIKE! ({guaranteed_damage} guaranteed vs {opp.life} life)")
            return my_creatures
        
        # If total power >= opponent life and we outnumber blockers — ALL IN
        if total_power >= opp.life and len(my_creatures) > len(opp_creatures):
            game.log_event(f"  → {player.name}: 💀 LETHAL SWING! ({total_power} power vs {opp.life} life)")
            return my_creatures
        
        # If opponent has no blockers — attack with everything
        if not opp_creatures:
            if my_creatures:
                game.log_event(f"  → {player.name}: all-out attack (no blockers)")
            return my_creatures
        
        # === CATEGORIZE CREATURES ===
        always_attack = []  # Evasive/lifelink — always attack
        regular = []        # Normal creatures — attack based on strategy
        
        for c in my_creatures:
            power = c.power or 0
            if power <= 0:
                continue  # Don't attack with 0-power creatures
            
            # Evasive creatures that can't be profitably blocked
            if c.has_flying and not any(getattr(b, 'can_block_flyer', False) for b in opp_creatures):
                always_attack.append(c)
            elif c.has_menace and len(opp_creatures) < 2:
                always_attack.append(c)
            elif hasattr(c, 'has_protection_from') and c.has_protection_from:
                always_attack.append(c)
            # Lifelink creatures should attack — the race favors them
            elif c.has_lifelink:
                always_attack.append(c)
            # Haste creatures — they're meant to be aggressive
            elif c.has_haste:
                always_attack.append(c)
            # Deathtouch creatures — opponent won't want to block favorably
            elif c.has_deathtouch:
                always_attack.append(c)
            # First strike / double strike — survives most combat trades
            elif c.has_first_strike or c.has_double_strike:
                always_attack.append(c)
            # Vigilance — can attack AND block, zero risk
            elif c.has_vigilance:
                always_attack.append(c)
            # Unblockable / shadow / skulk / intimidate — guaranteed damage
            elif c.is_unblockable or c.has_shadow or c.has_skulk or c.has_intimidate or c.has_fear:
                always_attack.append(c)
            # Combat damage trigger / attack trigger — value from attacking
            elif c.combat_damage_trigger or c.attack_trigger:
                always_attack.append(c)
            # Hold back: tap-ability creatures provide more value tapping than attacking
            elif getattr(c, 'tap_ability_effect', None) or getattr(c, 'is_mana_dork', False):
                pass  # Don't add to attackers — their tap ability is more valuable
            else:
                regular.append(c)
        
        attackers = list(always_attack)
        
        # === STALL-BREAKER: Escalate aggression as game progresses ===
        # NOTE: config.max_turns = 50, so timing is aligned to end cleanly
        turn = game.turn_count
        life_ahead = player.life > opp.life
        
        if turn >= 18:
            # Late game — attack with everything, board stalls must end
            attackers.extend(regular)
            if attackers:
                game.log_event(f"  → {player.name}: full assault (T{turn}, breaking stall)")
            return attackers
        
        if turn >= 14:
            # Mid-late game — attack with everything, hold back at most 1
            if len(regular) <= 1:
                attackers.extend(regular)
            else:
                # Hold back the single best blocker
                regular.sort(key=lambda c: (c.toughness or 0), reverse=True)
                attackers.extend(regular[1:])
            if attackers:
                game.log_event(f"  → {player.name}: aggressive push (T{turn})")
            return attackers
        
        if turn >= 10:
            # Mid-game — attack aggressively, hold at most 1 blocker
            if life_ahead or len(regular) <= 2:
                attackers.extend(regular)
            else:
                regular.sort(key=lambda c: (c.toughness or 0), reverse=True)
                attackers.extend(regular[1:])  # Hold best blocker only
            if attackers:
                game.log_event(f"  → {player.name}: mid-game push (T{turn})")
            return attackers
        
        # === EARLY/MID GAME (T1-9): Smart attacks ===
        my_total_power = sum(max(0, c.power or 0) for c in regular)
        opp_total_power = sum(max(0, c.power or 0) for c in opp_creatures)
        
        # Desperation Mode: We are losing badly, force high-variance
        if getattr(self, 'desperation_mode', False):
            attackers.extend(regular)
            game.log_event(f"  → {player.name}: DESPERATION ATTACK! (Sending {len(attackers)} creatures)")
            return attackers
        
        # ── T5: Damage Clock Racing ────────────────────────────────
        my_clock, opp_clock = self._calculate_clock(game, player, opp)
        if my_clock < opp_clock:
            # We're ahead in the race — be aggressive, send everything
            attackers.extend(regular)
            if attackers:
                game.log_event(f"  → {player.name}: racing! (our clock T{my_clock} vs opp T{opp_clock})")
            return attackers
        
        # If we have more total power or equal — attack with most, hold 1 back
        if my_total_power >= opp_total_power or player.life > opp.life:
            if len(regular) <= 2:
                # Small board — attack with all
                attackers.extend(regular)
            else:
                # Hold back best blocker, attack with rest
                regular.sort(key=lambda c: (c.toughness or 0), reverse=True)
                attackers.extend(regular[1:])
            if attackers:
                game.log_event(f"  → {player.name}: pressing advantage ({len(attackers)} attackers)")
            return attackers
        
        # Behind on board — still attack with evasive/lifelink (already in attackers)
        # Plus attack with any creature that has trample or > opponent's best blocker
        best_opp_toughness = max((b.toughness or 0) for b in opp_creatures) if opp_creatures else 0
        for c in regular:
            if c.has_trample and (c.power or 0) > best_opp_toughness:
                attackers.append(c)
            elif (c.power or 0) > best_opp_toughness:
                attackers.append(c)  # Can't be profitably blocked
        
        if attackers:
            game.log_event(f"  → {player.name}: selective attack ({len(attackers)} creatures)")
        elif my_creatures:
            # LAST RESORT: If we would attack with nothing, send at least 1
            # creature to chip away — doing nothing is always worse
            best_attacker = max(my_creatures, key=lambda c: (c.power or 0))
            if (best_attacker.power or 0) > 0:
                attackers.append(best_attacker)
                game.log_event(f"  → {player.name}: sending {best_attacker.name} to chip")
        
        return attackers
    def _calculate_blocks(self, game, player, potential_blockers, attackers):
        """Keyword-aware blocking with multi-blocking support (Rule 509.1a).
        
        - Multiple blockers can be assigned to the same attacker (gang-blocking)
        - Menace creatures need 2+ blockers (Rule 702.111b)
        - Flying creatures need flyers/reach to block (Rule 702.9b)
        - Deathtouch makes any trade favorable
        - Protection prevents blocking (Rule 702.16)
        """
        blocks = {}
        remaining_blockers = list(potential_blockers)
        
        # Sort attackers by threat level (power + pump potential + evasion)
        def attacker_threat(a):
            """Score an attacking creature's threat level to prioritize blocks."""
            threat = (a.power or 0) + getattr(a, 'self_pump_power', 0)
            if a.has_trample: threat += 2
            if a.has_lifelink: threat += 1
            if a.has_first_strike or a.has_double_strike: threat += 1
            if a.has_deathtouch: threat += 3  # Must be blocked or it kills everything
            return threat
        sorted_attackers = sorted(attackers, key=attacker_threat, reverse=True)
        
        for att in sorted_attackers:
            if not remaining_blockers: break
            
            att_power = att.power or 0
            att_toughness = att.toughness or 0
            
            # Filter blockers that CAN block this attacker
            valid_blockers = []
            for blk in remaining_blockers:
                # Flying check: only creatures with flying or reach can block flyers (Rule 702.9b)
                if att.has_flying and not blk.can_block_flyer:
                    continue
                # Protection check: can't be blocked by creatures with matching quality (Rule 702.16)
                if hasattr(att, 'is_protected_from') and att.is_protected_from(blk):
                    continue
                valid_blockers.append(blk)
            
            if not valid_blockers:
                continue
            
            # === MENACE: needs 2+ blockers (Rule 702.111b) ===
            if att.has_menace:
                if len(valid_blockers) < 2:
                    continue  # Can't block with fewer than 2
                # Try to find 2 blockers that can kill the attacker
                best_pair = None
                best_pair_score = -999
                for i, b1 in enumerate(valid_blockers):
                    for b2 in valid_blockers[i+1:]:
                        combined_power = (b1.power or 0) + (b2.power or 0)
                        combined_toughness = min(b1.toughness or 0, b2.toughness or 0)
                        score = 0
                        # Can we kill the attacker?
                        if combined_power >= att_toughness:
                            score += 50
                        # Do both survive?
                        if att_power < (b1.toughness or 0) + (b2.toughness or 0):
                            score += 30
                        # Deathtouch makes it easier
                        if b1.has_deathtouch or b2.has_deathtouch:
                            score += 40
                        # Big attacker is worth gang-blocking
                        score += att_power * 2
                        # Only worth it if attacker is dangerous enough
                        if score > best_pair_score and score > 20:
                            best_pair_score = score
                            best_pair = [b1, b2]
                
                if best_pair:
                    blocks[att.id] = best_pair
                    for b in best_pair:
                        remaining_blockers.remove(b)
                continue
            
            # === SINGLE or MULTI-blocker for non-menace ===
            # First try: find a single good blocker
            best_blocker = None
            best_score = -999
            
            for blk in valid_blockers:
                blk_power = blk.power or 0
                blk_toughness = blk.toughness or 0
                score = 0
                
                # Indestructible: always block — can't die from combat damage
                if blk.has_indestructible:
                    score = 95  # Basically free blocking forever
                    if blk_power >= att_toughness:
                        score = 100  # Kill the attacker too!
                
                # First-strike blocker: kills non-first-strike attacker before taking damage
                elif (blk.has_first_strike or blk.has_double_strike) and not (att.has_first_strike or att.has_double_strike):
                    if blk_power >= att_toughness:
                        score = 90  # Kill attacker before it deals damage!
                    elif blk.has_deathtouch:
                        score = 85  # Deathtouch + first strike = guaranteed kill before damage
                    else:
                        score = 40  # First strike, but can't kill — still advantageous
                
                # Attacker has first strike, we don't — worse trade for us
                elif (att.has_first_strike or att.has_double_strike) and not (blk.has_first_strike or blk.has_double_strike):
                    # We die before dealing damage if att kills us in first strike
                    if att_power >= blk_toughness:
                        if blk.death_effect:
                            score = 10  # At least we get death trigger
                        else:
                            score = -5  # We die for nothing
                    else:
                        # We survive first strike, then deal damage normally
                        if blk_power >= att_toughness:
                            score = 70  # We survive and kill
                        else:
                            score = 20  # We survive but can't kill

                # Deathtouch blocker: kills attacker regardless of size
                elif blk.has_deathtouch:
                    if blk_toughness > att_power:
                        score = 100  # Free kill!
                    else:
                        score = 50  # Trade — worth it if attacker is big
                        if att_power <= blk_power:
                            score = 10  # Trading deathtouch for equal creature — meh
                
                # Free eat (we survive, they die)
                elif blk_power >= att_toughness and att_power < blk_toughness:
                    score = 80
                
                # Trade (both die) — only worth it for big attackers
                elif blk_power >= att_toughness and att_power >= blk_toughness:
                    # Lifelink vs lifelink trade is pointless — both gain life, both die
                    if att.has_lifelink and blk.has_lifelink:
                        score = -10  # AVOID this trade — it just resets the board
                    elif att_power >= 4:
                        score = 35  # Trading with a big threat is worth it
                    elif att_power >= blk_power:
                        score = 15  # Even-ish trade, marginal
                    else:
                        score = 5  # Trading up for a smaller creature — not great
                
                # Chump block — for lethal/near-lethal, death triggers, or when desperate
                elif getattr(self, 'desperation_mode', False) or (att_power >= 3 and player.life <= att_power + 3):
                    # Trample: chump-blocking only reduces damage by blocker's toughness
                    if att.has_trample:
                        damage_prevented = blk_toughness
                        trample_through = att_power - damage_prevented
                        if damage_prevented >= 2 or blk.death_effect:
                            score = 15  # Worth chumping if we prevent significant damage
                        else:
                            score = 2  # Barely prevents anything, trample goes through
                    else:
                        score = 25  # No trample: chump prevents ALL damage
                    if blk.death_effect:
                        score += 15  # Dying with a death trigger = value
                
                # Death effect bonus: creatures with death triggers are better blockers
                if blk.death_effect and score > 0:
                    score += 10  # Death trigger value
                
                if score > best_score:
                    best_score = score
                    best_blocker = blk
            
            if best_blocker and best_score > 0:
                blocks[att.id] = [best_blocker]
                remaining_blockers.remove(best_blocker)
            elif att_power >= 5 and len(valid_blockers) >= 2:
                # Gang-block: combine two small creatures to kill a big threat
                valid_blockers.sort(key=lambda c: (c.power or 0), reverse=True)
                b1, b2 = valid_blockers[0], valid_blockers[1]
                combined_power = (b1.power or 0) + (b2.power or 0)
                if combined_power >= att_toughness:
                    blocks[att.id] = [b1, b2]
                    remaining_blockers.remove(b1)
                    remaining_blockers.remove(b2)
        
        # ── T5: Race-aware blocking override ────────────────────────
        # If we're ahead in the damage race, DON'T block (just race)
        if blocks and not getattr(self, 'desperation_mode', False):
            my_clock, opp_clock = self._calculate_clock(game, player, opp)
            if my_clock < opp_clock and player.life > 5:
                # We kill them first — skip blocking, take the damage
                game.log_event(f"  → {player.name}: not blocking — racing! (T{my_clock} vs T{opp_clock})")
                return {}
        
        return blocks
