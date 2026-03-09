"""FormatValidator — Deck legality checking against MTG format rules.

Validates decklists against Scryfall legality data for Standard, Modern,
Pioneer, and Commander formats. Enforces:

    - Card legality per format (legal / not_legal / banned / restricted)
    - Deck minimum sizes (60 for constructed, 100 for Commander)
    - Copy limits (max 4 per non-basic card; Commander is singleton)
    - Commander color identity restrictions

Usage:
    from engine.format_validator import FormatValidator, LegalityError

    validator = FormatValidator(card_pool, "modern")
    try:
        validator.validate(decklist)
    except LegalityError as e:
        print(e)  # Lists all violations
"""

from typing import Optional


# Basic lands exempt from copy limits (Rule 505.4)
BASIC_LAND_NAMES = frozenset([
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
    "Wastes",
])

# Supported formats and their minimum deck sizes
FORMAT_RULES = {
    "standard":  {"min_deck": 60, "max_copies": 4},
    "modern":    {"min_deck": 60, "max_copies": 4},
    "pioneer":   {"min_deck": 60, "max_copies": 4},
    "legacy":    {"min_deck": 60, "max_copies": 4},
    "vintage":   {"min_deck": 60, "max_copies": 4},
    "pauper":    {"min_deck": 60, "max_copies": 4},
    "commander": {"min_deck": 100, "max_copies": 1},
    "commander_1": {"min_deck": 100, "max_copies": 1, "max_bracket": 1},
    "commander_2": {"min_deck": 100, "max_copies": 1, "max_bracket": 2},
    "commander_3": {"min_deck": 100, "max_copies": 1, "max_bracket": 3},
    "commander_4": {"min_deck": 100, "max_copies": 1, "max_bracket": 4},
    "commander_5": {"min_deck": 100, "max_copies": 1, "max_bracket": 5},
}


class LegalityError(Exception):
    """Raised when a deck contains cards illegal in the target format.

    Attributes:
        violations: List of dicts describing each violation.
                    Each has 'card', 'reason', and optionally 'details'.
    """

    def __init__(self, format_name: str, violations: list[dict]):
        self.format_name = format_name
        self.violations = violations
        summary = f"{len(violations)} legality violation(s) for {format_name}:\n"
        summary += "\n".join(
            f"  - {v['card']}: {v['reason']}" for v in violations[:10]
        )
        if len(violations) > 10:
            summary += f"\n  ... and {len(violations) - 10} more"
        super().__init__(summary)


class FormatValidator:
    """Validates decklists against Magic: The Gathering format rules.

    Loads legality data from the Scryfall card pool (which includes a
    'legalities' dict per card). Call validate() to check a decklist or
    get_illegal_cards() for a non-throwing version.

    Args:
        card_pool: List of Scryfall card dicts (must include 'name' and 'legalities').
        format_name: Target format string (standard, modern, pioneer, commander, etc).
    """

    def __init__(self, card_pool: list[dict], format_name: str):
        format_name = format_name.lower()
        if format_name not in FORMAT_RULES:
            raise ValueError(
                f"Unknown format '{format_name}'. "
                f"Supported: {', '.join(FORMAT_RULES.keys())}"
            )
        self.format_name = format_name
        self.rules = FORMAT_RULES[format_name]

        # Build lookup: card name → legality status in this format
        self._legality: dict[str, str] = {}
        # Build lookup: card name → color identity (for Commander)
        self._color_identity: dict[str, list[str]] = {}

        for card in card_pool:
            name = card.get("name", "")
            legalities = card.get("legalities", {})
            self._legality[name] = legalities.get(format_name, "not_legal")
            
            # Feb 2026 CR: Lutri, the Spellchaser is legal in the 99 of all formats.
            if "Lutri, the Spellchaser" in name:
                self._legality[name] = "legal"

            self._color_identity[name] = card.get("color_identity", [])

            # Also index the front face of double-faced cards
            if " // " in name:
                front = name.split(" // ")[0].strip()
                if front not in self._legality:
                    self._legality[front] = self._legality[name]
                    self._color_identity[front] = self._color_identity[name]

    def validate(
        self,
        decklist: dict[str, int],
        commander: Optional[str] = None,
        companion: Optional[str] = None,
    ) -> None:
        """Validate a decklist. Raises LegalityError on any violation.

        Args:
            decklist: Dict of card_name → count.
            commander: Optional commander card name (for Commander format).
            companion: Optional companion card name.

        Raises:
            LegalityError: With a list of all violations found.
        """
        violations = self.get_illegal_cards(decklist, commander)
        if violations:
            raise LegalityError(self.format_name, violations)

    def get_illegal_cards(
        self,
        decklist: dict[str, int],
        commander: Optional[str] = None,
        companion: Optional[str] = None,
    ) -> list[dict]:
        """Return details of all illegal cards without raising.

        Returns:
            List of dicts with 'card', 'reason', and optionally 'details' keys.
        """
        violations: list[dict] = []

        # 1. Check deck size
        total = sum(decklist.values())
        min_deck = self.rules["min_deck"]
        if total < min_deck:
            violations.append({
                "card": "(deck)",
                "reason": f"Deck has {total} cards, minimum is {min_deck}",
            })

        # Feb 2026 CR: Lutri Companion ban
        if companion and "Lutri, the Spellchaser" in companion:
            violations.append({
                "card": companion,
                "reason": "Feb 2026 CR: Lutri, the Spellchaser is strictly prohibited as a Companion.",
            })

        # 1.5 Check 2026 Commander Brackets Guardrails
        if "max_bracket" in self.rules:
            from engine.salt_score import calculate_salt_score
            score_data = calculate_salt_score(decklist)
            deck_bracket = score_data['bracket']
            if deck_bracket > self.rules["max_bracket"]:
                # Find the cards that pushed it over
                offending = [f["card"] for f in score_data['flagged_cards'] if f["bracket"] > self.rules["max_bracket"]]
                violations.append({
                    "card": "(deck)",
                    "reason": f"Deck bracket {deck_bracket} is too high for {self.format_name}. Offending cards: {', '.join(offending[:3])}",
                })

        # 2. Check each card's legality and copy count
        max_copies = self.rules["max_copies"]
        for card_name, count in decklist.items():
            # Skip basic lands for copy checks
            is_basic = card_name in BASIC_LAND_NAMES

            # Check legality status
            status = self._legality.get(card_name)
            if status is None:
                violations.append({
                    "card": card_name,
                    "reason": f"Card not found in card pool",
                })
            elif status == "banned":
                violations.append({
                    "card": card_name,
                    "reason": f"Banned in {self.format_name}",
                })
            elif status == "not_legal":
                violations.append({
                    "card": card_name,
                    "reason": f"Not legal in {self.format_name}",
                })
            elif status == "restricted" and count > 1:
                violations.append({
                    "card": card_name,
                    "reason": f"Restricted in {self.format_name} (max 1 copy, have {count})",
                })

            # Check copy limits (basic lands are exempt)
            if not is_basic and count > max_copies:
                violations.append({
                    "card": card_name,
                    "reason": f"Too many copies: {count} (max {max_copies} in {self.format_name})",
                })

        # 3. Commander-specific: color identity restriction
        if "commander" in self.format_name and commander:
            cmd_identity = set(self._color_identity.get(commander, []))
            for card_name in decklist:
                card_identity = set(self._color_identity.get(card_name, []))
                overflow = card_identity - cmd_identity
                if overflow:
                    violations.append({
                        "card": card_name,
                        "reason": (
                            f"Color identity {card_identity} outside commander's "
                            f"identity {cmd_identity}"
                        ),
                    })

        return violations

    def validate_matchup(self, decklist1: dict[str, int], decklist2: dict[str, int]) -> None:
        """Validate that two decks are compatible for a matchup.
        In Commander, decks must be in the same salt bracket.
        
        Raises:
            LegalityError: If the matchup is incompatible.
        """
        if "commander" in self.format_name:
            from engine.salt_score import calculate_salt_score
            b1 = calculate_salt_score(decklist1)['bracket']
            b2 = calculate_salt_score(decklist2)['bracket']
            if b1 != b2:
                raise LegalityError(
                    self.format_name,
                    [{
                        "card": "(matchup)",
                        "reason": f"Incompatible Salt Brackets: {b1} vs {b2}",
                    }]
                )

    def is_legal(self, card_name: str) -> bool:
        """Quick check: is a single card legal in this format?"""
        return self._legality.get(card_name) == "legal"

    def get_status(self, card_name: str) -> str:
        """Get the legality status string for a card (legal/banned/not_legal/restricted)."""
        return self._legality.get(card_name, "unknown")
