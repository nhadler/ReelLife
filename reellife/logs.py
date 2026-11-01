"""JSON log lines for stdout.

Containers and journald collect stdout; one JSON object per line keeps
audit events and errors machine-readable.
"""

import datetime
import json
import logging
from typing import Any


class JsonFormatter(logging.Formatter):
    """Formats a record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Returns the record as JSON, with any `audit` extra included."""
        entry: dict[str, Any] = {
            "time": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.UTC
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        audit = getattr(record, "audit", None)
        if audit is not None:
            entry["audit"] = audit
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)
