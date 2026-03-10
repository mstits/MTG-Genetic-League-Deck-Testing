"""Tests for utils/hypergeometric.py — Mana probability calculations."""

import math
from utils.hypergeometric import (
    nCr,
    hypergeom_pmf,
    hypergeom_cdf_at_least,
    calculate_mana_requirements,
)


class TestNCr:
    """nCr (combinations) function."""

    def test_basic_combinations(self):
        assert nCr(5, 2) == 10
        assert nCr(10, 3) == 120

    def test_n_choose_0(self):
        assert nCr(5, 0) == 1

    def test_n_choose_n(self):
        assert nCr(5, 5) == 1

    def test_r_greater_than_n(self):
        assert nCr(3, 5) == 0

    def test_negative_r(self):
        assert nCr(5, -1) == 0


class TestHypergeomPMF:
    """Probability of exactly k successes."""

    def test_known_value(self):
        # Drawing exactly 1 red card from {5 red, 55 other} in 7 draws
        # P(X=1) for N=60, K=5, n=7, k=1
        p = hypergeom_pmf(60, 5, 7, 1)
        assert 0.0 < p < 1.0
        # Should be roughly 0.39 (known hypergeometric value)
        assert abs(p - 0.39) < 0.02

    def test_impossible_draw(self):
        # Drawing 6 red cards when only 5 exist
        p = hypergeom_pmf(60, 5, 7, 6)
        assert p == 0.0

    def test_guaranteed_draw(self):
        # All cards are successes — drawing 7 from 60 where all 60 are successes
        p = hypergeom_pmf(60, 60, 7, 7)
        assert abs(p - 1.0) < 1e-10


class TestHypergeomCDF:
    """Probability of at least k successes."""

    def test_at_least_zero(self):
        assert hypergeom_cdf_at_least(60, 5, 7, 0) == 1.0

    def test_more_than_population(self):
        # Need 6 successes but only 5 exist
        assert hypergeom_cdf_at_least(60, 5, 7, 6) == 0.0

    def test_at_least_one_land(self):
        # With 24 lands in 60 cards, chance of drawing at least 1 in 7 cards
        p = hypergeom_cdf_at_least(60, 24, 7, 1)
        # Should be very high (~99%)
        assert p > 0.95

    def test_monotonic_decrease(self):
        """P(X ≥ k) should decrease as k increases."""
        probs = [hypergeom_cdf_at_least(60, 20, 7, k) for k in range(8)]
        for i in range(len(probs) - 1):
            assert probs[i] >= probs[i + 1]


class TestManaRequirements:
    """Frank Karsten-style mana calculations."""

    def test_single_pip_turn_1(self):
        # Casting a {R} spell on turn 1 with 20 red sources in 60 cards
        prob = calculate_mana_requirements(60, 1, 1, 20)
        # Should be very high probability
        assert prob > 80.0

    def test_double_pip_hard(self):
        # Casting {W}{W} on turn 2 with only 8 white sources — should be hard
        prob = calculate_mana_requirements(60, 2, 2, 8)
        assert prob < 80.0  # Difficult with few sources

    def test_returns_percentage(self):
        prob = calculate_mana_requirements(60, 3, 1, 24)
        assert 0.0 <= prob <= 100.0

    def test_more_sources_better(self):
        """More sources = higher probability."""
        p_low = calculate_mana_requirements(60, 3, 1, 10)
        p_high = calculate_mana_requirements(60, 3, 1, 20)
        assert p_high > p_low
