"""Centralized logging configuration with automatic log rotation.

Import this module early (e.g., in discover_decks.py or web/app.py) to
configure rotating file handlers that prevent log files from growing
unbounded.

Usage:
    import logging_config  # Just importing sets up rotation
"""

import os
import logging
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Main league log — 10 MB max, keep 5 backups (50 MB total)
league_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "league.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
)
league_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

# Simulation-specific log
sim_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "simulation.log"),
    maxBytes=5 * 1024 * 1024,  # 5 MB
    backupCount=3,
    encoding="utf-8",
)
sim_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))

# Console handler (INFO level)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(levelname)s: %(message)s"
))

# Configure root logger
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(league_handler)
root.addHandler(console_handler)

# Simulation logger gets its own file
sim_logger = logging.getLogger("simulation")
sim_logger.addHandler(sim_handler)

# Engine logger
engine_logger = logging.getLogger("engine")
engine_logger.addHandler(sim_handler)

import sys

class StreamToLogger:
    """Fake file-like stream object that redirects writes to a logger instance."""
    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self):
        pass

# Only hijack if not already hijacked
if not isinstance(sys.stdout, StreamToLogger):
    sys.stdout = StreamToLogger(root, logging.INFO)
    sys.stderr = StreamToLogger(root, logging.ERROR)
