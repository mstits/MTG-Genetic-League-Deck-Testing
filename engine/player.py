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

# Basic land type → mana color mapping (Rule 305.6)
BASIC_LAND_MANA = {
    'Plains': 'W', 'Island': 'U', 'Swamp': 'B',
    'Mountain': 'R', 'Forest': 'G',
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
        self.lands_played_this_turn = 0
        self.max_lands_per_turn = 1
        # Colored mana pool (Rule 106.1)
        self.mana_pool: Dict[str, int] = {c: 0 for c in MANA_COLORS}
        
        # Initialize library from deck
        for card in deck.get_game_deck():
            card.controller = self
            self.library.add(card)
            
    def shuffle_library(self):
        self.library.shuffle()
        
    def draw_card(self, amount: int = 1):
        for _ in range(amount):
            card = self.library.draw()
            if card:
                self.hand.add(card)
            # Empty library = eventual loss (milling)

    def play_land(self, card: Card, game):
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
            card.tapped = True
        
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

    def pay_cost(self, cost: str, game):
        """Pay cost by draining mana pool first, then tapping lands for remainder."""
        req = self._parse_mana_requirements(cost)
        if not req: return

        # Drain floating mana pool for colored requirements
        for color in list(req.keys()):
            if color in ('generic', '_hybrid'):
                continue
            available = self.mana_pool.get(color, 0)
            if available > 0:
                used = min(available, req[color])
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
        
        # If pool covered everything, done
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
            print(f"ERROR: Failed to pay cost {cost} despite can_pay_cost passing.")

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
            
        # Recursive step
        if needed_colors:
            target = needed_colors[0]
            remaining_needs = needed_colors[1:]
            
            for i, (land, options) in enumerate(available_lands):
                if target in options:
                    remaining_lands = available_lands[:i] + available_lands[i+1:]
                    res = self._backtrack_solve(remaining_lands, remaining_needs, generic_needed, assignment + [(land, target)])
                    if res: return res
            return None
            
        # Pay generic
        if generic_needed > 0:
            if not available_lands:
                return None
            land, options = available_lands[0]
            color = options[0]
            return self._backtrack_solve(available_lands[1:], [], generic_needed - 1, assignment + [(land, color)])
            
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
        """Check if available mana can satisfy requirements."""
        remaining_pool = dict(available)
        for color, count in req.items():
            if color == 'generic':
                continue
            if remaining_pool.get(color, 0) < count:
                return False
            remaining_pool[color] -= count
        
        generic_needed = req.get('generic', 0)
        total_remaining = sum(remaining_pool.values())
        return total_remaining >= generic_needed
    
    def scry(self, n: int):
        """Scry N: look at top N cards, keep good ones on top, bottom bad ones.
        Heuristic: keep creatures/spells, bottom excess lands."""
        if n <= 0 or len(self.library) == 0:
            return
        
        top_cards = []
        for _ in range(min(n, len(self.library))):
            if self.library.cards:
                top_cards.append(self.library.cards.pop(0))
        
        # Separate into keep-on-top and bottom
        keep = []
        bottom = []
        
        for c in top_cards:
            if c.is_land:
                # Bottom excess lands (keep 1 land on top if needed)
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

    def reset_mana_pool(self):
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
