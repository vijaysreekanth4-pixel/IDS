"""
analyzer.py
-----------
Event sequence analysis and failure detection engine.

Design
------
Groups events per device and builds a chronological timeline.
Applies a multi-layered detection strategy so failures are identified
through *pattern*, not merely by checking for the literal string "FAILED".

Detection layers
~~~~~~~~~~~~~~~~
1. **Status failures** – direct FAILED / ERROR / TIMEOUT status values.
2. **Retry analysis** – counts RETRY events; flags excessive retries (>= threshold).
3. **Repeated failures** – same event type fails multiple times consecutively.
4. **Missing events** – expected events in the normal lifecycle are absent.
5. **Unexpected ordering** – events appear outside their expected position
   in the standard flow.
6. **Time-gap detection** – abnormally long delays between consecutive events.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .models import DeviceStatus, Event, FailureDetail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / thresholds
# ---------------------------------------------------------------------------

# Number of seconds between consecutive events before it is flagged as a delay.
DELAY_THRESHOLD_SECONDS: float = 60.0

# Number of RETRY events that triggers an "excessive retries" failure.
RETRY_THRESHOLD: int = 2

# The canonical happy-path event sequence.  Order matters.
EXPECTED_FLOW: List[str] = [
    "CONNECTION_START",
    "AUTHENTICATION",
    "SESSION_START",
    "DATA_TRANSFER",
]

# Status values that indicate a problem.
FAILURE_STATUSES = {"FAILED", "ERROR", "TIMEOUT"}


# ---------------------------------------------------------------------------
# SequenceAnalysis result container
# ---------------------------------------------------------------------------

class SequenceAnalysis:
    """Holds the full analysis result for a single device."""

    def __init__(self, device_id: str, events: List[Event]) -> None:
        self.device_id = device_id
        self.events = events                        # sorted chronologically
        self.failures: List[FailureDetail] = []
        self.retry_count: int = 0
        self.status: DeviceStatus = DeviceStatus.HEALTHY

    # Convenience
    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def first_seen(self) -> Optional[datetime]:
        return self.events[0].timestamp if self.events else None

    @property
    def last_seen(self) -> Optional[datetime]:
        return self.events[-1].timestamp if self.events else None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_failure_status(status: str) -> bool:
    return status.upper() in FAILURE_STATUSES


def _detect_status_failures(events: List[Event]) -> List[FailureDetail]:
    """Layer 1 – flag every event whose status is a failure indicator."""
    failures: List[FailureDetail] = []
    for event in events:
        if _is_failure_status(event.status):
            failures.append(
                FailureDetail(
                    failure_type="STATUS_FAILURE",
                    failure_event=event.event_type,
                    failed_at=event.timestamp,
                )
            )
    return failures


def _detect_repeated_failures(events: List[Event]) -> List[FailureDetail]:
    """
    Layer 3 – identify event types that fail more than once.

    e.g. SESSION_START fails three times in a row → SESSION_ESTABLISHMENT failure.
    """
    failures: List[FailureDetail] = []

    # Tally failures per event_type
    fail_counts: Counter = Counter()
    first_fail_ts: Dict[str, datetime] = {}

    for event in events:
        if _is_failure_status(event.status):
            fail_counts[event.event_type] += 1
            if event.event_type not in first_fail_ts:
                first_fail_ts[event.event_type] = event.timestamp

    for event_type, count in fail_counts.items():
        if count > 1:
            failures.append(
                FailureDetail(
                    failure_type="REPEATED_FAILURE",
                    failure_event=event_type,
                    retry_count=count - 1,
                    failed_at=first_fail_ts.get(event_type),
                )
            )
    return failures


def _detect_excessive_retries(events: List[Event]) -> Tuple[int, List[FailureDetail]]:
    """
    Layer 2 – count RETRY events; return (retry_count, failures).

    A RETRY event itself is not inherently bad, but more than
    RETRY_THRESHOLD retries indicates a deeper problem.
    """
    retry_events = [e for e in events if e.event_type == "RETRY"]
    retry_count = len(retry_events)
    failures: List[FailureDetail] = []

    if retry_count >= RETRY_THRESHOLD:
        first_ts = retry_events[0].timestamp if retry_events else None
        failures.append(
            FailureDetail(
                failure_type="EXCESSIVE_RETRIES",
                failure_event="RETRY",
                retry_count=retry_count,
                failed_at=first_ts,
            )
        )
    return retry_count, failures


def _detect_missing_events(events: List[Event]) -> List[FailureDetail]:
    """
    Layer 4 – check whether the device progressed through the expected flow.

    Only events with a non-failure status are considered as "present" steps.
    """
    failures: List[FailureDetail] = []
    successful_types = {
        e.event_type for e in events if not _is_failure_status(e.status)
    }

    # Find the furthest point reached in the expected flow
    reached_index = -1
    for i, expected_event in enumerate(EXPECTED_FLOW):
        if expected_event in successful_types:
            reached_index = i

    if reached_index == -1:
        # No standard flow events at all – skip (might be a custom device type)
        return failures

    # Every step before the furthest one should be present
    missing = [
        EXPECTED_FLOW[i]
        for i in range(reached_index)
        if EXPECTED_FLOW[i] not in successful_types
    ]

    if missing:
        failures.append(
            FailureDetail(
                failure_type="MISSING_EVENTS",
                failure_event=EXPECTED_FLOW[reached_index],
                missing_events=missing,
            )
        )
    return failures


def _detect_unexpected_ordering(events: List[Event]) -> List[FailureDetail]:
    """
    Layer 5 – detect when events appear in a position that violates the
    expected flow order.

    Example: DATA_TRANSFER appearing before SESSION_START.
    """
    failures: List[FailureDetail] = []

    flow_positions = {evt: idx for idx, evt in enumerate(EXPECTED_FLOW)}

    last_flow_position = -1
    for event in events:
        if event.event_type in flow_positions and not _is_failure_status(event.status):
            pos = flow_positions[event.event_type]
            if pos < last_flow_position:
                failures.append(
                    FailureDetail(
                        failure_type="UNEXPECTED_SEQUENCE",
                        failure_event=event.event_type,
                        unexpected_sequence=(
                            f"{event.event_type} appeared after position "
                            f"{EXPECTED_FLOW[last_flow_position]} "
                            f"(expected flow violation)"
                        ),
                        failed_at=event.timestamp,
                    )
                )
            else:
                last_flow_position = pos
    return failures


def _detect_time_gaps(events: List[Event]) -> List[FailureDetail]:
    """
    Layer 6 – detect suspiciously long gaps between consecutive events.

    A gap larger than DELAY_THRESHOLD_SECONDS is reported as a potential
    hanging/stalled operation.
    """
    failures: List[FailureDetail] = []

    for i in range(1, len(events)):
        delta = (events[i].timestamp - events[i - 1].timestamp).total_seconds()
        if delta > DELAY_THRESHOLD_SECONDS:
            failures.append(
                FailureDetail(
                    failure_type="LONG_DELAY",
                    failure_event=events[i].event_type,
                    delay_seconds=delta,
                    failed_at=events[i].timestamp,
                )
            )
    return failures


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_device(device_id: str, events: List[Event]) -> SequenceAnalysis:
    """
    Run all detection layers for a single device and return a
    :class:`SequenceAnalysis` with every detected failure.
    """
    result = SequenceAnalysis(device_id=device_id, events=events)

    if not events:
        result.status = DeviceStatus.UNKNOWN
        return result

    all_failures: List[FailureDetail] = []

    # --- Layer 1: direct status failures ---
    all_failures.extend(_detect_status_failures(events))

    # --- Layer 2: excessive retries ---
    retry_count, retry_failures = _detect_excessive_retries(events)
    result.retry_count = retry_count
    all_failures.extend(retry_failures)

    # --- Layer 3: repeated failures per event type ---
    all_failures.extend(_detect_repeated_failures(events))

    # --- Layer 4: missing expected events ---
    all_failures.extend(_detect_missing_events(events))

    # --- Layer 5: unexpected ordering ---
    all_failures.extend(_detect_unexpected_ordering(events))

    # --- Layer 6: long delays ---
    all_failures.extend(_detect_time_gaps(events))

    # De-duplicate failures (same type + event) before storing
    seen_failure_keys: set = set()
    unique_failures: List[FailureDetail] = []
    for f in all_failures:
        key = (f.failure_type, f.failure_event)
        if key not in seen_failure_keys:
            seen_failure_keys.add(key)
            unique_failures.append(f)

    result.failures = unique_failures

    # Determine overall device status
    if any(
        f.failure_type in (
            "STATUS_FAILURE", "REPEATED_FAILURE", "EXCESSIVE_RETRIES",
        )
        for f in unique_failures
    ):
        result.status = DeviceStatus.FAILED
    elif any(
        f.failure_type in ("MISSING_EVENTS", "LONG_DELAY", "UNEXPECTED_SEQUENCE")
        for f in unique_failures
    ):
        result.status = DeviceStatus.DEGRADED
    else:
        result.status = DeviceStatus.HEALTHY

    return result


def analyze_all_devices(
    device_events: Dict[str, List[Event]],
) -> Dict[str, SequenceAnalysis]:
    """
    Run :func:`analyze_device` for every device in the dataset.

    Returns a mapping of device_id → :class:`SequenceAnalysis`.
    """
    results: Dict[str, SequenceAnalysis] = {}
    for device_id, events in device_events.items():
        logger.debug("Analysing device %s (%d events)", device_id, len(events))
        results[device_id] = analyze_device(device_id, events)
    return results
