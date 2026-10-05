"""
root_cause.py
-------------
Rule-based root cause analysis engine with confidence scoring.

Approach
--------
A **pattern library** maps failure signatures to human-readable root causes
and base confidence scores.  For each device the engine:

1. Collects evidence from the :class:`SequenceAnalysis` result.
2. Iterates the pattern library and scores each candidate pattern.
3. Returns the best-scoring pattern whose threshold is met.

The confidence score is calculated as:

    confidence = base_confidence
               + evidence_boost   (0.05 per additional supporting piece)
               − retry_penalty    (small deduction for very high retries)

The design is intentionally pluggable:  the pattern library is a plain list
of dicts, so patterns can be loaded from a YAML/JSON file or replaced by an
ML classifier in the future.

Extending to ML
---------------
Replace ``_score_pattern`` with a call to a scikit-learn / PyTorch model that
accepts a feature vector derived from the same evidence fields.  The rest of
the pipeline (report generation, API) remains unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .analyzer import SequenceAnalysis
from .models import DeviceStatus, RootCauseResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern library
# ---------------------------------------------------------------------------
# Each pattern is a dict with:
#   name          – short identifier
#   failure_type  – maps to FailureDetail.failure_type values that must be
#                   present to activate this pattern
#   failure_event – the specific event type to check (None = any)
#   base_confidence – starting confidence when all conditions are met
#   description   – human-readable probable root cause text

PATTERNS: List[Dict[str, Any]] = [
    {
        "name": "SESSION_ESTABLISHMENT",
        "required_failure_types": {"STATUS_FAILURE", "REPEATED_FAILURE"},
        "failure_event": "SESSION_START",
        "base_confidence": 0.85,
        "description": (
            "Session establishment repeatedly failed after successful authentication. "
            "Likely cause: misconfigured session parameters or a resource exhaustion "
            "on the server side."
        ),
    },
    {
        "name": "AUTHENTICATION_FAILURE",
        "required_failure_types": {"STATUS_FAILURE"},
        "failure_event": "AUTHENTICATION",
        "base_confidence": 0.90,
        "description": (
            "Authentication step failed. "
            "Likely cause: invalid credentials, expired certificates, or "
            "an unreachable identity provider."
        ),
    },
    {
        "name": "CONNECTION_FAILURE",
        "required_failure_types": {"STATUS_FAILURE"},
        "failure_event": "CONNECTION_START",
        "base_confidence": 0.88,
        "description": (
            "Device failed to establish a network connection. "
            "Likely cause: network unreachable, firewall rule, or "
            "target host is down."
        ),
    },
    {
        "name": "DATA_TRANSFER_FAILURE",
        "required_failure_types": {"STATUS_FAILURE"},
        "failure_event": "DATA_TRANSFER",
        "base_confidence": 0.80,
        "description": (
            "Data transfer failed after a session was established. "
            "Likely cause: connection dropped mid-stream, payload corruption, "
            "or a timeout enforced by the remote endpoint."
        ),
    },
    {
        "name": "EXCESSIVE_RETRIES",
        "required_failure_types": {"EXCESSIVE_RETRIES"},
        "failure_event": None,                      # any event
        "base_confidence": 0.75,
        "description": (
            "The device performed an abnormally high number of retries. "
            "Likely cause: transient network instability, intermittent service "
            "unavailability, or a retry-storm triggered by a bug."
        ),
    },
    {
        "name": "MISSING_EVENTS",
        "required_failure_types": {"MISSING_EVENTS"},
        "failure_event": None,
        "base_confidence": 0.65,
        "description": (
            "Expected lifecycle events are absent from the log. "
            "Likely cause: the device crashed before completing those steps, "
            "or events were not emitted due to an early exit/exception."
        ),
    },
    {
        "name": "LONG_DELAY",
        "required_failure_types": {"LONG_DELAY"},
        "failure_event": None,
        "base_confidence": 0.60,
        "description": (
            "Abnormally long delay detected between consecutive events. "
            "Likely cause: the device is stalled waiting for an external "
            "dependency (database, network, lock) that is slow or unresponsive."
        ),
    },
    {
        "name": "UNEXPECTED_SEQUENCE",
        "required_failure_types": {"UNEXPECTED_SEQUENCE"},
        "failure_event": None,
        "base_confidence": 0.55,
        "description": (
            "Events arrived in an unexpected order. "
            "Likely cause: a race condition, out-of-order message delivery, "
            "or a bug causing the device to skip or repeat lifecycle steps."
        ),
    },
]


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------

def _score_pattern(
    pattern: Dict[str, Any],
    failure_types_present: set,
    failure_events_present: set,
    retry_count: int,
) -> Optional[float]:
    """
    Return a confidence score for ``pattern`` given the evidence, or ``None``
    if the pattern does not match.
    """
    required = pattern["required_failure_types"]

    # Pattern must have at least one required failure type present
    if not required.intersection(failure_types_present):
        return None

    # If a specific failure_event is required, check it
    if pattern["failure_event"] is not None:
        if pattern["failure_event"] not in failure_events_present:
            return None

    score: float = pattern["base_confidence"]

    # Boost for each additional required type that is also present
    extra = len(required.intersection(failure_types_present)) - 1
    score += extra * 0.05

    # Small penalty for very high retry counts (suggests complexity beyond
    # the single pattern)
    if retry_count > 5:
        score -= 0.05

    # Clamp to [0, 1]
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def determine_root_cause(analysis: SequenceAnalysis) -> RootCauseResult:
    """
    Run the pattern engine against a :class:`SequenceAnalysis` and return
    the best-matching :class:`RootCauseResult`.
    """
    if analysis.status == DeviceStatus.HEALTHY:
        return RootCauseResult(
            device_id=analysis.device_id,
            status=DeviceStatus.HEALTHY,
            confidence=1.0,
        )

    # Collect evidence sets
    failure_types_present: set = {f.failure_type for f in analysis.failures}
    failure_events_present: set = {f.failure_event for f in analysis.failures}
    retry_count = analysis.retry_count

    # Build evidence strings for the report
    evidence: List[str] = []
    for f in analysis.failures:
        if f.failure_type == "STATUS_FAILURE":
            evidence.append(
                f"Event '{f.failure_event}' reported a failure status"
                + (f" at {f.failed_at}" if f.failed_at else "")
            )
        elif f.failure_type == "REPEATED_FAILURE":
            evidence.append(
                f"Event '{f.failure_event}' failed {f.retry_count + 1} times"
            )
        elif f.failure_type == "EXCESSIVE_RETRIES":
            evidence.append(f"RETRY event appeared {f.retry_count} times (threshold: 2)")
        elif f.failure_type == "MISSING_EVENTS":
            evidence.append(f"Missing expected events: {f.missing_events}")
        elif f.failure_type == "LONG_DELAY":
            evidence.append(
                f"Long delay of {f.delay_seconds:.1f}s before '{f.failure_event}'"
            )
        elif f.failure_type == "UNEXPECTED_SEQUENCE":
            evidence.append(f"Sequence violation: {f.unexpected_sequence}")

    # Score all patterns
    best_pattern: Optional[Dict[str, Any]] = None
    best_score: float = -1.0

    for pattern in PATTERNS:
        score = _score_pattern(
            pattern,
            failure_types_present,
            failure_events_present,
            retry_count,
        )
        if score is not None and score > best_score:
            best_score = score
            best_pattern = pattern

    if best_pattern is None or best_score < 0.0:
        # No pattern matched – generic unknown failure
        return RootCauseResult(
            device_id=analysis.device_id,
            status=analysis.status,
            failure_type="UNKNOWN",
            failure_event=None,
            retry_count=retry_count,
            probable_root_cause="Unable to determine a specific root cause from available events.",
            confidence=0.30,
            supporting_evidence=evidence,
        )

    # Determine which failure event is primary (the one that matched the pattern)
    primary_event = best_pattern["failure_event"]
    if primary_event is None and analysis.failures:
        primary_event = analysis.failures[0].failure_event

    return RootCauseResult(
        device_id=analysis.device_id,
        status=analysis.status,
        failure_type=best_pattern["name"],
        failure_event=primary_event,
        retry_count=retry_count,
        probable_root_cause=best_pattern["description"],
        confidence=round(best_score, 2),
        supporting_evidence=evidence,
    )


def analyze_root_causes(
    analyses: Dict[str, SequenceAnalysis],
) -> Dict[str, RootCauseResult]:
    """Run root cause analysis for every device."""
    return {
        device_id: determine_root_cause(analysis)
        for device_id, analysis in analyses.items()
    }
