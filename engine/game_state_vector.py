"""game_state_vector — Fixed-length numeric representation of MTG game state.

Converts the complete game state (hand, board, stack, graveyard, exile,
life, mana pool, phase) into a dense numeric vector for neural network
consumption. This is the bridge between the rules engine and the AI layer.

Vector layout (276 dimensions):
    [0:5]    Hand: color counts (WUBRG)
    [5:15]   Hand: CMC histogram (0-9+)
    [15:20]  Hand: type counts (creature, instant, sorcery, land, other)
    [20:25]  Board: own creature stats (count, total_power, total_tough, keyword_count, avg_cmc)
    [25:30]  Board: opp creature stats (same 5)
    [30:35]  Board: own noncreature permanents (artifacts, enchantments, planeswalkers, lands_untapped, lands_total)
    [35:40]  Board: opp noncreature permanents (same 5)
    [40:56]  Board: own keyword flags (16 keywords)
    [56:72]  Board: opp keyword flags (16 keywords)
    [72:82]  Stack: item counts, types, CMC histogram
    [82:92]  Graveyard: own summary (count, creature_count, spell_count, total_cmc, land_count, 5 padding)
    [92:102] Graveyard: opp summary (same 10)
    [102:106] Life: own_life, opp_life, life_diff, life_ratio
    [106:116] Mana: available by color (WUBRGC) + total + floating + lands_untapped + ratio
    [116:129] Phase: one-hot encoding (13 phases)
    [129:133] Turn: turn_number, is_active_player, cards_in_library, opp_cards_in_library
    [133:143] Threat assessment: removal_in_hand, counters_in_hand, burn_in_hand, draw_in_hand, etc.
    [143:276] Extended features (zone encoding, synergy signals, etc.)
"""

try:
    import jax
    import jax.numpy as jnp
    HAS_JAX = True
except ImportError:
    HAS_JAX = False
    
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    # Pure-Python fallback
    class _NpFallback:
        float32 = float
        @staticmethod
        def zeros(shape, dtype=None):
            if isinstance(shape, tuple):
                return [[0.0] * shape[1] for _ in range(shape[0])]
            return [0.0] * shape
        @staticmethod
        def array(lst, dtype=None):
            return list(lst)
        @staticmethod
        def exp(x):
            import math
            if isinstance(x, list):
                return [math.exp(v) for v in x]
            return math.exp(x)
        @staticmethod
        def max(x):
            return max(x) if isinstance(x, list) else x
        @staticmethod
        def argmax(x):
            return x.index(max(x)) if isinstance(x, list) else 0
    np = _NpFallback()

from typing import List, Optional
from engine.game import Game
from engine.card import Card


# Phase names for one-hot encoding
PHASES = [
    "Untap", "Upkeep", "Draw", "Main 1", "Begin Combat",
    "Declare Attackers", "Declare Blockers", "First Strike Damage",
    "Combat Damage", "End Combat", "Main 2", "End", "Cleanup"
]

# Keywords to track as binary flags
KEYWORD_FLAGS = [
    'has_flying', 'has_trample', 'has_lifelink', 'has_deathtouch',
    'has_first_strike', 'has_double_strike', 'has_vigilance', 'has_haste',
    'has_hexproof', 'has_menace', 'has_reach', 'has_defender',
    'has_indestructible', 'has_flash', 'has_prowess', 'is_unblockable'
]

VECTOR_SIZE = 276


def vectorize_game_state(game: Game, player_idx: int) -> np.ndarray:
    """Convert complete game state to fixed-length vector from a player's perspective.
    
    Args:
        game: The Game object with current state
        player_idx: Index of the player whose perspective to encode (0 or 1)
    
    Returns:
        numpy array or jax.numpy array of shape (VECTOR_SIZE,) with float32 values
    """
    if HAS_JAX:
        vec = np.zeros(VECTOR_SIZE, dtype=np.float32)  # Stage in numpy, convert at end
    else:
        vec = np.zeros(VECTOR_SIZE, dtype=np.float32)
    
    player = game.players[player_idx]
    opp_idx = (player_idx + 1) % len(game.players)
    opp = game.players[opp_idx]
    
    idx = 0
    
    # === HAND (20 features) ===
    idx = _encode_hand(vec, idx, player)
    
    # === BOARD (52 features) ===
    idx = _encode_board(vec, idx, game, player, opp)
    
    # === STACK (10 features) ===
    idx = _encode_stack(vec, idx, game)
    
    # === GRAVEYARDS (20 features) ===
    idx = _encode_graveyard(vec, idx, player)
    idx = _encode_graveyard(vec, idx, opp)
    
    # === LIFE (4 features) ===
    vec[idx] = player.life / 20.0  # Normalized
    vec[idx+1] = opp.life / 20.0
    vec[idx+2] = (player.life - opp.life) / 20.0
    vec[idx+3] = player.life / max(opp.life, 1)
    idx += 4
    
    # === MANA (10 features) ===
    idx = _encode_mana(vec, idx, player, game)
    
    # === PHASE (13 features) ===
    phase_idx = PHASES.index(game.current_phase) if game.current_phase in PHASES else 0
    vec[idx + phase_idx] = 1.0
    idx += 13
    
    # === TURN INFO (4 features) ===
    vec[idx] = game.turn_count / 20.0  # Normalized
    vec[idx+1] = 1.0 if game.active_player == player else 0.0
    vec[idx+2] = len(player.library) / 60.0
    vec[idx+3] = len(opp.library) / 60.0
    idx += 4
    
    # === THREAT ASSESSMENT (10 features) ===
    idx = _encode_threats(vec, idx, player)
    
    # === EXTENDED FEATURES ===
    idx = _encode_extended(vec, idx, game, player, opp)
    
    if HAS_JAX:
        return jnp.array(vec)
    return vec


def _encode_hand(vec: np.ndarray, idx: int, player) -> int:
    """Encode hand composition: colors, CMC histogram, types."""
    color_map = {'W': 0, 'U': 1, 'B': 2, 'R': 3, 'G': 4}
    
    # Color counts [0:5]
    for card in player.hand.cards:
        for c in card.color_identity:
            if c in color_map:
                vec[idx + color_map[c]] += 1
    idx += 5
    
    # CMC histogram [5:15]
    from engine.player import Player
    for card in player.hand.cards:
        cmc = Player._parse_cmc(card.cost) if card.cost else 0
        cmc_bin = min(cmc, 9)
        vec[idx + cmc_bin] += 1
    idx += 10
    
    # Type counts [15:20]
    for card in player.hand.cards:
        if card.is_creature:
            vec[idx] += 1
        elif card.is_instant:
            vec[idx+1] += 1
        elif card.is_sorcery:
            vec[idx+2] += 1
        elif card.is_land:
            vec[idx+3] += 1
        else:
            vec[idx+4] += 1
    idx += 5
    
    return idx


def _encode_board(vec: np.ndarray, idx: int, game: Game, player, opp) -> int:
    """Encode board state: creature stats, permanent counts, keyword flags."""
    # Own creature stats [0:5]
    own_creatures = [c for c in game.battlefield.cards if c.controller == player and c.is_creature]
    vec[idx] = len(own_creatures) / 10.0
    vec[idx+1] = sum(c.power or 0 for c in own_creatures) / 20.0
    vec[idx+2] = sum(c.toughness or 0 for c in own_creatures) / 20.0
    vec[idx+3] = sum(sum(1 for k in KEYWORD_FLAGS if getattr(c, k, False)) for c in own_creatures) / 10.0
    from engine.player import Player
    vec[idx+4] = (sum(Player._parse_cmc(c.cost) for c in own_creatures if c.cost) / max(len(own_creatures), 1)) / 5.0
    idx += 5
    
    # Opp creature stats [5:10]
    opp_creatures = [c for c in game.battlefield.cards if c.controller == opp and c.is_creature]
    vec[idx] = len(opp_creatures) / 10.0
    vec[idx+1] = sum(c.power or 0 for c in opp_creatures) / 20.0
    vec[idx+2] = sum(c.toughness or 0 for c in opp_creatures) / 20.0
    vec[idx+3] = sum(sum(1 for k in KEYWORD_FLAGS if getattr(c, k, False)) for c in opp_creatures) / 10.0
    vec[idx+4] = (sum(Player._parse_cmc(c.cost) for c in opp_creatures if c.cost) / max(len(opp_creatures), 1)) / 5.0
    idx += 5
    
    # Own noncreature permanents [10:15]
    own_perms = [c for c in game.battlefield.cards if c.controller == player and not c.is_creature]
    vec[idx] = sum(1 for c in own_perms if 'Artifact' in c.type_line) / 5.0
    vec[idx+1] = sum(1 for c in own_perms if 'Enchantment' in c.type_line) / 5.0
    vec[idx+2] = sum(1 for c in own_perms if c.is_planeswalker) / 3.0
    vec[idx+3] = sum(1 for c in own_perms if c.is_land and not c.tapped) / 10.0
    vec[idx+4] = sum(1 for c in own_perms if c.is_land) / 10.0
    idx += 5
    
    # Opp noncreature permanents [15:20]
    opp_perms = [c for c in game.battlefield.cards if c.controller == opp and not c.is_creature]
    vec[idx] = sum(1 for c in opp_perms if 'Artifact' in c.type_line) / 5.0
    vec[idx+1] = sum(1 for c in opp_perms if 'Enchantment' in c.type_line) / 5.0
    vec[idx+2] = sum(1 for c in opp_perms if c.is_planeswalker) / 3.0
    vec[idx+3] = sum(1 for c in opp_perms if c.is_land and not c.tapped) / 10.0
    vec[idx+4] = sum(1 for c in opp_perms if c.is_land) / 10.0
    idx += 5
    
    # Own keyword flags [20:36]
    for ki, kw in enumerate(KEYWORD_FLAGS):
        vec[idx + ki] = 1.0 if any(getattr(c, kw, False) for c in own_creatures) else 0.0
    idx += 16
    
    # Opp keyword flags [36:52]
    for ki, kw in enumerate(KEYWORD_FLAGS):
        vec[idx + ki] = 1.0 if any(getattr(c, kw, False) for c in opp_creatures) else 0.0
    idx += 16
    
    return idx


def _encode_stack(vec: np.ndarray, idx: int, game: Game) -> int:
    """Encode stack state."""
    from engine.card import StackItem
    items = game.stack.cards
    vec[idx] = len(items) / 5.0
    vec[idx+1] = sum(1 for s in items if isinstance(s, Card)) / 5.0
    vec[idx+2] = sum(1 for s in items if isinstance(s, StackItem)) / 5.0
    # CMC histogram of stack items
    from engine.player import Player
    for s in items:
        if isinstance(s, Card):
            cmc = Player._parse_cmc(s.cost) if s.cost else 0
            bin_idx = min(cmc, 6)
            vec[idx + 3 + bin_idx] += 1
    idx += 10
    return idx


def _encode_graveyard(vec: np.ndarray, idx: int, player) -> int:
    """Encode graveyard summary."""
    cards = player.graveyard.cards
    vec[idx] = len(cards) / 20.0
    vec[idx+1] = sum(1 for c in cards if c.is_creature) / 10.0
    vec[idx+2] = sum(1 for c in cards if c.is_instant or c.is_sorcery) / 10.0
    from engine.player import Player
    vec[idx+3] = sum(Player._parse_cmc(c.cost) for c in cards if c.cost) / 50.0
    vec[idx+4] = sum(1 for c in cards if c.is_land) / 10.0
    # Padding for future use
    idx += 10
    return idx


def _encode_mana(vec: np.ndarray, idx: int, player, game: Game) -> int:
    """Encode mana availability."""
    color_order = ['W', 'U', 'B', 'R', 'G', 'C']
    for ci, color in enumerate(color_order):
        vec[idx + ci] = player.mana_pool.get(color, 0) / 5.0
    vec[idx+6] = player.available_mana(game) / 10.0
    vec[idx+7] = sum(player.mana_pool.values()) / 10.0
    untapped_lands = sum(1 for c in game.battlefield.cards 
                        if c.controller == player and c.is_land and not c.tapped)
    vec[idx+8] = untapped_lands / 10.0
    total_lands = sum(1 for c in game.battlefield.cards 
                     if c.controller == player and c.is_land)
    vec[idx+9] = untapped_lands / max(total_lands, 1)
    idx += 10
    return idx


def _encode_threats(vec: np.ndarray, idx: int, player) -> int:
    """Encode threat assessment from hand contents."""
    hand = player.hand.cards
    vec[idx] = sum(1 for c in hand if c.is_removal) / 4.0
    vec[idx+1] = sum(1 for c in hand if c.is_counter) / 4.0
    vec[idx+2] = sum(1 for c in hand if c.is_burn) / 4.0
    vec[idx+3] = sum(1 for c in hand if c.is_draw) / 4.0
    vec[idx+4] = sum(1 for c in hand if c.is_creature) / 8.0
    vec[idx+5] = sum(1 for c in hand if c.is_board_wipe) / 2.0
    vec[idx+6] = sum(1 for c in hand if c.is_buff) / 4.0
    vec[idx+7] = sum(1 for c in hand if c.is_lifegain) / 4.0
    vec[idx+8] = len(hand) / 7.0
    vec[idx+9] = sum(1 for c in hand if c.is_land) / 7.0
    idx += 10
    return idx


def _encode_extended(vec: np.ndarray, idx: int, game: Game, player, opp) -> int:
    """Extended features: zone sizes, tempo signals, game progression."""
    # Library sizes
    vec[idx] = len(player.library) / 60.0
    vec[idx+1] = len(opp.library) / 60.0
    
    # Hand sizes
    vec[idx+2] = len(player.hand) / 7.0
    vec[idx+3] = len(opp.hand) / 7.0
    
    # Exile zone sizes
    vec[idx+4] = len(player.exile) / 10.0
    vec[idx+5] = len(opp.exile) / 10.0
    
    # Board advantage signals
    own_power = sum(c.power or 0 for c in game.battlefield.cards if c.controller == player and c.is_creature)
    opp_power = sum(c.power or 0 for c in game.battlefield.cards if c.controller == opp and c.is_creature)
    vec[idx+6] = (own_power - opp_power) / 20.0  # Power differential
    
    own_count = sum(1 for c in game.battlefield.cards if c.controller == player)
    opp_count = sum(1 for c in game.battlefield.cards if c.controller == opp)
    vec[idx+7] = (own_count - opp_count) / 10.0  # Permanent count differential
    
    # Tempo: cards played vs turn number
    vec[idx+8] = own_count / max(game.turn_count, 1)
    
    # Lethal potential: do we have enough power to kill opponent?
    vec[idx+9] = 1.0 if own_power >= opp.life else own_power / max(opp.life, 1)
    
    idx += 10
    
    # Remaining features padded with zeros (for future expansion)
    return VECTOR_SIZE  # Ensure we fill the full vector


def vectorize_actions(game: Game, actions: list, player_idx: int) -> np.ndarray:
    """Encode legal actions as a matrix for neural network action selection.
    
    Returns:
        numpy array of shape (num_actions, ACTION_DIM) where each row
        encodes one legal action's features.
    """
    ACTION_DIM = 20
    mat = np.zeros((len(actions), ACTION_DIM), dtype=np.float32)
    
    action_type_map = {
        'pass': 0, 'play_land': 1, 'cast_spell': 2, 'cycle': 3,
        'declare_attackers': 4, 'declare_blockers': 5, 'crew': 6,
        'equip': 7, 'loyalty_ability': 8, 'activate_ability': 9
    }
    
    from engine.player import Player
    
    for i, action in enumerate(actions):
        # Action type one-hot
        atype = action_type_map.get(action.get('type', 'pass'), 0)
        mat[i, atype] = 1.0
        
        # Card features (if action involves a card)
        card = action.get('card')
        if card:
            mat[i, 10] = (card.power or 0) / 10.0
            mat[i, 11] = (card.toughness or 0) / 10.0
            mat[i, 12] = Player._parse_cmc(card.cost) / 8.0 if card.cost else 0
            mat[i, 13] = 1.0 if card.is_creature else 0.0
            mat[i, 14] = 1.0 if card.is_removal else 0.0
            mat[i, 15] = 1.0 if card.is_burn else 0.0
            mat[i, 16] = 1.0 if card.is_counter else 0.0
            mat[i, 17] = 1.0 if card.is_draw else 0.0
        
        # Target features
        target = action.get('target')
        if target and isinstance(target, Card):
            mat[i, 18] = (target.power or 0) / 10.0
            mat[i, 19] = (target.toughness or 0) / 10.0
    
    if HAS_JAX:
        return jnp.array(mat)
    return mat
