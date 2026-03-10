"""Tests for engine/mechanics/combat_keywords.py — Evasion, blocking, protection."""

from engine.card import Card
from engine.mechanics.combat_keywords import (
    apply_flying, apply_trample, apply_first_strike, apply_double_strike,
    apply_deathtouch, apply_lifelink, apply_vigilance, apply_haste,
    apply_hexproof, apply_indestructible, apply_menace, apply_reach,
    apply_ward, apply_prowess, apply_flash, apply_undying, apply_persist
)


class TestCombatKeywords:
    """Keyword applicators set the correct boolean flags on a Card."""

    def test_apply_flying(self):
        c = Card("Bird", "{W}", "Creature")
        assert not getattr(c, 'has_flying', False)
        apply_flying(c)
        assert c.has_flying is True

    def test_apply_trample(self):
        c = Card("Beast", "{G}", "Creature")
        apply_trample(c)
        assert c.has_trample is True

    def test_apply_first_strike(self):
        c = Card("Knight", "{W}", "Creature")
        apply_first_strike(c)
        assert c.has_first_strike is True

    def test_apply_double_strike(self):
        c = Card("Paladin", "{W}{W}", "Creature")
        apply_double_strike(c)
        assert c.has_double_strike is True

    def test_apply_deathtouch(self):
        c = Card("Snake", "{B}", "Creature")
        apply_deathtouch(c)
        assert c.has_deathtouch is True

    def test_apply_lifelink(self):
        c = Card("Vampire", "{B}", "Creature")
        apply_lifelink(c)
        assert c.has_lifelink is True

    def test_apply_vigilance(self):
        c = Card("Angel", "{W}", "Creature")
        apply_vigilance(c)
        assert c.has_vigilance is True

    def test_apply_haste(self):
        c = Card("Goblin", "{R}", "Creature")
        apply_haste(c)
        assert c.has_haste is True

    def test_apply_hexproof(self):
        c = Card("Slippery", "{G}", "Creature")
        apply_hexproof(c)
        assert c.has_hexproof is True

    def test_apply_indestructible(self):
        c = Card("God", "{3}", "Creature")
        apply_indestructible(c)
        assert c.has_indestructible is True

    def test_apply_menace(self):
        c = Card("Intimidating", "{B}", "Creature")
        apply_menace(c)
        assert c.has_menace is True

    def test_apply_reach(self):
        c = Card("Spider", "{G}", "Creature")
        apply_reach(c)
        assert c.has_reach is True

    def test_apply_ward(self):
        c = Card("Protected", "{U}", "Creature")
        apply_ward(c)
        assert c.has_ward is True

    def test_apply_prowess(self):
        c = Card("Monk", "{U}", "Creature")
        apply_prowess(c)
        assert c.has_prowess is True

    def test_apply_flash(self):
        c = Card("Surprise", "{U}", "Creature")
        apply_flash(c)
        assert c.has_flash is True

    def test_apply_undying(self):
        c = Card("Zombie", "{B}", "Creature")
        apply_undying(c)
        assert c.has_undying is True

    def test_apply_persist(self):
        c = Card("Spirit", "{B}", "Creature")
        apply_persist(c)
        assert c.has_persist is True
