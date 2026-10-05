"""
parser.py
---------
Streaming event log parser.

Design goals
------------
* Memory-efficient: reads input line-by-line; never loads the full dataset.
* Resilient: malformed lines are logged and skipped.
* Correct: timestamps parsed with timezone-naive UTC; duplicates removed;
  per-device events sorted chronologically.

Log format (space-delimited, minimum 5 tokens)
----------------------------------------------
  <DATE> <TIME> <DEVICE_ID> <EVENT_TYPE> [<MESSAGE> ...] <STATUS>

  e.g.  2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS
        2026-09-01 10:01:03 DEVICE-02 SESSION_START FAILED
        2026-09-01 10:02:00 DEVICE-03 DATA_TRANSFER timeout detected FAILED
"""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Generator, IO, Iterable, List, Optional, Tuple

from .models import Event

logger = logging.getLogger(__name__)

# Minimum number of whitespace-separated tokens required on a valid log line.
_MIN_TOKENS = 5

# Known status tokens (case-insensitive match).
_KNOWN_STATUSES = {
    "SUCCESS", "FAILED", "STARTED", "STOPPED",
    "ERROR", "TIMEOUT", "OK", "UNKNOWN",
}


def _normalize_status(raw: str) -> str:
    """Uppercase the status token for consistent comparison."""
    return raw.upper()


def _parse_line(line: str, line_no: int) -> Optional[Event]:
    """
    Parse a single log line into an :class:`Event`.

    Returns ``None`` (and emits a warning) if the line is malformed.
    """
    line = line.strip()

    # Skip blank lines and comment lines
    if not line or line.startswith("#"):
        return None

    tokens = line.split()

    if len(tokens) < _MIN_TOKENS:
        logger.warning(
            "Line %d skipped – too few fields (%d < %d): %r",
            line_no, len(tokens), _MIN_TOKENS, line,
        )
        return None

    # tokens[0] = date, tokens[1] = time, tokens[2] = device_id,
    # tokens[3] = event_type, tokens[-1] = status,
    # tokens[4:-1] = optional message parts
    date_str = tokens[0]
    time_str = tokens[1]
    device_id = tokens[2].upper()
    event_type = tokens[3].upper()
    status_raw = tokens[-1]
    message_parts = tokens[4:-1]

    # --- Timestamp ---
    try:
        timestamp = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        logger.warning(
            "Line %d skipped – invalid timestamp '%s %s': %r",
            line_no, date_str, time_str, line,
        )
        return None

    # --- Device ID basic validation ---
    if not device_id:
        logger.warning("Line %d skipped – empty device_id: %r", line_no, line)
        return None

    # --- Status ---
    status = _normalize_status(status_raw)

    # Warn but still keep the event if status is unrecognised
    if status not in _KNOWN_STATUSES:
        logger.debug(
            "Line %d – unrecognised status token '%s'; keeping as-is.", line_no, status,
        )

    # --- Optional message ---
    message: Optional[str] = " ".join(message_parts) if message_parts else None

    return Event(
        timestamp=timestamp,
        device_id=device_id,
        event_type=event_type,
        message=message,
        status=status,
        line_number=line_no,
    )


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def _dedup_key(event: Event) -> Tuple:
    """Unique key used for deduplication."""
    return (event.timestamp, event.device_id, event.event_type, event.status)


# ---------------------------------------------------------------------------
# Public streaming API
# ---------------------------------------------------------------------------

def stream_events(source: IO[str]) -> Generator[Event, None, None]:
    """
    Yield valid :class:`Event` objects from an open text stream, one at a time.

    This generator is the core streaming primitive: it processes the source
    line-by-line and never holds more than a single line in memory, making it
    suitable for arbitrarily large files.

    Duplicates within the stream are removed via a lightweight hash set.
    """
    seen: set = set()
    for line_no, raw_line in enumerate(source, start=1):
        event = _parse_line(raw_line, line_no)
        if event is None:
            continue
        key = _dedup_key(event)
        if key in seen:
            logger.debug("Line %d – duplicate event removed: %r", line_no, key)
            continue
        seen.add(key)
        yield event


def parse_log_file(filepath: str) -> Dict[str, List[Event]]:
    """
    Parse a log file on disk and return a dict mapping device_id → sorted events.

    The file is read via :func:`stream_events` so only one line is in memory
    at a time (plus the accumulated per-device event lists).

    For extremely large files where even the accumulated per-device lists
    cannot fit in RAM, use :func:`stream_events` directly and feed events
    into a database or external sort.
    """
    device_events: Dict[str, List[Event]] = defaultdict(list)
    parse_errors = 0

    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for event in stream_events(fh):
            device_events[event.device_id].append(event)

    # Sort each device's events chronologically
    for device_id in device_events:
        device_events[device_id].sort(key=lambda e: e.timestamp)

    return dict(device_events)


def parse_log_text(text: str) -> Dict[str, List[Event]]:
    """
    Parse a raw log string (e.g. from an API request body) the same way as a
    file, using an in-memory stream so the streaming contract is preserved.
    """
    device_events: Dict[str, List[Event]] = defaultdict(list)

    with io.StringIO(text) as fh:
        for event in stream_events(fh):
            device_events[event.device_id].append(event)

    for device_id in device_events:
        device_events[device_id].sort(key=lambda e: e.timestamp)

    return dict(device_events)


def count_parse_errors(text: str) -> int:
    """Return the number of lines that could not be parsed."""
    errors = 0
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if len(tokens) < _MIN_TOKENS:
            errors += 1
            continue
        try:
            datetime.strptime(f"{tokens[0]} {tokens[1]}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            errors += 1
    return errors
