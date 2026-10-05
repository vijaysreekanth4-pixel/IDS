"""
test_analyzer.py
----------------
Unit tests for src/analyzer.py.

Covers: healthy device, failed events, repeated failures, excessive retries,
        missing events, unexpected sequence, long delay, multiple devices,
        empty event list.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analyzer import (
    DELAY_THRESHOLD_SECONDS,
    RETRY_THRESHOLD,
    analyze_all_devices,
    analyze_device,
)
from src.models import DeviceStatus, Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(
    event_type: str,
    status: str = "SUCCESS",
    device_id: str = "DEVICE-TEST",
    offset_seconds: int = 0,
) -> Event:
    base = datetime(2026, 9, 1, 10, 0, 0) + timedelta(seconds=offset_seconds)
    return Event(
        timestamp=base,
        device_id=device_id,
        event_type=event_type,
        status=status,
    )


def healthy_events(device_id: str = "DEVICE-OK") -> list:
    return [
        make_event("CONNECTION_START", "SUCCESS", device_id, 0),
        make_event("AUTHENTICATION",   "SUCCESS", device_id, 1),
        make_event("SESSION_START",    "SUCCESS", device_id, 2),
        make_event("DATA_TRANSFER",    "SUCCESS", device_id, 10),
    ]


# ---------------------------------------------------------------------------
# Healthy device
# ---------------------------------------------------------------------------

class TestHealthyDevice:
    def test_full_lifecycle_is_healthy(self):
        result = analyze_device("DEVICE-OK", healthy_events())
        assert result.status == DeviceStatus.HEALTHY
        assert result.failures == []

    def test_event_count(self):
        result = analyze_device("DEVICE-OK", healthy_events())
        assert result.event_count == 4


# ---------------------------------------------------------------------------
# Failed events
# ---------------------------------------------------------------------------

class TestFailedEvents:
    def test_single_failed_event_detected(self):
        events = [make_event("SESSION_START", "FAILED")]
        result = analyze_device("DEVICE-X", events)
        assert result.status == DeviceStatus.FAILED
        assert any(f.failure_event == "SESSION_START" for f in result.failures)

    def test_error_status_detected(self):
        events = [make_event("DATA_TRANSFER", "ERROR")]
        result = analyze_device("DEVICE-X", events)
        assert result.status == DeviceStatus.FAILED

    def test_timeout_status_detected(self):
        events = [make_event("AUTHENTICATION", "TIMEOUT")]
        result = analyze_device("DEVICE-X", events)
        assert result.status == DeviceStatus.FAILED


# ---------------------------------------------------------------------------
# Repeated failures
# ---------------------------------------------------------------------------

class TestRepeatedFailures:
    def test_repeated_failure_same_event_detected(self):
        events = [
            make_event("SESSION_START", "FAILED", offset_seconds=0),
            make_event("RETRY",         "STARTED", offset_seconds=1),
            make_event("SESSION_START", "FAILED", offset_seconds=2),
            make_event("RETRY",         "STARTED", offset_seconds=3),
            make_event("SESSION_START", "FAILED", offset_seconds=4),
        ]
        result = analyze_device("DEVICE-X", events)
        assert result.status == DeviceStatus.FAILED
        repeated = [f for f in result.failures if f.failure_type == "REPEATED_FAILURE"]
        assert len(repeated) >= 1
        assert repeated[0].failure_event == "SESSION_START"

    def test_single_failure_not_repeated(self):
        events = [make_event("SESSION_START", "FAILED")]
        result = analyze_device("DEVICE-X", events)
        repeated = [f for f in result.failures if f.failure_type == "REPEATED_FAILURE"]
        assert repeated == []


# ---------------------------------------------------------------------------
# Excessive retries
# ---------------------------------------------------------------------------

class TestExcessiveRetries:
    def test_retries_above_threshold_flagged(self):
        events = [make_event("RETRY", "STARTED", offset_seconds=i) for i in range(RETRY_THRESHOLD + 1)]
        result = analyze_device("DEVICE-X", events)
        excessive = [f for f in result.failures if f.failure_type == "EXCESSIVE_RETRIES"]
        assert len(excessive) == 1
        assert result.retry_count == RETRY_THRESHOLD + 1

    def test_retries_below_threshold_not_flagged(self):
        events = [make_event("RETRY", "STARTED")]
        result = analyze_device("DEVICE-X", events)
        excessive = [f for f in result.failures if f.failure_type == "EXCESSIVE_RETRIES"]
        assert excessive == []


# ---------------------------------------------------------------------------
# Missing events
# ---------------------------------------------------------------------------

class TestMissingEvents:
    def test_missing_authentication_detected(self):
        # Device goes straight from CONNECTION_START to SESSION_START
        events = [
            make_event("CONNECTION_START", "SUCCESS", offset_seconds=0),
            make_event("SESSION_START",    "SUCCESS", offset_seconds=1),
            make_event("DATA_TRANSFER",    "SUCCESS", offset_seconds=2),
        ]
        result = analyze_device("DEVICE-X", events)
        missing_failures = [f for f in result.failures if f.failure_type == "MISSING_EVENTS"]
        # AUTHENTICATION is missing before DATA_TRANSFER
        assert len(missing_failures) >= 1
        assert "AUTHENTICATION" in missing_failures[0].missing_events


# ---------------------------------------------------------------------------
# Long delay
# ---------------------------------------------------------------------------

class TestLongDelay:
    def test_long_gap_detected(self):
        events = [
            make_event("CONNECTION_START", offset_seconds=0),
            make_event("AUTHENTICATION",   offset_seconds=DELAY_THRESHOLD_SECONDS + 10),
        ]
        result = analyze_device("DEVICE-X", events)
        delay_failures = [f for f in result.failures if f.failure_type == "LONG_DELAY"]
        assert len(delay_failures) == 1
        assert delay_failures[0].delay_seconds > DELAY_THRESHOLD_SECONDS

    def test_short_gap_not_flagged(self):
        events = [
            make_event("CONNECTION_START", offset_seconds=0),
            make_event("AUTHENTICATION",   offset_seconds=5),
        ]
        result = analyze_device("DEVICE-X", events)
        delay_failures = [f for f in result.failures if f.failure_type == "LONG_DELAY"]
        assert delay_failures == []


# ---------------------------------------------------------------------------
# Unexpected sequence
# ---------------------------------------------------------------------------

class TestUnexpectedSequence:
    def test_data_before_session_detected(self):
        events = [
            make_event("CONNECTION_START", "SUCCESS", offset_seconds=0),
            make_event("AUTHENTICATION",   "SUCCESS", offset_seconds=1),
            make_event("DATA_TRANSFER",    "SUCCESS", offset_seconds=2),
            make_event("SESSION_START",    "SUCCESS", offset_seconds=3),
        ]
        result = analyze_device("DEVICE-X", events)
        seq_failures = [f for f in result.failures if f.failure_type == "UNEXPECTED_SEQUENCE"]
        assert len(seq_failures) >= 1


# ---------------------------------------------------------------------------
# Empty events
# ---------------------------------------------------------------------------

class TestEmptyEvents:
    def test_empty_events_returns_unknown(self):
        result = analyze_device("DEVICE-EMPTY", [])
        assert result.status == DeviceStatus.UNKNOWN
        assert result.failures == []
        assert result.event_count == 0


# ---------------------------------------------------------------------------
# Multiple devices
# ---------------------------------------------------------------------------

class TestMultipleDevices:
    def test_analyze_all_devices(self):
        device_events = {
            "DEVICE-OK":  healthy_events("DEVICE-OK"),
            "DEVICE-BAD": [make_event("SESSION_START", "FAILED", "DEVICE-BAD")],
        }
        results = analyze_all_devices(device_events)
        assert results["DEVICE-OK"].status == DeviceStatus.HEALTHY
        assert results["DEVICE-BAD"].status == DeviceStatus.FAILED

    def test_multiple_devices_independent(self):
        device_events = {
            "DEVICE-A": healthy_events("DEVICE-A"),
            "DEVICE-B": healthy_events("DEVICE-B"),
            "DEVICE-C": healthy_events("DEVICE-C"),
        }
        results = analyze_all_devices(device_events)
        assert all(r.status == DeviceStatus.HEALTHY for r in results.values())
