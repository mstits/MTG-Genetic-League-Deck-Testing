"""Fidelity Report — data structures for rules validation outcomes.

Captures pass/fail results from the Rules Sandbox Gauntlet, including
full game-state dumps for debugging failures and generating reports.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import json
import time


class FidelityError(Exception):
    """Raised when rules fidelity check fails, halting GA evolution."""
    def __init__(self, report: 'FidelityReport'):
        self.report = report
        failed_ids = [f.scenario_id for f in report.failures[:5]]
        super().__init__(
            f"Fidelity check failed: {report.failed}/{report.total_scenarios} scenarios "
            f"deviated from CR. First failures: {failed_ids}"
        )


@dataclass
class FidelityResult:
    """Outcome of a single scenario replay."""
    scenario_id: str
    scenario_name: str
    passed: bool
    rule_refs: List[str]
    expected_state: Dict[str, Any]
    actual_state: Dict[str, Any]
    deviation: str = ""         # Human-readable explanation of what went wrong
    board_state_dump: Dict[str, Any] = field(default_factory=dict)
    replay_index: int = 0       # Which of the 1000 replays failed
    variation_desc: str = ""    # Description of the board variation applied

    def to_dict(self) -> dict:
        return {
            'scenario_id': self.scenario_id,
            'scenario_name': self.scenario_name,
            'passed': self.passed,
            'rule_refs': self.rule_refs,
            'expected': self.expected_state,
            'actual': self.actual_state,
            'deviation': self.deviation,
            'replay_index': self.replay_index,
            'variation': self.variation_desc,
        }


@dataclass
class FidelityReport:
    """Aggregate report from a full gauntlet run."""
    total_scenarios: int = 0
    total_replays: int = 0
    passed: int = 0
    failed: int = 0
    failures: List[FidelityResult] = field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: str = ""

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        status = "✅ ALL PASS" if self.all_passed else f"❌ {self.failed} FAILURES"
        lines = [
            f"═══ Rules Sandbox Gauntlet ═══",
            f"Status:    {status}",
            f"Scenarios: {self.passed}/{self.total_scenarios} passed",
            f"Replays:   {self.total_replays:,} total",
            f"Duration:  {self.duration_seconds:.2f}s",
        ]
        if self.failures:
            lines.append(f"\n── Fidelity Failures ──")
            for f in self.failures[:20]:
                lines.append(
                    f"  [{f.scenario_id}] {f.scenario_name}\n"
                    f"    Rules: {', '.join(f.rule_refs)}\n"
                    f"    Deviation: {f.deviation}\n"
                    f"    Expected: {f.expected_state}\n"
                    f"    Actual:   {f.actual_state}"
                )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            'total_scenarios': self.total_scenarios,
            'total_replays': self.total_replays,
            'passed': self.passed,
            'failed': self.failed,
            'duration_seconds': self.duration_seconds,
            'timestamp': self.timestamp,
            'all_passed': self.all_passed,
            'failures': [f.to_dict() for f in self.failures],
        }, indent=2)

    def to_markdown(self) -> str:
        status = "✅ ALL PASS" if self.all_passed else f"❌ {self.failed} FAILURES"
        lines = [
            f"# Rules Sandbox Gauntlet Report",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Status | {status} |",
            f"| Scenarios | {self.passed}/{self.total_scenarios} passed |",
            f"| Total Replays | {self.total_replays:,} |",
            f"| Duration | {self.duration_seconds:.2f}s |",
            f"| Timestamp | {self.timestamp} |",
        ]
        if self.failures:
            lines += [
                f"",
                f"## Fidelity Failures",
                f"",
                f"| ID | Name | Rules | Deviation |",
                f"|----|------|-------|-----------|",
            ]
            for f in self.failures:
                rules = ", ".join(f.rule_refs)
                lines.append(f"| {f.scenario_id} | {f.scenario_name} | {rules} | {f.deviation} |")
        return "\n".join(lines)

    def save_report(self, path: str = "data/fidelity_report.json"):
        """Save report to disk."""
        import os
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(self.to_json())
