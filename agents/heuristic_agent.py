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
import random


class HeuristicAgent(BaseAgent):
    """AI agent using hand-tuned heuristics for competitive play.

    Makes decisions based on board state analysis: creature power comparisons,
    life total racing, keyword evaluation, and threat assessment. Handles
    stack interaction (counter-spells) and keyword-aware combat (flying,
    menace, deathtouch, trample, protection, gang-blocking).
    """

    def get_action(self, game, player) -> dict:
        """Choose the best action using the priority-based heuristic system."""
        legal = game.get_legal_actions()
        if not legal: return {'type': 'pass'}

        # Calculate opponent from player parameter, not game.opponent
        # (game.opponent is relative to active player, which may not be us)
        opp = game.players[(game.players.index(player) + 1) % 2]

        # === STACK RESPONSE: When opponent cast a spell, we get priority ===
        if len(game.stack) > 0:
            # Check if WE have instants to respond with
            instants = [a for a in legal if a['type'] == 'cast_spell' and 
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
            
            # No meaningful response — pass to let stack resolve
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
                attackers = self._choose_attackers(game, player, action['candidates'], opp)
                return {'type': 'declare_attackers', 'attackers': attackers}
        
        # 4. Play Land
        for action in legal:
            if action['type'] == 'play_land': return action

        # 5. Cast Spells Priority System

        # A. Lethal Burn?
        burn_spells = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_burn]
        for action in burn_spells:
            if opp.life <= 5: 
                game.log_event(f"  → {player.name}: going for lethal burn ({opp.name} at {opp.life}hp)")
                return action

        # B. Board wipes when outnumbered
        wipe_spells = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_board_wipe]
        if wipe_spells:
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
            if len(opp_creatures) >= len(my_creatures) + 2:
                game.log_event(f"  → {player.name}: board wipe (outnumbered {len(my_creatures)} vs {len(opp_creatures)})")
                return wipe_spells[0]

        # C. Removal (If opponent has threats — includes fight, bounce)
        removal_spells = [a for a in legal if a['type'] == 'cast_spell' and 
                         (a['card'].is_removal or a['card'].is_fight or a['card'].is_bounce) 
                         and not a['card'].is_board_wipe]
        if removal_spells:
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            if opp_creatures:
                best_threat = max(opp_creatures, key=lambda c: (c.power or 0))
                game.log_event(f"  → {player.name}: removal on {best_threat.name} ({best_threat.power}/{best_threat.toughness} threat)")
                return removal_spells[0]

        # D. Mill spells when opponent's library is low
        mill_spells = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_mill]
        if mill_spells:
            if len(opp.library) <= 20:  # Mill is effective when library is small
                game.log_event(f"  → {player.name}: milling {opp.name} ({len(opp.library)} cards left)")
                return mill_spells[0]

        # E. Creatures — prefer kicked versions, creatures with evasion
        creatures = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_creature]
        if creatures:
            def creature_priority(action):
                c = action['card']
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
                if hasattr(c, 'static_effect') and c.static_effect:
                    score += 3
                if hasattr(c, 'death_effect') and c.death_effect:
                    score += 1.5
                # Prefer kicked versions
                if action.get('kicked'):
                    score += 3
                return score
            creatures.sort(key=creature_priority, reverse=True)
            chosen = creatures[0]['card']
            kick_label = ' (kicked!)' if creatures[0].get('kicked') else ''
            game.log_event(f"  → {player.name}: deploying {chosen.name} ({chosen.power}/{chosen.toughness}){kick_label}")
            return creatures[0]
        
        # F. Loyalty Abilities (Planeswalkers)
        loyalty_actions = [a for a in legal if a['type'] == 'loyalty_ability']
        if loyalty_actions:
            # Prefer +N abilities when safe, -N for removal/draw when needed
            plus_abilities = [a for a in loyalty_actions if a['ability']['cost'] > 0]
            minus_abilities = [a for a in loyalty_actions if a['ability']['cost'] < 0]
            
            opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
            if minus_abilities and opp_creatures:
                # Use minus ability when opponent has threats
                return minus_abilities[0]
            elif plus_abilities:
                return plus_abilities[0]
            elif loyalty_actions:
                return loyalty_actions[0]

        # G. Activated Abilities (tap/sacrifice)
        abilities = [a for a in legal if a['type'] == 'activate_ability']
        if abilities:
            damage_abilities = [a for a in abilities if 'damage' in a.get('ability', {}).get('description', '').lower()]
            if damage_abilities and opp.life <= 10:
                game.log_event(f"  → {player.name}: activating {damage_abilities[0]['card'].name}")
                return damage_abilities[0]
            
            draw_abilities = [a for a in abilities if 'draw' in a.get('ability', {}).get('description', '').lower()]
            if draw_abilities:
                return draw_abilities[0]
            
            non_sac = [a for a in abilities if not a.get('ability', {}).get('cost_sacrifice')]
            if non_sac:
                return non_sac[0]

        # H. Buff spells — only if we have creatures
        buff_spells = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_buff]
        if buff_spells:
            my_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
            if my_creatures:
                return buff_spells[0]

        # I. Proliferate — when we have countered permanents
        prolif_spells = [a for a in legal if a['type'] == 'cast_spell' and a['card'].is_proliferate]
        if prolif_spells:
            countered = [c for c in game.battlefield.cards if c.controller == player and c.counters]
            if countered:
                return prolif_spells[0]

        # J. Draw / Other spells (including mill when no better option)
        other_spells = [a for a in legal if a['type'] == 'cast_spell']
        if other_spells:
            return other_spells[0]

        # K. Cycling — when mana-flooded or low-value hand
        cycle_actions = [a for a in legal if a['type'] == 'cycle']
        if cycle_actions:
            lands_in_play = sum(1 for c in game.battlefield.cards if c.controller == player and c.is_land)
            if lands_in_play >= 5 or len(player.hand) >= 5:
                game.log_event(f"  → {player.name}: cycling {cycle_actions[0]['card'].name} (filtering)")
                return cycle_actions[0]

        return {'type': 'pass'}
    
    def _choose_attackers(self, game, player, candidates, opp):
        """Smart attacking: all-out if ahead, hold back blockers if behind."""
        opp_creatures = [c for c in game.battlefield.cards 
                        if c.controller != player and c.is_creature and not c.tapped]
        my_creatures = candidates[:]
        
        # If opponent has no untapped creatures, attack with everything
        if not opp_creatures:
            if my_creatures:
                names = ', '.join(f"{c.name}({c.power}/{c.toughness})" for c in my_creatures)
                game.log_event(f"  → {player.name}: all-out attack (no blockers) — {names}")
            return my_creatures
        
        # If we have evasive creatures (flying, menace), always attack with them
        attackers = []
        remaining = []
        for c in my_creatures:
            if c.has_flying and not any(b.can_block_flyer for b in opp_creatures):
                attackers.append(c)  # Unblockable flyer
            elif c.has_trample and (c.power or 0) > max((b.toughness or 0) for b in opp_creatures):
                attackers.append(c)  # Will trample through
            elif c.has_menace and len(opp_creatures) < 2:
                attackers.append(c)  # Can't be blocked (menace, not enough blockers)
            elif hasattr(c, 'has_protection_from') and c.has_protection_from:
                attackers.append(c)  # Protection makes it hard to block
            else:
                remaining.append(c)
        
        # For the rest, attack if total power > their total toughness (race mentality)
        my_total = sum(max(0, c.power or 0) for c in remaining)
        opp_total = sum(max(0, c.power or 0) for c in opp_creatures)
        
        if my_total > opp_total or player.life > opp.life + 5:
            attackers.extend(remaining)
            if attackers:
                game.log_event(f"  → {player.name}: aggressive attack (power advantage or life lead)")
        else:
            # Leave the biggest toughness creature as blocker, attack with rest
            remaining.sort(key=lambda c: c.toughness or 0, reverse=True)
            if len(remaining) > 1:
                attackers.extend(remaining[1:])
                game.log_event(f"  → {player.name}: cautious attack (holding back {remaining[0].name} as blocker)")
        
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
        
        # Sort attackers by threat level (power, evasion)
        sorted_attackers = sorted(attackers, key=lambda a: (a.power or 0) + (2 if a.has_trample else 0), reverse=True)
        
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
                
                # Deathtouch makes any block a trade
                if blk.has_deathtouch:
                    if blk_toughness > att_power:
                        score = 100  # Free kill!
                    else:
                        score = 50  # Trade
                
                # Free eat (we survive, they die)
                elif blk_power >= att_toughness and att_power < blk_toughness:
                    score = 80
                
                # Trade (both die)
                elif blk_power >= att_toughness and att_power >= blk_toughness:
                    score = 30  # OK trade
                
                # Chump block big stuff to protect life
                elif att_power >= 4 and player.life <= att_power + 3:
                    score = 20  # Chump to survive
                
                if score > best_score:
                    best_score = score
                    best_blocker = blk
            
            if best_blocker and best_score > 0:
                blocks[att.id] = [best_blocker]
                remaining_blockers.remove(best_blocker)
            elif att_power >= 4 and len(valid_blockers) >= 2:
                # Gang-block: combine two small creatures to kill a big threat
                valid_blockers.sort(key=lambda c: (c.power or 0), reverse=True)
                b1, b2 = valid_blockers[0], valid_blockers[1]
                combined_power = (b1.power or 0) + (b2.power or 0)
                if combined_power >= att_toughness:
                    blocks[att.id] = [b1, b2]
                    remaining_blockers.remove(b1)
                    remaining_blockers.remove(b2)
                
        return blocks
