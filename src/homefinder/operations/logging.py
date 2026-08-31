"""Small JSON logging setup with conservative secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

_SECRET_KEYS = re.compile(
    r"(?:password|token|secret|authorization|api[_-]?key|database[_-]?url)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:Bearer\s+\S+|(?:[\w.-]*(?:password|token|secret|api[_-]?key))=\S+)",
    re.IGNORECASE,
)
_SECRET_URI = re.compile(r"(://[^/\s:]+:)[^@\s]+(@)")


def redact(value: Any, *, key: str = "") -> Any:
    if key and _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(name): redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_URI.sub(
            r"\1[REDACTED]\2", _SECRET_VALUE.sub("[REDACTED]", value)
        )
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        reserved = {
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
            "thread",
            "threadName",
        }
        fields = {
            name: value
            for name, value in record.__dict__.items()
            if name not in reserved and not name.startswith("_")
        }
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": redact(record.getMessage()),
        }
        payload.update(redact(fields))
        return json.dumps(payload, default=str, separators=(",", ":"))


def setup_logging(level: str = "INFO", *, force: bool = False) -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=force)
    return logging.getLogger("homefinder")
