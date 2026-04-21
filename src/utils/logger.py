"""
Structured logging — every trade decision, signal, and risk check
gets logged with full context for post-session review.
"""

import logging
import json
import sys
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from config import settings


class StructuredFormatter(logging.Formatter):
    """Outputs JSON-structured log lines for machine parsing."""

    def format(self, record):
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, "trade_data"):
            entry["trade"] = record.trade_data
        if hasattr(record, "signal_data"):
            entry["signal"] = record.signal_data
        if hasattr(record, "risk_data"):
            entry["risk"] = record.risk_data
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class ReadableFormatter(logging.Formatter):
    """Human-readable format for console output."""

    FORMAT = "%(asctime)s │ %(levelname)-7s │ %(name)-18s │ %(message)s"

    def __init__(self):
        super().__init__(self.FORMAT, datefmt="%H:%M:%S")


def setup_logging() -> logging.Logger:
    """Configure root logger with console + file handlers."""
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("tradingapp")
    root.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    # Console: human-readable
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(ReadableFormatter())
    root.addHandler(console)

    # File: structured JSON, rotated at 10MB, keep 30 files
    file_handler = RotatingFileHandler(
        log_dir / "trading.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=30,
    )
    file_handler.setFormatter(StructuredFormatter())
    root.addHandler(file_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Get a child logger for a specific module."""
    return logging.getLogger(f"tradingapp.{name}")
