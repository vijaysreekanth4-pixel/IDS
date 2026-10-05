"""
test_root_cause.py
------------------
Unit tests for src/root_cause.py and src/recommender.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.analyzer import SequenceAnalysis
from src.models import DeviceStatus, Event, FailureDetail
from src.recommender import generate_recommendation
from src.root_cause import determine_root_cause


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(event_type: str, status: str = "SUCCESS", offset: int = 0) -> Event:
    base = datetime(2026, 9, 1, 10, 0, 0) + timedelta(seconds=offset)
    return Event(timestamp=base, device_id="DEVICE-T", event_type=event_type, status=status)


def build_analysis(
    device_id: str,
    events: list,
    failures: list,
    retry_count: int = 0,
    status: DeviceStatus = DeviceStatus.FAILED,
) -> SequenceAnalysis:
    analysis = SequenceAnalysis(device_id=device_id, events=events)
    analysis.failures = failures
    analysis.retry_count = retry_count
    analysis.status = status
    return analysis


# ---------------------------------------------------------------------------
# Healthy device
# ---------------------------------------------------------------------------

class TestHealthyRootCause:
    def test_healthy_device_no_root_cause(self):
        events = [make_event("CONNECTION_START", "SUCCESS")]
        analysis = build_analysis("DEVICE-OK", events, [], status=DeviceStatus.HEALTHY)
        result = determine_root_cause(analysis)
        assert result.status == DeviceStatus.HEALTHY
        assert result.failure_type is None
        assert result.confidence == 1.0

    def test_healthy_recommendation(self):
        events = [make_event("CONNECTION_START", "SUCCESS")]
        analysis = build_analysis("DEVICE-OK", events, [], status=DeviceStatus.HEALTHY)
        rca = determine_root_cause(analysis)
        rec = generate_recommendation(rca)
        assert "no action required" in rec.recommendation[0].lower()


# ---------------------------------------------------------------------------
# Session establishment failure
# ---------------------------------------------------------------------------

class TestSessionFailure:
    def _make_analysis(self):
        events = [
            make_event("CONNECTION_START", "SUCCESS", 0),
            make_event("AUTHENTICATION",   "SUCCESS", 1),
            make_event("SESSION_START",    "FAILED",  2),
            make_event("RETRY",            "STARTED", 3),
            make_event("SESSION_START",    "FAILED",  4),
        ]
        failures = [
            FailureDetail(failure_type="STATUS_FAILURE",    failure_event="SESSION_START"),
            FailureDetail(failure_type="REPEATED_FAILURE",  failure_event="SESSION_START", retry_count=1),
        ]
        return build_analysis("DEVICE-02", events, failures, retry_count=1)

    def test_session_failure_detected(self):
        result = determine_root_cause(self._make_analysis())
        assert result.failure_type == "SESSION_ESTABLISHMENT"

    def test_session_failure_confidence_high(self):
        result = determine_root_cause(self._make_analysis())
        assert result.confidence >= 0.80

    def test_session_failure_has_evidence(self):
        result = determine_root_cause(self._make_analysis())
        assert len(result.supporting_evidence) >= 1

    def test_session_failure_recommendation(self):
        rca = determine_root_cause(self._make_analysis())
        rec = generate_recommendation(rca)
        assert len(rec.recommendation) >= 1
        assert any("session" in r.lower() for r in rec.recommendation)


# ---------------------------------------------------------------------------
# Authentication failure
# ---------------------------------------------------------------------------

class TestAuthFailure:
    def _make_analysis(self):
        events = [
            make_event("CONNECTION_START", "SUCCESS", 0),
            make_event("AUTHENTICATION",   "FAILED",  1),
        ]
        failures = [
            FailureDetail(failure_type="STATUS_FAILURE", failure_event="AUTHENTICATION"),
        ]
        return build_analysis("DEVICE-03", events, failures)

    def test_auth_failure_detected(self):
        result = determine_root_cause(self._make_analysis())
        assert result.failure_type == "AUTHENTICATION_FAILURE"

    def test_auth_failure_confidence(self):
        result = determine_root_cause(self._make_analysis())
        assert result.confidence >= 0.85


# ---------------------------------------------------------------------------
# Connection failure
# ---------------------------------------------------------------------------

class TestConnectionFailure:
    def _make_analysis(self):
        events = [make_event("CONNECTION_START", "FAILED", 0)]
        failures = [
            FailureDetail(failure_type="STATUS_FAILURE", failure_event="CONNECTION_START"),
        ]
        return build_analysis("DEVICE-05", events, failures)

    def test_connection_failure_detected(self):
        result = determine_root_cause(self._make_analysis())
        assert result.failure_type == "CONNECTION_FAILURE"


# ---------------------------------------------------------------------------
# Excessive retries
# ---------------------------------------------------------------------------

class TestExcessiveRetriesRCA:
    def _make_analysis(self):
        events = [make_event("RETRY", "STARTED", i) for i in range(5)]
        failures = [
            FailureDetail(failure_type="EXCESSIVE_RETRIES", failure_event="RETRY", retry_count=5),
        ]
        return build_analysis("DEVICE-X", events, failures, retry_count=5)

    def test_excessive_retry_rca(self):
        result = determine_root_cause(self._make_analysis())
        assert result.failure_type == "EXCESSIVE_RETRIES"

    def test_very_high_retries_confidence_capped(self):
        events = [make_event("RETRY", "STARTED", i) for i in range(10)]
        failures = [
            FailureDetail(failure_type="EXCESSIVE_RETRIES", failure_event="RETRY", retry_count=10),
        ]
        analysis = build_analysis("DEVICE-X", events, failures, retry_count=10)
        result = determine_root_cause(analysis)
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# Unknown failure
# ---------------------------------------------------------------------------

class TestUnknownRCA:
    def test_no_pattern_match_returns_unknown_type(self):
        # Give it an empty failure list but mark as failed
        analysis = build_analysis("DEVICE-X", [], [], status=DeviceStatus.FAILED)
        result = determine_root_cause(analysis)
        # Should fall through to the generic unknown
        assert result.failure_type == "UNKNOWN"
        assert result.confidence > 0.0
