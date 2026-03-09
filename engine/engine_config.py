"""EngineConfig — Runtime-adjustable simulation parameters.

Singleton configuration for controlling resource allocation during
genetic algorithm and league simulations.

Attributes:
    max_workers:     Number of parallel worker processes (1..cpu_count).
    memory_limit_mb: Soft heap cap per worker in MB (0 = unlimited).
    max_turns:       Maximum number of turns before game drawn (default 50).
    max_actions:     Maximum actions before game drawn (default 500).
    headless_mode:   True = max speed (minimal logging), False = verbose
                     board state logging for visualization.
    strict_errors:   True = re-raise genuine code bugs (TypeError, KeyError, etc.)
                     instead of catching them as Error outcomes. Default False.
    error_budget_threshold:
                     Number of error-outcome games per season before triggering
                     a warning. Default 10.

Usage:
    from engine.engine_config import config

    config.max_workers = 4
    config.memory_limit_mb = 512
    config.headless_mode = False
"""

import os
import logging

logger = logging.getLogger(__name__)


class EngineConfig:
    """Runtime-adjustable simulation resource configuration.

    Thread-safe singleton — one instance shared across the application.
    Changes take effect on the next simulation batch (not mid-match).
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        cpu_count = os.cpu_count() or 4
        self._max_workers = min(cpu_count, 8)
        self._cpu_count = cpu_count
        self._memory_limit_mb = 0  # 0 = unlimited
        self._max_turns = 75
        self._max_actions = 2000
        self._headless_mode = True
        self._strict_errors = False
        self._error_budget_threshold = 10
        logger.info("EngineConfig initialized: %d workers, %dMB mem limit, headless=%s",
                     self._max_workers, self._memory_limit_mb, self._headless_mode)

    @property
    def max_workers(self) -> int:
        """Number of parallel worker processes."""
        return self._max_workers

    @max_workers.setter
    def max_workers(self, value: int):
        value = max(1, min(value, self._cpu_count))
        self._max_workers = value
        logger.info("EngineConfig: max_workers set to %d", value)

    @property
    def cpu_count(self) -> int:
        """Total available CPU cores (read-only)."""
        return self._cpu_count

    @property
    def memory_limit_mb(self) -> int:
        """Soft memory limit per worker in MB. 0 = unlimited."""
        return self._memory_limit_mb

    @memory_limit_mb.setter
    def memory_limit_mb(self, value: int):
        value = max(0, value)
        self._memory_limit_mb = value
        logger.info("EngineConfig: memory_limit_mb set to %d", value)

    @property
    def headless_mode(self) -> bool:
        """True = max speed, False = verbose logging for visualization."""
        return self._headless_mode

    @headless_mode.setter
    def headless_mode(self, value: bool):
        self._headless_mode = bool(value)
        logger.info("EngineConfig: headless_mode set to %s", self._headless_mode)

    @property
    def max_turns(self) -> int:
        """Maximum turns before forcing a draw."""
        return self._max_turns

    @max_turns.setter
    def max_turns(self, value: int):
        self._max_turns = max(1, value)
        logger.info("EngineConfig: max_turns set to %d", self._max_turns)

    @property
    def max_actions(self) -> int:
        """Maximum actions before forcing a draw."""
        return self._max_actions

    @max_actions.setter
    def max_actions(self, value: int):
        self._max_actions = max(1, value)
        logger.info("EngineConfig: max_actions set to %d", self._max_actions)

    def to_dict(self) -> dict:
        """Serialize config for API response."""
        return {
            "max_workers": self._max_workers,
            "cpu_count": self._cpu_count,
            "memory_limit_mb": self._memory_limit_mb,
            "max_turns": self._max_turns,
            "max_actions": self._max_actions,
            "headless_mode": self._headless_mode,
            "strict_errors": self._strict_errors,
            "error_budget_threshold": self._error_budget_threshold,
        }

    def update_from_dict(self, data: dict):
        """Update config from API request data."""
        if "max_workers" in data:
            self.max_workers = int(data["max_workers"])
        if "memory_limit_mb" in data:
            self.memory_limit_mb = int(data["memory_limit_mb"])
        if "headless_mode" in data:
            self.headless_mode = bool(data["headless_mode"])
        if "max_turns" in data:
            self.max_turns = int(data["max_turns"])
        if "max_actions" in data:
            self.max_actions = int(data["max_actions"])
        if "strict_errors" in data:
            self.strict_errors = bool(data["strict_errors"])
        if "error_budget_threshold" in data:
            self.error_budget_threshold = int(data["error_budget_threshold"])

    @property
    def strict_errors(self) -> bool:
        """True = re-raise genuine code bugs instead of catching as Error outcomes."""
        return self._strict_errors

    @strict_errors.setter
    def strict_errors(self, value: bool):
        self._strict_errors = bool(value)
        logger.info("EngineConfig: strict_errors set to %s", self._strict_errors)

    @property
    def error_budget_threshold(self) -> int:
        """Number of error-outcome games before triggering a warning."""
        return self._error_budget_threshold

    @error_budget_threshold.setter
    def error_budget_threshold(self, value: int):
        self._error_budget_threshold = max(1, value)
        logger.info("EngineConfig: error_budget_threshold set to %d", self._error_budget_threshold)


# Module-level singleton
config = EngineConfig()
