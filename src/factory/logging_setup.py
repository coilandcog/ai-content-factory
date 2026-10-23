"""Console + JSON-lines logging with secret redaction (CONVENTIONS.md §3).

Modules log through `logging.getLogger("factory.<module>")`. Pass `extra={"event": ...,
"content_id": ...}` for those fields; run_id, stage and channel come from set_log_context().
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LOGGER_NAME = "factory"
JSON_FIELDS = ("ts", "level", "run_id", "stage", "channel", "content_id", "event", "msg")
REDACTED = "***"

# A ContextVar is a variable scoped to the current run of code, not a plain global.
_log_context: ContextVar[dict[str, object] | None] = ContextVar("factory_log_context", default=None)


def set_log_context(**fields: object) -> None:
    """Set run_id / stage / channel for every following log line of this command."""
    _log_context.set({**(_log_context.get() or {}), **fields})


class Redactor:
    """Replaces known secret values with '***' in any text."""

    def __init__(self, secrets: Iterable[str]) -> None:
        # Longest first, so a secret that contains another one is masked whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


class _ContextFilter(logging.Filter):
    """Adds the JSON fields every line must have, taking run_id/stage/channel from the context."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _log_context.get() or {}
        for name in ("run_id", "stage", "channel"):
            if not hasattr(record, name):
                setattr(record, name, context.get(name))
        for name in ("content_id", "event"):
            if not hasattr(record, name):
                setattr(record, name, None)
        return True


class _RedactFilter(logging.Filter):
    """Masks secrets in the message and in any traceback, before a handler writes them."""

    def __init__(self, redact: Redactor) -> None:
        super().__init__()
        self._redact = redact

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = None  # already merged into msg above
        if record.exc_info:
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None  # formatters now use the redacted exc_text
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        entry = {
            "ts": ts.replace("+00:00", "Z"),
            "level": record.levelname,
            "run_id": record.run_id,
            "stage": record.stage,
            "channel": record.channel,
            "content_id": record.content_id,
            "event": record.event,
            "msg": record.getMessage(),
        }
        if record.exc_text:
            entry["exc"] = record.exc_text
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(logs_dir: Path, level: str, timezone: str, secrets: Iterable[str]) -> Redactor:
    """Send 'factory.*' logs to the console and to logs_dir/factory-YYYY-MM-DD.jsonl.

    The file date is the local date in `timezone`, so one night's run lands in one file.
    Returns the Redactor so callers can also mask text they store elsewhere (e.g. runs.error).
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ZoneInfo(timezone)).date().isoformat()
    redact = Redactor(secrets)

    logger = logging.getLogger(LOGGER_NAME)
    for old in list(logger.handlers):  # safe to call again (tests, long-running bot)
        logger.removeHandler(old)
        old.close()
    logger.setLevel(level)
    logger.propagate = False

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    jsonl = logging.FileHandler(logs_dir / f"factory-{day}.jsonl", encoding="utf-8")
    jsonl.setFormatter(_JsonFormatter())
    for handler in (console, jsonl):
        # Filters sit on the handlers so they also see records from child loggers.
        handler.addFilter(_ContextFilter())
        handler.addFilter(_RedactFilter(redact))
        logger.addHandler(handler)
    return redact
