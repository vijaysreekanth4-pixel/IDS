"""
test_parser.py
--------------
Unit tests for src/parser.py.

Covers: valid input, invalid input, empty input, duplicate events,
        out-of-order events, optional message field.
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest

from src.parser import count_parse_errors, parse_log_text, stream_events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_log(*lines: str) -> str:
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Valid input
# ---------------------------------------------------------------------------

class TestValidInput:
    def test_single_valid_event(self):
        text = make_log("2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS")
        result = parse_log_text(text)
        assert "DEVICE-01" in result
        assert len(result["DEVICE-01"]) == 1
        event = result["DEVICE-01"][0]
        assert event.device_id == "DEVICE-01"
        assert event.event_type == "CONNECTION_START"
        assert event.status == "SUCCESS"
        assert event.timestamp == datetime(2026, 9, 1, 10, 0, 1)

    def test_multiple_events_same_device(self):
        text = make_log(
            "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS",
            "2026-09-01 10:00:02 DEVICE-01 AUTHENTICATION SUCCESS",
            "2026-09-01 10:00:03 DEVICE-01 SESSION_START SUCCESS",
            "2026-09-01 10:00:10 DEVICE-01 DATA_TRANSFER SUCCESS",
        )
        result = parse_log_text(text)
        assert len(result["DEVICE-01"]) == 4

    def test_multiple_devices(self):
        text = make_log(
            "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS",
            "2026-09-01 10:01:01 DEVICE-02 CONNECTION_START SUCCESS",
            "2026-09-01 10:02:00 DEVICE-03 CONNECTION_START SUCCESS",
        )
        result = parse_log_text(text)
        assert set(result.keys()) == {"DEVICE-01", "DEVICE-02", "DEVICE-03"}

    def test_event_with_optional_message(self):
        text = make_log(
            "2026-09-01 10:00:01 DEVICE-01 DATA_TRANSFER timeout detected FAILED"
        )
        result = parse_log_text(text)
        event = result["DEVICE-01"][0]
        assert event.message == "timeout detected"
        assert event.status == "FAILED"

    def test_device_id_uppercased(self):
        text = make_log("2026-09-01 10:00:01 device-99 CONNECTION_START SUCCESS")
        result = parse_log_text(text)
        assert "DEVICE-99" in result

    def test_failed_status_parsed(self):
        text = make_log("2026-09-01 10:00:01 DEVICE-01 SESSION_START FAILED")
        result = parse_log_text(text)
        assert result["DEVICE-01"][0].status == "FAILED"


# ---------------------------------------------------------------------------
# Invalid / malformed input
# ---------------------------------------------------------------------------

class TestInvalidInput:
    def test_too_few_tokens_skipped(self):
        text = make_log("BADLINE", "2026-09-01 ONLY_FOUR_FIELDS DEVICE MISSING")
        result = parse_log_text(text)
        assert result == {}

    def test_bad_timestamp_skipped(self):
        text = make_log("not-a-date 99:99:99 DEVICE-01 EVENT STATUS")
        result = parse_log_text(text)
        assert result == {}

    def test_mixed_valid_invalid(self):
        text = make_log(
            "BADLINE",
            "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS",
        )
        result = parse_log_text(text)
        assert "DEVICE-01" in result
        assert len(result["DEVICE-01"]) == 1

    def test_parse_error_count(self):
        text = make_log(
            "BADLINE",
            "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS",
            "ANOTHER_BAD_LINE",
        )
        errors = count_parse_errors(text)
        assert errors == 2


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_string(self):
        result = parse_log_text("")
        assert result == {}

    def test_only_comments(self):
        text = make_log("# This is a comment", "# Another comment")
        result = parse_log_text(text)
        assert result == {}

    def test_only_blank_lines(self):
        text = "\n\n\n"
        result = parse_log_text(text)
        assert result == {}


# ---------------------------------------------------------------------------
# Duplicate events
# ---------------------------------------------------------------------------

class TestDuplicateEvents:
    def test_exact_duplicate_removed(self):
        line = "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS"
        text = make_log(line, line, line)
        result = parse_log_text(text)
        assert len(result["DEVICE-01"]) == 1

    def test_near_duplicate_different_status_kept(self):
        text = make_log(
            "2026-09-01 10:00:01 DEVICE-01 SESSION_START SUCCESS",
            "2026-09-01 10:00:01 DEVICE-01 SESSION_START FAILED",
        )
        result = parse_log_text(text)
        # Different status → different dedup key → both kept
        assert len(result["DEVICE-01"]) == 2


# ---------------------------------------------------------------------------
# Out-of-order events
# ---------------------------------------------------------------------------

class TestOutOfOrderEvents:
    def test_events_sorted_by_timestamp(self):
        text = make_log(
            "2026-09-01 10:00:10 DEVICE-01 DATA_TRANSFER SUCCESS",
            "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS",
            "2026-09-01 10:00:02 DEVICE-01 AUTHENTICATION SUCCESS",
        )
        result = parse_log_text(text)
        events = result["DEVICE-01"]
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)
        assert events[0].event_type == "CONNECTION_START"
        assert events[-1].event_type == "DATA_TRANSFER"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreamEvents:
    def test_stream_yields_events(self):
        text = "2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS\n"
        events = list(stream_events(io.StringIO(text)))
        assert len(events) == 1

    def test_stream_skips_blank_lines(self):
        text = "\n\n2026-09-01 10:00:01 DEVICE-01 CONNECTION_START SUCCESS\n\n"
        events = list(stream_events(io.StringIO(text)))
        assert len(events) == 1
