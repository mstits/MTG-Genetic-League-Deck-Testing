"""Tests for simulation/flex_tester.py — combinatorial deck testing."""

import pytest
from simulation.flex_tester import FlexTester


class TestFlexTester:
    """FlexTester combinatorial deck generation."""

    def test_init_deck_size_validation(self):
        core = {"Mountain": 61}
        # Core deck already at target size shouldn't accept more flex slots
        with pytest.raises(ValueError, match="already exceeds target size"):
            FlexTester(core, flex_pool=["Lightning Bolt"], target_size=60)

    def test_init_calculates_slots(self):
        core = {"Mountain": 20, "Lightning Bolt": 4}
        # 24 core cards, target 60, slots to fill = 36
        tester = FlexTester(core, flex_pool=[], target_size=60)
        assert tester.slots_to_fill == 36

    def test_generate_configurations_respects_four_of_rule(self):
        """Should filter out configurations that violate the 4-of rule."""
        core = {"Lightning Bolt": 3}
        flex_pool = ["Lightning Bolt", "Shock"]
        
        # We need 2 more cards to hit a target size of 5
        tester = FlexTester(core, flex_pool, target_size=5)
        
        # Possible combos from flex pool of size 2:
        # 2x Shock (Valid: 3 Bolt, 2 Shock)
        # 1x Bolt, 1x Shock (Valid: 4 Bolt, 1 Shock)
        # 2x Bolt (Invalid: 5 Bolt!)
        
        configs = list(tester.generate_configurations())
        
        assert len(configs) == 2
        for c in configs:
            assert c.get("Lightning Bolt", 0) <= 4

    def test_generate_configurations_zero_slots(self):
        """If core deck is exactly target size, generates exactly 1 config (the core deck)."""
        core = {"Mountain": 10}
        tester = FlexTester(core, flex_pool=["Bolt"], target_size=10)
        configs = list(tester.generate_configurations())
        assert len(configs) == 1
        assert configs[0] == core

    def test_generate_configurations_unrestricted_basics(self):
        """Basic lands ignore the 4-of rule."""
        core = {"Mountain": 3}
        flex_pool = ["Mountain"]
        
        tester = FlexTester(core, flex_pool, target_size=6)
        configs = list(tester.generate_configurations())
        
        # 3x Mountain flex slots + 3 core = 6 Mountains. Valid.
        assert len(configs) == 1
        assert configs[0]["Mountain"] == 6
