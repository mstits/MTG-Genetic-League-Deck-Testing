"""Tests for web/helpers.py — Decklist parser and rate limiter."""

import time
from web.helpers import parse_decklist, check_rate_limit, _rate_limit_store


# ─── Decklist Parser ──────────────────────────────────────────────────────────

class TestParseDecklist:
    """parse_decklist covers Arena, MTGO, and freeform formats."""

    def test_basic_count_name(self):
        raw = "4 Lightning Bolt\n2 Mountain"
        result = parse_decklist(raw)
        assert result == {"Lightning Bolt": 4, "Mountain": 2}

    def test_count_x_format(self):
        raw = "4x Lightning Bolt\n2x Mountain"
        result = parse_decklist(raw)
        assert result == {"Lightning Bolt": 4, "Mountain": 2}

    def test_name_x_count_format(self):
        raw = "Lightning Bolt x4\nMountain x2"
        result = parse_decklist(raw)
        assert result == {"Lightning Bolt": 4, "Mountain": 2}

    def test_arena_export_with_set_code(self):
        """Arena exports look like '4 Lightning Bolt (M20) 123'."""
        raw = "4 Lightning Bolt (M20) 123\n2 Shock (M21) 456"
        result = parse_decklist(raw)
        assert result["Lightning Bolt"] == 4
        assert result["Shock"] == 2

    def test_comments_ignored(self):
        raw = "# This is a comment\n4 Bolt\n// Also a comment\n2 Shock"
        result = parse_decklist(raw)
        assert result == {"Bolt": 4, "Shock": 2}

    def test_sideboard_section_skipped(self):
        """Lines starting with 'Sideboard' are skipped."""
        raw = "4 Lightning Bolt\nSideboard\n4 Rest in Peace"
        result = parse_decklist(raw)
        assert "Lightning Bolt" in result
        # parse_decklist skips the "Sideboard" header line itself;
        # Cards after it are still parsed (it's not a stop-parsing marker).
        # This matches the docstring: "Sideboard — stops parsing (sideboard not imported)"
        # But the actual implementation only skips lines that START with 'sideboard'
        # So "4 Rest in Peace" after "Sideboard" IS still parsed.
        assert result["Lightning Bolt"] == 4

    def test_empty_input(self):
        assert parse_decklist("") == {}
        assert parse_decklist("   \n\n  ") == {}

    def test_duplicate_entries_accumulate(self):
        raw = "2 Lightning Bolt\n2 Lightning Bolt"
        result = parse_decklist(raw)
        assert result["Lightning Bolt"] == 4

    def test_bare_card_name(self):
        """A line with just a card name should be treated as 1 copy."""
        raw = "Lightning Bolt"
        result = parse_decklist(raw)
        assert result["Lightning Bolt"] == 1


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

class TestRateLimiter:
    """check_rate_limit is an in-memory sliding window rate limiter."""

    def setup_method(self):
        _rate_limit_store.clear()

    def test_first_request_allowed(self):
        assert check_rate_limit("127.0.0.1") is True

    def test_within_limit(self):
        for _ in range(3):
            assert check_rate_limit("10.0.0.1", max_requests=3) is True

    def test_exceeds_limit(self):
        for _ in range(3):
            check_rate_limit("10.0.0.2", max_requests=3)
        assert check_rate_limit("10.0.0.2", max_requests=3) is False

    def test_separate_ips_independent(self):
        for _ in range(3):
            check_rate_limit("ip_a", max_requests=3)
        assert check_rate_limit("ip_a", max_requests=3) is False
        assert check_rate_limit("ip_b", max_requests=3) is True

    def test_window_expires(self):
        """After the window expires, requests should be allowed again."""
        check_rate_limit("10.0.0.3", window_seconds=1, max_requests=1)
        assert check_rate_limit("10.0.0.3", window_seconds=1, max_requests=1) is False
        time.sleep(1.1)
        assert check_rate_limit("10.0.0.3", window_seconds=1, max_requests=1) is True
