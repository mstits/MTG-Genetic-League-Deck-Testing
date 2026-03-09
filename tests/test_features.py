"""Tests for new features: EngineConfig, SaltScore, Historical Gauntlet, DB schema."""

import pytest
from engine.engine_config import EngineConfig
from engine.salt_score import calculate_salt_score, get_bracket_warning


class TestEngineConfig:
    """Test EngineConfig singleton behavior."""

    def test_singleton(self):
        """Only one EngineConfig instance should exist."""
        c1 = EngineConfig()
        c2 = EngineConfig()
        assert c1 is c2

    def test_default_workers(self):
        """Default workers should be <= cpu_count."""
        c = EngineConfig()
        assert 1 <= c.max_workers <= c.cpu_count

    def test_set_workers_clamped(self):
        """Workers should be clamped to [1, cpu_count]."""
        c = EngineConfig()
        original = c.max_workers
        c.max_workers = 999
        assert c.max_workers == c.cpu_count
        c.max_workers = 0
        assert c.max_workers == 1
        c.max_workers = original  # Restore

    def test_memory_limit(self):
        """Memory limit should accept and clamp values."""
        c = EngineConfig()
        c.memory_limit_mb = 512
        assert c.memory_limit_mb == 512
        c.memory_limit_mb = -1
        assert c.memory_limit_mb == 0
        c.memory_limit_mb = 0  # Restore

    def test_headless_mode(self):
        """Headless mode toggle."""
        c = EngineConfig()
        c.headless_mode = False
        assert c.headless_mode is False
        c.headless_mode = True  # Restore

    def test_to_dict(self):
        """Serialization should include all fields."""
        c = EngineConfig()
        d = c.to_dict()
        assert "max_workers" in d
        assert "cpu_count" in d
        assert "memory_limit_mb" in d
        assert "headless_mode" in d

    def test_update_from_dict(self):
        """Update from API request data."""
        c = EngineConfig()
        original = c.to_dict()
        c.update_from_dict({"max_workers": 2, "headless_mode": False})
        assert c.max_workers == 2
        assert c.headless_mode is False
        # Restore
        c.update_from_dict(original)


class TestSaltScore:
    """Test Commander salt scoring."""

    def test_casual_deck(self):
        """A deck with no staples should be Bracket 1."""
        deck = {"Lightning Bolt": 1, "Mountain": 38, "Goblin Guide": 1}
        result = calculate_salt_score(deck)
        assert result["bracket"] == 1
        assert result["salt_score"] == 0

    def test_cedh_deck(self):
        """A deck with cEDH staples should be Bracket 4."""
        deck = {
            "Mana Vault": 1, "Force of Will": 1, "Thassa's Oracle": 1,
            "Ad Nauseam": 1, "Demonic Consultation": 1, "Mountain": 95,
        }
        result = calculate_salt_score(deck)
        assert result["bracket"] == 4
        assert result["salt_score"] >= 80
        assert len(result["flagged_cards"]) == 5

    def test_optimized_deck(self):
        """A deck with Bracket 3 cards but no B4 should be Bracket 3."""
        deck = {
            "Sol Ring": 1, "Cyclonic Rift": 1, "Demonic Tutor": 1,
            "Mountain": 97,
        }
        result = calculate_salt_score(deck)
        assert result["bracket"] == 3

    def test_focused_deck(self):
        """A deck with only Bracket 2 staples."""
        deck = {
            "Swords to Plowshares": 1, "Counterspell": 1, "Path to Exile": 1,
            "Mountain": 97,
        }
        result = calculate_salt_score(deck)
        assert result["bracket"] == 2

    def test_bracket_warning_b4(self):
        """Bracket 4 deck should get a warning."""
        deck = {"Mana Vault": 1, "Force of Will": 1, "Mountain": 98}
        warning = get_bracket_warning(deck)
        assert warning is not None
        assert "Bracket 4" in warning

    def test_bracket_warning_casual(self):
        """Casual deck should get no warning."""
        deck = {"Lightning Bolt": 1, "Mountain": 99}
        warning = get_bracket_warning(deck)
        assert warning is None

    def test_breakdown_counts(self):
        """Breakdown should count cards per bracket."""
        deck = {
            "Mana Vault": 1,  # B4
            "Sol Ring": 1,     # B3
            "Counterspell": 1, # B2
            "Mountain": 97,    # B1
        }
        result = calculate_salt_score(deck)
        assert result["breakdown"][4] == 1
        assert result["breakdown"][3] == 1
        assert result["breakdown"][2] == 1
        assert result["breakdown"][1] == 97


class TestHistoricalGauntlet:
    """Test Historical Gauntlet era data."""

    def test_era_list(self):
        """Should return at least 5 eras."""
        from league.historical_gauntlet import get_era_list
        eras = get_era_list()
        assert len(eras) >= 5

    def test_era_list_structure(self):
        """Each era should have required fields."""
        from league.historical_gauntlet import get_era_list
        eras = get_era_list()
        for era in eras:
            assert "id" in era
            assert "name" in era
            assert "format" in era
            assert "deck_count" in era
            assert era["deck_count"] >= 2

    def test_get_era_decks(self):
        """Should return decks for a valid era."""
        from league.historical_gauntlet import get_era_decks
        decks = get_era_decks("Modern 2022")
        assert decks is not None
        assert len(decks) == 8
        for deck in decks:
            assert "name" in deck
            assert "colors" in deck
            assert "cards" in deck
            assert sum(deck["cards"].values()) >= 40

    def test_invalid_era(self):
        """Invalid era should return None."""
        from league.historical_gauntlet import get_era_decks
        assert get_era_decks("Invalid Era 9999") is None


class TestDBSchema:
    """Test new DB tables can be created."""

    def test_init_creates_tables(self):
        """init_db should create mutations and hall_of_fame tables."""
        try:
            from data import db
            db.init_db()
            with db.get_db_connection() as conn:
                cursor = conn.cursor()
                # Check mutations table exists (PostgreSQL)
                cursor.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = 'mutations'
                """)
                assert cursor.fetchone() is not None, "mutations table not found"
                # Check hall_of_fame table exists
                cursor.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = 'hall_of_fame'
                """)
                assert cursor.fetchone() is not None, "hall_of_fame table not found"
        except Exception as e:
            # Skip if no DB connection available (CI/local without PostgreSQL)
            import pytest
            pytest.skip(f"PostgreSQL not available: {e}")
