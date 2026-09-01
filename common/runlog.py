"""The single structured logger for all agent code.

Every emitted line is one JSON object carrying a run_id, so one execution —
baseline, solution, or eval harness — traces end to end from its log alone.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TextIO


def new_run_id() -> str:
    """A short unique id stamped on every log line of one execution."""
    return uuid.uuid4().hex[:12]


class _JsonLineFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "run_id": self._run_id,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = self.formatStack(record.stack_info)
        return json.dumps(line, ensure_ascii=False)


def get_logger(run_id: str, name: str = "run", stream: TextIO | None = None) -> logging.Logger:
    """Logger whose every line is one JSON object stamped with run_id.

    Repeated calls with the same (run_id, name) return the same logger without
    stacking handlers; later `stream` arguments are then ignored. The default
    stream is stderr, keeping stdout clean for the report a human reads.
    """
    logger = logging.getLogger(f"{name}.{run_id}")
    if not logger.handlers:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(_JsonLineFormatter(run_id))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
