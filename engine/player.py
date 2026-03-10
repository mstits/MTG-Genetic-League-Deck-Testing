"""Player — Player state management and mana payment system.

Manages life total, hand, library, graveyard, mana pool, and land drops.
The mana payment pipeline works as follows:

    1. Parse cost string (e.g. "{2}{R}{R}") into structured requirements
    2. Drain floating mana pool first (from dorks, rituals, treasures)
    3. Use backtracking solver to assign untapped lands to remaining costs
    4. Handle hybrid mana (e.g. {R/W}) by trying both colors via backtracking

The solver sorts lands by flexibility (fewest production options first) to
find solutions faster by constraining the most limited resources early.
"""

from typing import List, Optional, Dict, Tuple
from .zone import Zone
from .card import Card
from .deck import Deck
import re
import logging

logger = logging.getLogger(__name__)

# Basic land type → mana color mapping (Rule 305.6)
BASIC_LAND_MANA = {
    'Plains': 'W', 'Island': 'U', 'Swamp': 'B',
    'Mountain': 'R', 'Forest': 'G', 'Wastes': 'C',
}

MANA_COLORS = {'W', 'U', 'B', 'R', 'G', 'C'}


class Player:
    """A Magic player with life total, zones, and mana management.

    Attributes:
        name:                  Display name for logging.
        original_deck:         The Deck blueprint this player was initialized from.
        life:                  Current life total (starts at 20, Rule 119.1).
        library:               Zone containing undrawn cards.
        hand:                  Zone containing cards in hand.
        graveyard:             Zone containing discarded/destroyed cards.
        exile:                 Zone for exiled cards.
        lands_played_this_turn: Land drop counter (resets each turn).
        max_lands_per_turn:    Usually 1 (can be modified by effects).
        mana_pool:             Floating mana from dorks/rituals (empties between phases).
    """

    def __init__(self, name: str, deck: Deck):
        self.name = name
        self.original_deck = deck
        self.life = 20
        self.library = Zone("Library")
        self.hand = Zone("Hand")
        self.graveyard = Zone("Graveyard")
        self.exile = Zone("Exile")
        self.sideboard = Zone("Sideboard")  # Bo3: up to 15 cards
        self.lands_played_this_turn = 0
        self.max_lands_per_turn = 1
        # Colored mana pool (Rule 106.1)
        self.mana_pool: Dict[str, int] = {c: 0 for c in MANA_COLORS}
        # Commander tracking (EDH)
        self.commander = None
        self.commander_tax = 0  # Increases by 2 each cast
        self.commander_damage_taken: Dict[str, int] = {}  # opponent_name → damage
        self.poison_counters: int = 0  # Infect/Toxic — 10 = loss (Rule 704.5c)
        self.fatal_blow_reason: Optional[str] = None  # Tracks the exact reason a player lost
        
        
        # Initialize library from deck
        for card in deck.get_game_deck():
            card.controller = self
            self.library.add(card)
            
        self.library_empty_draw = False  # Track empty library draw for SBA loss
    
    def reset_for_new_game(self, game=None) -> None:
        """Reset player state for a new game in a Bo3 match."""
        self.life = 20
        self.hand = Zone("Hand")
        self.graveyard = Zone("Graveyard")
        self.exile = Zone("Exile")
        self.lands_played_this_turn = 0
        self.mana_pool = {c: 0 for c in MANA_COLORS}
        self.poison_counters = 0
        self.fatal_blow_reason = None
        self.library = Zone("Library")
        for card in self.original_deck.get_game_deck():
            card.controller = self
            self.library.add(card)
            
        self.library_empty_draw = False
            
    def shuffle_library(self) -> None:
        """Randomize the order of the remaining cards in the library."""
        self.library.shuffle()
        
    def draw_card(self, amount: int = 1, game=None) -> None:
        """Draw cards. If game is provided, triggers CR 614 replacement events."""
        for _ in range(amount):
            if game and game.apply_replacement('draw', player=self):
                continue  # Draw was replaced (e.g., Dredge)
                
            card = self.library.draw()
            if card:
                self.hand.add(card)
            else:
                self.library_empty_draw = True  # Caught by SBAs (Rule 704.5b)

    def play_land(self, card: Card, game) -> bool:
        """Move a land card from hand to the battlefield and decrement available land plays."""
        if not card.is_land:
            return False
        if self.lands_played_this_turn >= self.max_lands_per_turn:
            return False
        
        self.hand.remove(card)
        card.controller = self
        game.battlefield.add(card)
        self.lands_played_this_turn += 1
        
        # Enters-tapped lands (Rule 305.7)
        if hasattr(card, 'enters_tapped') and card.enters_tapped:
            # Check if it's a shockland
            if getattr(card, 'is_shock_land', False):
                if self.life > 5:
                    self.life -= 2
                    card.tapped = False
                    game.log_event(f"T{game.turn_count}: {self.name} pays 2 life for {card.name} to enter untapped")
                else:
                    card.tapped = True
            else:
                card.tapped = True
        
        # Fetchland activation: auto-sacrifice to search for a basic land
        # Fetches sacrifice + pay 1 life → search library for matching land type
        if getattr(card, 'is_fetchland', False) and getattr(card, 'fetch_targets', None):
            self.life -= 1
            game.battlefield.remove(card)
            self.graveyard.add(card)
            
            # Find the best matching basic land in library
            # Prioritize colors the deck needs most (based on hand)
            fetch_targets = card.fetch_targets  # e.g. ['Forest', 'Island']
            
            # Map land types to mana colors they produce
            land_type_colors = {
                'Plains': 'W', 'Island': 'U', 'Swamp': 'B',
                'Mountain': 'R', 'Forest': 'G'
            }
            
            # Score each fetchable color by how many cards in hand need it
            color_needs = {}
            for hand_card in self.hand.cards:
                if hand_card.cost:
                    for c in re.findall(r'\{([WUBRG])\}', hand_card.cost):
                        color_needs[c] = color_needs.get(c, 0) + 1
            
            best_land = None
            best_score = -1
            
            for lib_card in self.library.cards:
                if not lib_card.is_land:
                    continue
                # Check if this land matches any of the fetch targets
                for target_type in fetch_targets:
                    if target_type in lib_card.type_line or lib_card.name == target_type:
                        # Score by how much we need this color
                        target_color = land_type_colors.get(target_type, '')
                        score = color_needs.get(target_color, 0)
                        # Prefer dual/shock lands over basics (they produce 2 colors)
                        produced = getattr(lib_card, 'produced_mana', [])
                        if len(produced) > 1:
                            score += 2
                        if score > best_score:
                            best_score = score
                            best_land = lib_card
                        break
            
            if best_land:
                self.library.remove(best_land)
                best_land.controller = self
                game.battlefield.add(best_land)
                # Fetched lands enter untapped (unless they have enters_tapped and we don't pay life)
                if getattr(best_land, 'is_shock_land', False):
                    if self.life > 5:
                        self.life -= 2
                        best_land.tapped = False
                        best_land.enters_tapped = False
                        game.log_event(f"T{game.turn_count}: {self.name} fetches {best_land.name} (shock, paid 2 life, total life={self.life})")
                    else:
                        best_land.tapped = True
                        game.log_event(f"T{game.turn_count}: {self.name} fetches {best_land.name} (enters tapped, declined shock)")
                elif getattr(best_land, 'enters_tapped', False):
                    best_land.tapped = True
                    game.log_event(f"T{game.turn_count}: {self.name} fetches {best_land.name} (enters tapped)")
                else:
                    best_land.tapped = False
                    game.log_event(f"T{game.turn_count}: {self.name} fetches {best_land.name}")
            else:
                game.log_event(f"T{game.turn_count}: {self.name} fetches with {card.name} but found no matching land")
            
            return True
        
        return True

    def count_lands(self, game) -> int:
        """Count untapped lands this player controls."""
        count = 0
        for c in game.battlefield.cards:
            if c.controller == self and c.is_land and not c.tapped:
                count += 1
        return count

    @staticmethod
    def _land_produces(card: Card) -> List[str]:
        """Determine what color mana a land produces (Rule 305.6).
        Returns a list of all potential colors (e.g. ['W', 'U', 'C'])."""
        colors = []
        # Check basic land types first
        for land_name, color in BASIC_LAND_MANA.items():
            if land_name in card.type_line or card.name == land_name:
                colors.append(color)
        
        # Check produced_mana field (from Scryfall data or Oracle parse)
        if hasattr(card, 'produced_mana') and card.produced_mana:
            for c in card.produced_mana:
                if c in MANA_COLORS and c not in colors:
                    colors.append(c)
        
        # Check color_identity as fallback (only if nothing else found?)
        # Actually color_identity might be broader than production (e.g. fetchlands).
        # So fallback to 'C' only if empty
        if not colors:
            return ['C']
        
        return colors

    def _get_available_mana(self, game) -> Dict[str, int]:
        """Calculate logical max available mana (for AI heuristics).
        Note: This over-estimates dual lands (counts for both colors)."""
        available = {c: 0 for c in MANA_COLORS}
        for c in game.battlefield.cards:
            if c.controller == self and c.is_land and not c.tapped:
                options = self._land_produces(c)
                for color in options:
                    available[color] += 1
        return available
    
    def available_mana(self, game) -> int:
        """Total untapped lands (for curve efficiency calculations)."""
        return sum(1 for c in game.battlefield.cards
                   if c.controller == self and c.is_land and not c.tapped)
    
    def can_pay_cost(self, cost: str, game=None) -> bool:
        """Check if player can pay the mana cost with available lands + mana pool."""
        if not cost: return True
        if not game: return True

        req = self._parse_mana_requirements(cost)
        if not req: return True
        
        # Drain floating mana pool first (from dorks, treasures, rituals)
        req = self._reduce_req_by_pool(req)
        if not req:  # Pool covered everything
            return True
        
        lands = [c for c in game.battlefield.cards 
                 if c.controller == self and c.is_land and not c.tapped]
        
        # Fast path: if total lands < remaining cost (exclude _hybrid — handled by solver)
        total_pips = sum(v for k, v in req.items() if k != '_hybrid')
        hybrid_count = len(req.get('_hybrid', []))
        if len(lands) < total_pips + hybrid_count:
            return False
            
        return self._solve_mana_payment(req, lands) is not None

    def _reduce_req_by_pool(self, req: Dict[str, int]) -> Dict[str, int]:
        """Reduce mana requirements by what's available in the floating mana pool.
        Returns the remaining requirements (may be empty if pool covers all)."""
        remaining = dict(req)
        pool_copy = dict(self.mana_pool)
        
        # First satisfy colored requirements from pool
        for color in list(remaining.keys()):
            if color in ('generic', '_hybrid'):
                continue
            available = pool_copy.get(color, 0)
            if available > 0:
                used = min(available, remaining[color])
                remaining[color] -= used
                pool_copy[color] -= used
                if remaining[color] <= 0:
                    del remaining[color]
        
        # Satisfy hybrid requirements from pool (try either color)
        if '_hybrid' in remaining:
            unsatisfied = []
            for pair in remaining['_hybrid']:
                satisfied = False
                for c in pair:
                    if pool_copy.get(c, 0) > 0:
                        pool_copy[c] -= 1
                        satisfied = True
                        break
                if not satisfied:
                    unsatisfied.append(pair)
            if unsatisfied:
                remaining['_hybrid'] = unsatisfied
            else:
                del remaining['_hybrid']
        
        # Then satisfy generic requirements from leftover pool
        if 'generic' in remaining and remaining['generic'] > 0:
            leftover = sum(pool_copy.values())
            used = min(leftover, remaining['generic'])
            remaining['generic'] -= used
            if remaining['generic'] <= 0:
                del remaining['generic']
        
        return remaining

    def drain_pool_for_cost(self, cost: str) -> bool:
        """Pay cost strictly from the floating mana pool (CR 605).
        Returns True if successful, False if the pool is insufficient.
        """
        req = self._parse_mana_requirements(cost)
        if not req: 
            return True
            
        if not self._check_requirements(req, self.mana_pool):
            return False

        # Drain floating mana pool for colored requirements
        for color in list(req.keys()):
            if color in ('generic', '_hybrid'):
                continue
            used = min(self.mana_pool.get(color, 0), req[color])
            req[color] -= used
            self.mana_pool[color] -= used
            if req[color] <= 0:
                del req[color]
        
        # Drain pool for hybrid requirements
        if '_hybrid' in req:
            unsatisfied = []
            for pair in req['_hybrid']:
                paid = False
                for c in pair:
                    if self.mana_pool.get(c, 0) > 0:
                        self.mana_pool[c] -= 1
                        paid = True
                        break
                if not paid:
                    unsatisfied.append(pair)
            if unsatisfied:
                req['_hybrid'] = unsatisfied
            else:
                del req['_hybrid']
        
        # Drain leftover pool for generic
        if 'generic' in req and req['generic'] > 0:
            for color in list(MANA_COLORS):
                available = self.mana_pool.get(color, 0)
                if available > 0 and req['generic'] > 0:
                    used = min(available, req['generic'])
                    req['generic'] -= used
                    self.mana_pool[color] -= used
            if req.get('generic', 0) <= 0:
                req.pop('generic', None)
                
        return len(req) == 0

    def pay_cost(self, cost: str, game) -> None:
        """Legacy auto-tap cost payment (to be deprecated by CR 601.2 sequence).
        Pays cost by draining mana pool first, then tapping lands for remainder.
        """
        # First try strict drain
        if self.drain_pool_for_cost(cost):
            return
            
        req = self._parse_mana_requirements(cost)
        if not req: return

        # Reduce requirements by what pool CAN cover
        req = self._reduce_req_by_pool(req)
        if not req:
            return

        # Tap lands for the remainder
        target_lands = [c for c in game.battlefield.cards 
                        if c.controller == self and c.is_land and not c.tapped]
        
        solution = self._solve_mana_payment(req, target_lands)
        if solution is not None:
            for land, color in solution:
                land.tapped = True
        else:
            logger.error("Failed to pay cost %s despite can_pay_cost passing.", cost)

    def _solve_mana_payment(self, req: Dict[str, int], lands: List[Card]) -> Optional[List[Tuple[Card, str]]]:
        """Backtracking solver to find a valid assignment of lands to requirements.
        Handles hybrid mana by trying both colors.
        Returns list of (land, color) tuples, or None if impossible."""
        
        # Pre-process lands: sort by flexibility (fewest options first)
        land_options = [(land, self._land_produces(land)) for land in lands]
        land_options.sort(key=lambda x: len(x[1]))
        
        # Extract hybrid pairs and expand into needed colors
        # Each hybrid pip becomes a flexible requirement tried via backtracking
        hybrid_pairs = req.pop('_hybrid', [])
        
        # Flatten fixed color requirements: ['W', 'W', 'U']
        needed = []
        for c, count in req.items():
            if c != 'generic':
                needed.extend([c] * count)
        
        generic_needed = req.get('generic', 0)
        
        # For hybrid pairs, try each combination via backtracking
        return self._backtrack_solve_hybrid(land_options, needed, generic_needed, hybrid_pairs, [])

    def _backtrack_solve_hybrid(self, available_lands, needed_colors, generic_needed, hybrid_pairs, assignment):
        """Solve mana payment with hybrid mana support.
        For each hybrid pip, tries both colors via backtracking."""
        
        # First, resolve all hybrid pairs into concrete color needs
        if hybrid_pairs:
            pair = hybrid_pairs[0]
            remaining_hybrid = hybrid_pairs[1:]
            # Try first color
            res = self._backtrack_solve_hybrid(
                available_lands, needed_colors + [pair[0]], generic_needed, remaining_hybrid, assignment)
            if res:
                return res
            # Try second color
            res = self._backtrack_solve_hybrid(
                available_lands, needed_colors + [pair[1]], generic_needed, remaining_hybrid, assignment)
            return res
        
        # All hybrids resolved — run the standard solver
        return self._backtrack_solve(available_lands, needed_colors, generic_needed, assignment)

    def _backtrack_solve(self, available_lands, needed_colors, generic_needed, assignment):
        # Base case: All requirements met
        if not needed_colors and generic_needed <= 0:
            return assignment
            
        # Recursive step: satisfy colored requirements
        if needed_colors:
            target = needed_colors[0]
            remaining_needs = needed_colors[1:]
            
            for i, (land, options) in enumerate(available_lands):
                if target in options:
                    remaining_lands = available_lands[:i] + available_lands[i+1:]
                    res = self._backtrack_solve(remaining_lands, remaining_needs, generic_needed, assignment + [(land, target)])
                    if res: return res
            return None
            
        # Pay generic — COLOR-DIVERSITY OPTIMIZATION
        # Sort remaining lands by "expendability": tap lands that produce
        # already-abundant colors first, preserve scarce-color sources.
        if generic_needed > 0:
            if not available_lands:
                return None
            
            # Count how many untapped lands produce each color
            color_supply = {}
            for _, options in available_lands:
                for c in options:
                    color_supply[c] = color_supply.get(c, 0) + 1
            
            def expendability(land_tuple):
                """Higher = more expendable (tap this land first for generic).
                Lands producing only abundant colors are tapped first.
                Lands that are the sole source of a color are preserved."""
                _, options = land_tuple
                if not options:
                    return 1000  # Colorless — always tap first
                
                # Minimum supply across all colors this land produces:
                # If this land is the only source of any color, it's critical
                min_supply = min(color_supply.get(c, 0) for c in options)
                
                # Fewer unique colors = more expendable (basic land)
                # More unique colors = could be valuable (dual/tri land)
                flexibility = len(options)
                
                # Score: high supply + low flexibility = expendable
                return min_supply * 10 - flexibility
            
            sorted_lands = sorted(available_lands, key=expendability, reverse=True)
            land, options = sorted_lands[0]
            remaining_lands = [(l, o) for l, o in sorted_lands[1:]]
            color = options[0] if options else 'C'
            return self._backtrack_solve(remaining_lands, [], generic_needed - 1, assignment + [(land, color)])
            
        return None

    @staticmethod
    def _parse_mana_requirements(cost: str) -> Dict[str, int]:
        """Parse '{2}{R}{R}' → {'generic': 2, 'R': 2}.
        Hybrid mana {R/W} stored in '_hybrid' key for solver."""
        if not cost:
            return {}
        req = {}
        # Generic mana {N}
        generic = re.findall(r'\{(\d+)\}', cost)
        generic_total = sum(int(g) for g in generic)
        if generic_total > 0:
            req['generic'] = generic_total
        # Colored pips {W}, {U}, {B}, {R}, {G}, {C}
        colored = re.findall(r'\{([WUBRGC])\}', cost)
        for c in colored:
            req[c] = req.get(c, 0) + 1
        # Hybrid mana {R/W} — store as list of pairs for solver flexibility
        hybrid_pairs = re.findall(r'\{([WUBRG])/([WUBRG])\}', cost)
        if hybrid_pairs:
            req['_hybrid'] = list(hybrid_pairs)
        return req

    @staticmethod
    def _parse_hybrid_pairs(cost: str) -> list:
        """Extract hybrid mana pairs from cost string."""
        return re.findall(r'\{([WUBRG])/([WUBRG])\}', cost)

    @staticmethod
    def _check_requirements(req: Dict[str, int], available: Dict[str, int]) -> bool:
        """Check if available mana can satisfy requirements.
        
        Handles: colored pips, generic, and hybrid {R/W} pips.
        """
        remaining_pool = dict(available)
        
        # 1. Colored pips first
        for color, count in req.items():
            if color == 'generic' or color.startswith('_'):
                continue
            if not isinstance(count, int):
                continue
            if remaining_pool.get(color, 0) < count:
                return False
            remaining_pool[color] = remaining_pool.get(color, 0) - count
        
        # 2. Hybrid pips — each needs 1 mana from either color
        hybrid_pairs = req.get('_hybrid', [])
        for c1, c2 in hybrid_pairs:
            if remaining_pool.get(c1, 0) >= remaining_pool.get(c2, 0):
                if remaining_pool.get(c1, 0) > 0:
                    remaining_pool[c1] -= 1
                elif remaining_pool.get(c2, 0) > 0:
                    remaining_pool[c2] -= 1
                else:
                    # Check generic pool
                    total = sum(v for v in remaining_pool.values() if isinstance(v, int))
                    if total <= 0:
                        return False
                    # Use any available color
                    for c in remaining_pool:
                        if isinstance(remaining_pool[c], int) and remaining_pool[c] > 0:
                            remaining_pool[c] -= 1
                            break
            else:
                if remaining_pool.get(c2, 0) > 0:
                    remaining_pool[c2] -= 1
                elif remaining_pool.get(c1, 0) > 0:
                    remaining_pool[c1] -= 1
                else:
                    total = sum(v for v in remaining_pool.values() if isinstance(v, int))
                    if total <= 0:
                        return False
                    for c in remaining_pool:
                        if isinstance(remaining_pool[c], int) and remaining_pool[c] > 0:
                            remaining_pool[c] -= 1
                            break
        
        # 3. Generic mana
        generic_needed = req.get('generic', 0)
        total_remaining = sum(v for v in remaining_pool.values() if isinstance(v, int))
        return total_remaining >= generic_needed
    
    def scry(self, n: int, role: str = 'midrange') -> None:
        """Scry N: look at top N cards, keep good ones on top, bottom bad ones.
        Role-aware: aggro bottoms expensive cards, control keeps removal on top."""
        if n <= 0 or len(self.library) == 0:
            return
        
        top_cards = []
        for _ in range(min(n, len(self.library))):
            if self.library.cards:
                top_cards.append(self.library.cards.pop(0))
        
        # Separate into keep-on-top and bottom based on role
        keep = []
        bottom = []
        
        for c in top_cards:
            if role == 'aggro':
                # Aggro: bottom expensive cards and excess lands, keep cheap threats + burn
                cmc = self._parse_cmc(c.cost) if c.cost else 0
                if c.is_land and any(k.is_land for k in keep):
                    bottom.append(c)  # Bottom excess lands
                elif cmc >= 4 and not c.is_land:
                    bottom.append(c)  # Bottom expensive spells
                else:
                    keep.append(c)
            elif role == 'control':
                # Control: keep removal/sweepers/card draw, bottom small creatures
                is_useful = (c.is_removal or c.is_board_wipe or 
                           getattr(c, 'is_counterspell', False) or
                           getattr(c, 'draws_cards', False) or
                           c.is_land)
                if c.is_creature and (c.power or 0) <= 2 and not is_useful:
                    bottom.append(c)  # Bottom small creatures
                elif c.is_land and sum(1 for k in keep if k.is_land) >= 2:
                    bottom.append(c)  # Bottom excess lands (control still needs some)
                else:
                    keep.append(c)
            else:
                # Midrange (default): bottom excess lands, keep everything else
                if c.is_land:
                    if not any(k.is_land for k in keep):
                        keep.append(c)
                    else:
                        bottom.append(c)
                else:
                    keep.append(c)
        
        # Put keeps back on top (non-lands first, then lands), bottoms at bottom
        keep.sort(key=lambda c: 1 if c.is_land else 0)
        for c in reversed(keep):
            self.library.cards.insert(0, c)
        for c in bottom:
            self.library.cards.append(c)

    def reset_mana_pool(self) -> None:
        """Empty mana pool (Rule 106.4 — mana empties between steps/phases)."""
        for c in MANA_COLORS:
            self.mana_pool[c] = 0

    @staticmethod
    def _parse_cmc(cost: str) -> int:
        """Parse a mana cost string like '{2}{R}{R}' into total CMC."""
        if not cost:
            return 0
        total = 0
        generic = re.findall(r'\{(\d+)\}', cost)
        for g in generic:
            total += int(g)
        colored = re.findall(r'\{([WUBRGC])\}', cost)
        total += len(colored)
        hybrid = re.findall(r'\{[WUBRG]/[WUBRG]\}', cost)
        total += len(hybrid)
        return total

    def __repr__(self):
        return f"Player({self.name}, Life: {self.life}, Hand: {len(self.hand)}, Library: {len(self.library)})"
