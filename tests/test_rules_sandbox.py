"""Tests for the Rules Sandbox Gauntlet.

Validates scenario registry integrity, gauntlet execution,
fidelity reporting, and GA halt mechanism.
"""

import pytest
from engine.rules_sandbox import SCENARIO_REGISTRY, run_gauntlet, run_quick_fidelity_check
from engine.fidelity_report import FidelityReport, FidelityError


class TestScenarioRegistry:
    """Validate the scenario registry has correct structure."""

    def test_registry_count(self):
        """Registry has at least 100 scenarios."""
        assert len(SCENARIO_REGISTRY) >= 100, \
            f"Expected >= 100 scenarios, got {len(SCENARIO_REGISTRY)}"

    def test_unique_ids(self):
        """All scenario IDs are unique."""
        ids = [s.id for s in SCENARIO_REGISTRY]
        assert len(ids) == len(set(ids)), "Duplicate scenario IDs found"

    def test_all_have_setup(self):
        """Every scenario has a setup function."""
        for s in SCENARIO_REGISTRY:
            assert callable(s.setup), f"{s.id} has no setup function"

    def test_all_have_expected(self):
        """Every scenario has an expected/check function."""
        for s in SCENARIO_REGISTRY:
            assert callable(s.expected), f"{s.id} has no expected function"

    def test_all_have_rule_refs(self):
        """Every scenario references at least one CR rule."""
        for s in SCENARIO_REGISTRY:
            assert len(s.rule_refs) > 0, f"{s.id} has no rule references"

    def test_all_have_names(self):
        """Every scenario has a non-empty name."""
        for s in SCENARIO_REGISTRY:
            assert s.name, f"{s.id} has no name"

    def test_categories_covered(self):
        """All 10 categories are represented."""
        cats = set(s.category for s in SCENARIO_REGISTRY)
        expected = {
            "layer_7_pt", "damage_toughness", "stack_ordering",
            "replacement_effects", "state_based_actions",
            "protection_hexproof", "counters_tokens",
            "triggers_priority", "keyword_interactions",
        }
        missing = expected - cats
        assert not missing, f"Missing categories: {missing}"


class TestGauntletExecution:
    """Test the replay harness runs correctly."""

    def test_quick_check_runs(self):
        """Quick check (1 replay) completes without error."""
        report = run_quick_fidelity_check()
        assert isinstance(report, FidelityReport)
        assert report.total_scenarios >= 100

    def test_quick_check_passes(self):
        """All scenarios pass the quick check."""
        report = run_quick_fidelity_check()
        assert report.all_passed, \
            f"Failures: {[(f.scenario_id, f.deviation) for f in report.failures]}"

    def test_replay_10x(self):
        """10x replay completes and passes."""
        report = run_gauntlet(replays=10)
        assert report.all_passed, \
            f"Failures: {[(f.scenario_id, f.deviation) for f in report.failures]}"

    def test_subset_run(self):
        """Can run a subset of scenarios by ID."""
        report = run_gauntlet(scenarios=["L7-001", "DT-001", "CBT-001"], replays=5)
        assert report.total_scenarios == 3

    def test_report_has_timing(self):
        """Report includes duration measurement."""
        report = run_quick_fidelity_check()
        assert report.duration_seconds >= 0


class TestFidelityReporting:
    """Test report output formats."""

    def test_json_output(self):
        """Report generates valid JSON."""
        import json
        report = run_quick_fidelity_check()
        data = json.loads(report.to_json())
        assert 'total_scenarios' in data
        assert 'all_passed' in data

    def test_markdown_output(self):
        """Report generates markdown with table."""
        report = run_quick_fidelity_check()
        md = report.to_markdown()
        assert "Rules Sandbox Gauntlet" in md
        assert "Status" in md

    def test_summary_output(self):
        """Report summary is human-readable."""
        report = run_quick_fidelity_check()
        summary = report.summary()
        assert "Scenarios" in summary


class TestGAHalt:
    """Test that GA evolution halts on fidelity failure."""

    def test_fidelity_error_class(self):
        """FidelityError carries the report."""
        report = FidelityReport(total_scenarios=1, failed=1)
        err = FidelityError(report)
        assert err.report is report
        assert "1/1" in str(err)
