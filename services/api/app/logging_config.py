"""Structured, privacy-aware logging.

Logs are written as single-line JSON so that they can be inspected locally
without a log aggregator.  Document text, prompts, student answers and model
output are never logged unless ``log_sensitive_content`` is explicitly enabled.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEYS = {
    "answer",
    "api_key",
    "authorization",
    "content",
    "document_text",
    "prompt",
    "question",
    "student_answer",
    "text",
    "token",
}

_RESERVED = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, *, allow_sensitive: bool = False) -> None:
        super().__init__()
        self.allow_sensitive = allow_sensitive

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if not self.allow_sensitive and key.lower() in SENSITIVE_KEYS:
                payload[key] = "<redacted>"
            else:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", *, allow_sensitive: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(allow_sensitive=allow_sensitive))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
