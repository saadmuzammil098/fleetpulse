"""JSON structured logging for the Fleet Health Risk API.

Every request and every prediction logs one JSON line — vehicle id,
window size, latency, model version, risk score, action — instead of a
free-text message, so log lines are queryable/aggregatable by a fleet-ops
observability stack rather than grep'd.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

RESERVED_LOG_RECORD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in RESERVED_LOG_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int | None = None) -> None:
    # Task 6 addition: LOG_LEVEL is real, operator-facing config (the
    # Task 6 ConfigMap sets it) — not a hardcoded default pretending to be
    # configurable. Falls back to INFO if unset or unrecognized, same as
    # before this env lookup existed.
    if level is None:
        level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    logging.getLogger("fleet_health_api").setLevel(level)
