"""Bo3 — Best-of-Three match system with sideboarding.

Plays a best-of-3 match between two decks. Between games 1→2 and 2→3,
each player can swap cards between their maindeck and sideboard.

Usage:
    from engine.bo3 import Bo3Match
    match = Bo3Match(deck_a, deck_b, agent_cls=HeuristicAgent)
    result = match.play()
    # result = {'winner': 'Player 1', 'score': [2, 1], 'games': [...]}
"""

from typing import List, Optional, Type
from engine.deck import Deck
from engine.player import Player
from engine.game import Game
from engine.card import Card
from engine.zone import Zone
import copy
import logging

logger = logging.getLogger(__name__)


class Bo3Match:
    """Best-of-Three match with sideboarding between games."""
    
    def __init__(self, deck_a: Deck, deck_b: Deck, agent_cls=None, 
                 sideboard_a: List[Card] = None, sideboard_b: List[Card] = None,
                 max_turns: int = 100):
        self.deck_a = deck_a
        self.deck_b = deck_b
        self.agent_cls = agent_cls
        self.sideboard_a = sideboard_a or []
        self.sideboard_b = sideboard_b or []
        self.max_turns = max_turns
        self.games_played = []
        self.wins = [0, 0]  # [player_a_wins, player_b_wins]
    
    # ── T2: Play/Draw Decision ───────────────────────────────────
    def _choose_play_or_draw(self, loser_deck: Deck, winner_deck: Deck) -> str:
        """Loser of previous game chooses play or draw based on archetype.
        
        Returns 'play' or 'draw'.
        """
        # Classify the loser's deck archetype
        creatures = sum(1 for c in loser_deck.maindeck if c.is_creature)
        total_nonland = sum(1 for c in loser_deck.maindeck if not c.is_land)
        if total_nonland == 0:
            return 'play'
        creature_ratio = creatures / max(1, total_nonland)
        
        # Aggro always wants to go first (tempo advantage)
        if creature_ratio > 0.60:
            return 'play'
        
        # Control may prefer draw (extra card)
        removal = sum(1 for c in loser_deck.maindeck if c.is_removal or c.is_burn or c.is_counter)
        removal_ratio = removal / max(1, total_nonland)
        if creature_ratio < 0.35 and removal_ratio > 0.20:
            return 'draw'
        
        # Midrange defaults to play
        return 'play'
    
    def play(self) -> dict:
        """Play a best-of-3 match. Returns match result dict."""
        for game_num in range(1, 4):
            if max(self.wins) >= 2:
                break  # Match decided
            
            # Create fresh players for each game
            # T2: In games 2-3, loser of previous game chooses play/draw
            on_play_first = True  # Default: deck_a goes first
            if game_num > 1 and self.games_played:
                prev = self.games_played[-1]
                if prev['winner'] == 'Player 2':
                    # Player 1 lost, they choose
                    choice = self._choose_play_or_draw(self.deck_a, self.deck_b)
                    on_play_first = (choice == 'play')  # P1 stays first if they choose play
                elif prev['winner'] == 'Player 1':
                    # Player 2 lost, they choose
                    choice = self._choose_play_or_draw(self.deck_b, self.deck_a)
                    on_play_first = (choice != 'play')  # P2 goes first if they choose play
                logger.info(f"Game {game_num}: {'Player 1' if on_play_first else 'Player 2'} chose to go first")
            
            if on_play_first:
                p1 = Player(f"Player 1", copy.deepcopy(self.deck_a))
                p2 = Player(f"Player 2", copy.deepcopy(self.deck_b))
            else:
                p1 = Player(f"Player 2", copy.deepcopy(self.deck_b))
                p2 = Player(f"Player 1", copy.deepcopy(self.deck_a))
            
            # Load sideboards with card tracking from previous game
            if game_num > 1:
                prev_game = self.games_played[-1] if self.games_played else None
                cards_seen_by_p1 = prev_game.get('p2_cards_seen', set()) if prev_game else set()
                cards_seen_by_p2 = prev_game.get('p1_cards_seen', set()) if prev_game else set()
                self._apply_sideboard(p1, self.sideboard_a, game_num, prev_game, cards_seen_by_p1)
                self._apply_sideboard(p2, self.sideboard_b, game_num, prev_game, cards_seen_by_p2)
            
            # Create and play the game
            game = Game([p1, p2])
            
            # Use provided agent or import default
            if self.agent_cls:
                try:
                    agent = self.agent_cls()
                except TypeError:
                    agent = self.agent_cls(game)
            else:
                from agents.heuristic_agent import HeuristicAgent
                agent = HeuristicAgent()
            
            game.start_game()
            
            turns = 0
            while not game.game_over and turns < self.max_turns:
                actions = game.get_legal_actions()
                if not actions:
                    game.advance_phase()
                    continue
                # Use agent's get_action with proper game + player context
                priority = game.players[game.priority_player_index]
                if hasattr(agent, 'get_action'):
                    chosen = agent.get_action(game, priority)
                elif hasattr(agent, 'choose_action'):
                    chosen = agent.choose_action(actions)
                else:
                    chosen = actions[0]
                game.apply_action(chosen)
                if game.current_phase == "Cleanup":
                    turns += 1
            
            # Extract cards seen from game log for sideboard intelligence
            import re
            p1_cards_seen = set()
            p2_cards_seen = set()
            for log_line in game.log:
                # Parse cast/resolve events: "T3: Player 1 casts Lightning Bolt"
                cast_match = re.search(r'(Player [12]) casts (.+?)(?:\s*\(|$)', log_line)
                if cast_match:
                    player_name, card_name = cast_match.groups()
                    card_name = card_name.strip()
                    if 'Player 1' in player_name:
                        p1_cards_seen.add(card_name)
                    else:
                        p2_cards_seen.add(card_name)
                # Also track creatures/permanents that entered
                etb_match = re.search(r'(Player [12]).*enters.*battlefield.*: (.+?)(?:\s*\(|$)', log_line)
                if etb_match:
                    player_name, card_name = etb_match.groups()
                    card_name = card_name.strip()
                    if 'Player 1' in player_name:
                        p1_cards_seen.add(card_name)
                    else:
                        p2_cards_seen.add(card_name)
            
            # Record result
            winner_idx = None
            if game.winner:
                winner_idx = game.players.index(game.winner)
                self.wins[winner_idx] += 1
            
            self.games_played.append({
                'game_num': game_num,
                'winner': game.winner.name if game.winner else 'Draw',
                'turns': turns,
                'p1_life': p1.life,
                'p2_life': p2.life,
                'p1_cards_seen': p1_cards_seen,
                'p2_cards_seen': p2_cards_seen
            })
            
            logger.info(f"Game {game_num}: {game.winner.name if game.winner else 'Draw'} "
                       f"(P1: {p1.life} life, P2: {p2.life} life, {turns} turns, "
                       f"P1 cards seen: {len(p1_cards_seen)}, P2 cards seen: {len(p2_cards_seen)})")
        
        # Determine match winner
        match_winner = None
        if self.wins[0] > self.wins[1]:
            match_winner = "Player 1"
        elif self.wins[1] > self.wins[0]:
            match_winner = "Player 2"
        
        return {
            'winner': match_winner,
            'score': list(self.wins),
            'games': self.games_played
        }
    
    def _apply_sideboard(self, player: Player, sideboard: List[Card], game_num: int,
                          prev_game: dict = None, cards_seen: set = None) -> List[dict]:
        """Matchup-aware sideboarding based on opponent's archetype AND cards seen.
        
        Enhanced: Uses specific cards observed in previous game for targeted boarding.
        """
        swaps_log = []
        if not sideboard:
            return swaps_log
        
        cards_seen = cards_seen or set()
        
        # Load sideboard into player's sideboard zone
        for card in sideboard:
            sb_copy = copy.deepcopy(card)
            sb_copy.controller = player
            player.sideboard.add(sb_copy)
        
        sb_cards = list(player.sideboard.cards)
        if not sb_cards:
            return
        
        # Classify opponent archetype from previous game log + cards seen
        opp_archetype = self._classify_opponent(prev_game, player, cards_seen)
        
        # Detect specific threat patterns from cards seen
        seen_graveyard_recursion = any(kw in name.lower() for name in cards_seen 
                                       for kw in ['reanimate', 'unearth', 'persist', 'undying', 'return'])
        seen_artifacts = any(kw in name.lower() for name in cards_seen 
                            for kw in ['artifact', 'treasure', 'clue', 'food'])
        seen_enchantments = any(kw in name.lower() for name in cards_seen 
                               for kw in ['enchantment', 'aura', 'shrine'])
        seen_burn = sum(1 for name in cards_seen 
                       if any(kw in name.lower() for kw in ['bolt', 'burn', 'shock', 'strike', 'blaze']))
        
        # Score sideboard cards for matchup
        def sb_priority(card):
            """Higher = more desirable to board in for this matchup."""
            score = self._card_value(card)
            if opp_archetype == 'aggro' or opp_archetype == 'burn':
                if card.is_removal: score += 8
                if card.is_board_wipe: score += 10
                if card.is_lifegain: score += 5
                if card.has_lifelink: score += 3
                if card.has_deathtouch: score += 3
                if card.is_creature and (card.toughness or 0) >= 4: score += 3
            elif opp_archetype == 'control':
                if card.is_creature and card.has_flash: score += 6
                if card.is_creature and card.has_hexproof: score += 5
                if card.is_creature and card.has_haste: score += 4
                if card.is_draw: score += 4
                if card.is_counter: score += 3
                if card.is_creature and (card.power or 0) >= 3: score += 3
            else:  # midrange
                if card.is_removal: score += 5
                if card.is_draw: score += 4
                if card.is_board_wipe: score += 4
                if card.is_creature and card.etb_effect: score += 3
            
            # Cards-seen-specific bonuses
            card_text = (card.oracle_text or '').lower()
            if seen_graveyard_recursion and ('exile' in card_text and 'graveyard' in card_text):
                score += 7  # Board in graveyard hate
            if seen_artifacts and ('destroy' in card_text and 'artifact' in card_text):
                score += 6  # Board in artifact removal
            if seen_enchantments and ('destroy' in card_text and 'enchantment' in card_text):
                score += 6  # Board in enchantment removal
            if seen_burn >= 3 and card.is_lifegain:
                score += 5  # Extra value for lifegain vs heavy burn
            return score
        
        # Score maindeck cards for this matchup (lower = more cuttable)
        def md_weakness(card):
            """Lower = more desirable to board out for this matchup."""
            if card.is_land: return 1000  # Never board out lands
            score = self._card_value(card)
            if opp_archetype == 'aggro':
                # Cut slow cards, mill, discard
                if card.is_mill: score -= 5
                if card.is_discard: score -= 3
                if card.is_counter: score -= 2
            elif opp_archetype == 'control':
                # Cut creature-dependent cards, board wipes, life gain
                if card.is_lifegain: score -= 4
                if card.is_board_wipe: score -= 3
                if card.is_creature and (card.power or 0) <= 1: score -= 3
            else:
                if card.is_lifegain: score -= 2
                if card.is_mill: score -= 3
            return score
        
        # Sort sideboard by priority (best first), library by weakness (worst first)
        sb_cards.sort(key=sb_priority, reverse=True)
        lib_cards = sorted(player.library.cards, key=md_weakness)
        
        opp_deck = self.deck_b if player.name == "Player 1" else self.deck_a
        opp_name = getattr(opp_deck, 'name', opp_archetype)
        
        # Swap up to min(sideboard_size, 5) cards
        swaps = min(5, len(sb_cards), len(lib_cards))
        swapped = 0
        for i in range(swaps):
            sb_card = sb_cards[i]
            weak_card = lib_cards[i]
            # Don't board out if sideboard card is worse than maindeck card
            if sb_priority(sb_card) <= md_weakness(weak_card):
                continue
            if weak_card in player.library.cards and sb_card in player.sideboard.cards:
                player.library.cards.remove(weak_card)
                player.sideboard.add(weak_card)
                player.sideboard.cards.remove(sb_card)
                player.library.add(sb_card)
                swapped += 1
                swaps_log.append({
                    'opp_archetype': opp_name,
                    'card_in': sb_card.name,
                    'card_out': weak_card.name
                })
        
        logger.info(f"Sideboarding: {player.name} swapped {swapped} cards (vs {opp_name})")
        return swaps_log
    
    def _classify_opponent(self, prev_game: dict, player: Player, cards_seen: set = None) -> str:
        """Classify opponent archetype from deck data AND cards actually played.
        
        Uses cards_seen from previous game for more accurate classification.
        """
        if not prev_game:
            return 'midrange'  # Default assumption
        
        cards_seen = cards_seen or set()
        opp_deck = self.deck_b if player.name == "Player 1" else self.deck_a
        deck_name = getattr(opp_deck, 'name', '').lower()
        
        # Cards-seen-based classification (highest accuracy)
        if cards_seen:
            seen_lower = {name.lower() for name in cards_seen}
            burn_cards = {'lightning bolt', 'shock', 'lava spike', 'rift bolt', 'skullcrack',
                         'searing blaze', 'boros charm', 'goblin guide', 'monastery swiftspear'}
            control_cards = {'counterspell', 'mana leak', 'force of will', 'cryptic command',
                           'supreme verdict', 'wrath of god', 'teferi', 'jace'}
            combo_cards = {'storm', 'tendrils', 'grapeshot', 'ritual', 'scapeshift',
                          'through the breach', 'emrakul', 'aetherflux'}
            
            burn_hits = len(seen_lower & burn_cards)
            control_hits = len(seen_lower & control_cards)
            combo_hits = len(seen_lower & combo_cards)
            
            if burn_hits >= 2:
                return 'burn'
            if combo_hits >= 2:
                return 'combo'
            if control_hits >= 2:
                return 'control'
        
        # Deck name keywords
        burn_keywords = ['burn', 'red deck', 'rdw', 'sligh', 'prowess']
        if any(kw in deck_name for kw in burn_keywords):
            return 'burn'
        
        affinity_keywords = ['affinity', 'artifact', 'metalcraft', 'modular']
        if any(kw in deck_name for kw in affinity_keywords):
            return 'artifacts'
        
        tokens_keywords = ['token', 'go wide', 'swarm', 'weenie']
        if any(kw in deck_name for kw in tokens_keywords):
            return 'tokens'
        
        mill_keywords = ['mill', 'dredge', 'self-mill']
        if any(kw in deck_name for kw in mill_keywords):
            return 'mill'
        
        ramp_keywords = ['ramp', 'tron', 'big mana', 'eldrazi']
        if any(kw in deck_name for kw in ramp_keywords):
            return 'ramp'
        
        combo_keywords = ['combo', 'storm', 'scapeshift', 'through the breach']
        if any(kw in deck_name for kw in combo_keywords):
            return 'combo'
        
        # Fallback: card composition analysis
        creatures = sum(1 for c in opp_deck.maindeck if c.is_creature)
        removal = sum(1 for c in opp_deck.maindeck if c.is_removal or c.is_burn)
        burn_spells = sum(1 for c in opp_deck.maindeck if c.is_burn)
        counters = sum(1 for c in opp_deck.maindeck if c.is_counter)
        total_nonland = sum(1 for c in opp_deck.maindeck if not c.is_land)
        
        if total_nonland == 0:
            return 'midrange'
        
        creature_ratio = creatures / max(1, total_nonland)
        burn_ratio = burn_spells / max(1, total_nonland)
        counter_ratio = counters / max(1, total_nonland)
        removal_ratio = removal / max(1, total_nonland)
        
        if burn_ratio > 0.30:
            return 'burn'
        if creature_ratio > 0.65:
            return 'aggro'
        if creature_ratio < 0.35 and (removal_ratio > 0.2 or counter_ratio > 0.1):
            return 'control'
        return 'midrange'
    
    @staticmethod
    def _card_value(card: Card) -> float:
        """Estimate card value for sideboarding decisions."""
        value = 0
        if card.is_land:
            return 100  # Never board out lands
        if card.is_creature:
            value += (card.power or 0) + (card.toughness or 0)
            # Evasion and combat keywords
            if card.has_flying: value += 2
            if card.has_trample: value += 1
            if card.has_lifelink: value += 1.5
            if card.has_deathtouch: value += 1.5
            if card.has_first_strike or card.has_double_strike: value += 1.5
            if card.has_haste: value += 1.5
            if card.has_hexproof: value += 1
            if card.has_menace: value += 1
            if card.has_vigilance: value += 0.5
            if card.has_reach: value += 0.5
            if card.has_flash: value += 1
            if card.has_ward: value += 1
            if card.has_prowess: value += 1
            if card.has_indestructible: value += 2
            if card.has_protection: value += 1.5
            if card.has_infect: value += 2
        if card.is_removal: value += 5
        if card.is_burn: value += 4
        if card.is_draw: value += 3
        if card.is_counter: value += 4
        if card.is_board_wipe: value += 5
        if card.is_buff: value += 1.5
        if card.is_lifegain: value += 1
        if card.is_discard: value += 2
        if card.is_mill: value += 1.5
        # Effects and triggers
        if card.etb_effect: value += 2
        if card.death_effect: value += 2
        if card.attack_trigger: value += 2
        if getattr(card, 'cast_trigger', None): value += 1.5
        if card.static_effect: value += 3
        if getattr(card, 'is_mana_dork', False): value += 2
        if card.combat_damage_trigger: value += 1.5
        if getattr(card, 'tap_ability_effect', None): value += 2
        if getattr(card, 'sacrifice_effect', None): value += 1.5
        if getattr(card, 'broad_trigger', None): value += 1
        if getattr(card, 'enchantment_trigger', None): value += 2
        if getattr(card, 'equip_bonus', None): value += 2
        if card.upkeep_effect: value += 1.5
        if card.landfall_effect: value += 2
        if getattr(card, 'has_self_pump', False): value += 1
        value += getattr(card, 'self_pump_power', 0) * 0.5
        if getattr(card, 'has_text_ability', False): value += 0.5
        if getattr(card, 'has_morph', False): value += 1
        # Alternative costs
        if card.cycling_cost: value += 0.5
        if card.flashback_cost: value += 1.5
        if card.evoke_cost: value += 1
        if card.unearth_cost: value += 1
        return value
