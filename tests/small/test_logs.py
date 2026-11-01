"""Tests for reellife.logs.JsonFormatter."""

import json
import logging
import sys

from reellife import logs


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "reellife.test", logging.INFO, "f.py", 1, message, (), None
    )
    record.__dict__.update(extra)
    record.created = 0.0
    return record


def test_formats_one_json_object() -> None:
    line = logs.JsonFormatter().format(_record("hello %s"))
    assert json.loads(line) == {
        "time": "1970-01-01T00:00:00.000+00:00",
        "level": "INFO",
        "logger": "reellife.test",
        "message": "hello %s",
    }


def test_includes_audit_detail() -> None:
    line = logs.JsonFormatter().format(
        _record("audit", audit={"action": "login"})
    )
    assert json.loads(line)["audit"] == {"action": "login"}


def test_includes_exceptions() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("failed")
        record.exc_info = sys.exc_info()
    entry = json.loads(logs.JsonFormatter().format(record))
    assert "ValueError: boom" in entry["exception"]
